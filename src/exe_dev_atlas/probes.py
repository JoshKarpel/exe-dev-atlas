from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Final

import h11
from without_asgi import headers
from without_async import cancel_futures
from without_async import timeout
from without_http import Client
from without_http import ResponseBody
from without_http import request

from exe_dev_atlas.listeners import Listener

# A server can hold its port down before it answers on it, which is exactly the moment this
# page is being watched. A port that did not respond is re-probed on this cadence until it
# does, or until the attempts run out.
PROBE_RETRY: Final = timedelta(seconds=5)
PROBE_MAX_ATTEMPTS: Final = 6
PROBE_TIMEOUT: Final = timedelta(seconds=1.5)

# Enough of a page to carry a `<title>`, and the point the read stops at, which is what makes
# it a bound on what a hostile or broken listener can make this process hold.
PROBE_MAX_BYTES: Final = 65_536

TITLE_PATTERN: Final = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class Probe:
    """What the port said when asked for a page."""

    is_http: bool
    status: int | None
    title: str
    server: str
    attempts: int
    at: float


def format_probe_title(body: str) -> str:
    match = TITLE_PATTERN.search(body)
    if not match:
        return ""
    return html.unescape(" ".join(match.group(1).split()))


def _text(value: bytes | None) -> str:
    """A header value as text, or "" where the field was absent."""
    return "" if value is None else value.decode("latin-1")


def describe_response(status: int, content_type: str, server: str, body: bytes, at: float) -> Probe:
    title = ""
    if "html" in content_type.lower():
        title = format_probe_title(body.decode("utf-8", "replace"))
    return Probe(is_http=True, status=status, title=title, server=server, attempts=1, at=at)


async def read_beginning(body: ResponseBody, limit: int) -> bytes:
    """
    The first `limit` bytes of a response body, read no further than that.

    Stopping the read is the point, rather than reading the body and slicing what came back.
    A `/` that streams (a log tailer, an event stream, a video feed) never ends, so draining
    it costs the whole probe timeout and holds everything it sent in the meantime, and the
    probe then reports a web server as not answering HTTP because the head it already parsed
    was thrown away with the timeout.
    """
    held: list[bytes] = []
    size = 0
    async for chunk in body:
        held.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(held)[:limit]


async def probe_port(client: Client, port: int) -> Probe:
    """
    Ask a port for a page, to tell a web server from a database socket.

    A 404 or a 401 is still a web server and still worth a link, so any answer at all
    counts as HTTP; only a connection that fails or never answers does not.

    A listener that answers with something that is not HTTP is the case this exists for, and
    it arrives as h11's own `RemoteProtocolError`: Postgres and Redis each answer a `GET /`
    in their own protocol, and a socket that accepts and hangs up says nothing at all. All of
    them mean "not a web server", which is the answer this returns rather than an exception
    out of a listing.
    """
    now = time.time()
    try:
        async with (
            timeout(PROBE_TIMEOUT),
            request(
                client,
                "GET",
                f"http://127.0.0.1:{port}/",
                headers=((b"user-agent", b"exe-dev-atlas"), (b"accept", b"text/html")),
            ) as (head, body),
        ):
            return describe_response(
                head.status,
                _text(headers.first(head.headers, b"content-type")),
                _text(headers.first(head.headers, b"server")),
                await read_beginning(body, PROBE_MAX_BYTES),
                now,
            )
    except OSError, TimeoutError, ValueError, h11.RemoteProtocolError:
        return Probe(is_http=False, status=None, title="", server="", attempts=1, at=now)


class Probes:
    """
    Probe results, keyed so a restarted process is asked again.

    Probing happens off the scan loop: a port that accepts a connection and then says
    nothing would otherwise stall every other row behind its timeout. A probe finishing does
    not push on its own either; it lands here and the next scan carries it, which costs a
    title up to a second and saves a probe from re-entering the scan.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._results: dict[tuple[int, int | None], Probe] = {}
        # The probe running for each key, which is both the "already asked" ledger and the
        # strong reference that keeps it alive: asyncio holds only a weak one and will
        # otherwise collect a task mid-probe.
        self._probing: dict[tuple[int, int | None], asyncio.Task[None]] = {}

    def get(self, listener: Listener) -> Probe | None:
        return self._results.get((listener.port, listener.pid))

    def refresh(self, listeners: list[Listener]) -> None:
        live = {(listener.port, listener.pid) for listener in listeners}
        for key in list(self._results):
            if key not in live:
                del self._results[key]
        for listener in listeners:
            key = (listener.port, listener.pid)
            if not self._is_due(key):
                continue
            task = asyncio.create_task(self._run(listener))
            self._probing[key] = task
            task.add_done_callback(partial(self._forget, key))

    async def aclose(self) -> None:
        """
        Cancel every probe still in flight, so nothing outlives the scan that started it.

        Filtered to what is still running, as `cancel_futures` asks: a finished task holding
        an exception propagates it out of the await loop, and the probes queued behind it are
        then cancelled but never awaited. `_probing` can hold one, because the entry is
        dropped from a done callback that runs a turn after the task itself finished.
        """
        await cancel_futures(task for task in self._probing.values() if not task.done())

    def _forget(self, key: tuple[int, int | None], _finished: asyncio.Task[None]) -> None:
        self._probing.pop(key, None)

    def _is_due(self, key: tuple[int, int | None]) -> bool:
        if key in self._probing:
            return False
        previous = self._results.get(key)
        if previous is None:
            return True
        if previous.is_http:
            return False
        return previous.attempts < PROBE_MAX_ATTEMPTS and time.time() - previous.at >= PROBE_RETRY.total_seconds()

    async def _run(self, listener: Listener) -> None:
        key = (listener.port, listener.pid)
        probe = await probe_port(self._client, listener.port)
        previous = self._results.get(key)
        if previous is not None and not probe.is_http:
            probe = replace(probe, attempts=previous.attempts + 1)
        self._results[key] = probe
