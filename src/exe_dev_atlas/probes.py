from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from without_http import Client
from without_http import request

from exe_dev_atlas.listeners import Listener

# A server can hold its port down before it answers on it, which is exactly the moment this
# page is being watched. A port that did not respond is re-probed on this cadence until it
# does, or until the attempts run out.
PROBE_RETRY: Final = timedelta(seconds=5)
PROBE_MAX_ATTEMPTS: Final = 6
PROBE_TIMEOUT: Final = timedelta(seconds=1.5)

# Enough of a page to carry a `<title>`, and a bound on what a hostile or broken listener
# can make this process hold.
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


def describe_response(status: int, content_type: str, server: str, body: bytes, at: float) -> Probe:
    title = ""
    if "html" in content_type.lower():
        title = format_probe_title(body.decode("utf-8", "replace"))
    return Probe(is_http=True, status=status, title=title, server=server, attempts=1, at=at)


def _header(headers: object, name: bytes) -> str:
    """One header value out of the raw pairs, case-insensitively, or ""."""
    if not isinstance(headers, tuple | list):
        return ""
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


async def probe_port(client: Client, port: int) -> Probe:
    """
    Ask a port for a page, to tell a web server from a database socket.

    A 404 or a 401 is still a web server and still worth a link, so any answer at all
    counts as HTTP; only a connection that fails or never answers does not.
    """
    now = time.time()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT.total_seconds()):
            async with request(
                client,
                "GET",
                f"http://127.0.0.1:{port}/",
                headers=((b"user-agent", b"exe-dev-atlas"), (b"accept", b"text/html")),
            ) as (head, body):
                return describe_response(
                    head.status,
                    _header(head.headers, b"content-type"),
                    _header(head.headers, b"server"),
                    (await body.read())[:PROBE_MAX_BYTES],
                    now,
                )
    except OSError, TimeoutError, ValueError:
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
        self._in_flight: set[tuple[int, int | None]] = set()
        self._tasks: set[asyncio.Task[None]] = set()

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
            self._in_flight.add(key)
            # Held in a set until done, because asyncio keeps only a weak reference to a
            # running task and will otherwise collect one mid-probe.
            task = asyncio.create_task(self._run(listener))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        """Cancel every probe still in flight, so nothing outlives the scan that started it."""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:  # noqa: SIM105
                await task
            except asyncio.CancelledError:
                pass

    def _is_due(self, key: tuple[int, int | None]) -> bool:
        if key in self._in_flight:
            return False
        previous = self._results.get(key)
        if previous is None:
            return True
        if previous.is_http:
            return False
        return previous.attempts < PROBE_MAX_ATTEMPTS and time.time() - previous.at >= PROBE_RETRY.total_seconds()

    async def _run(self, listener: Listener) -> None:
        key = (listener.port, listener.pid)
        try:
            probe = await probe_port(self._client, listener.port)
        finally:
            self._in_flight.discard(key)
        previous = self._results.get(key)
        if previous is not None and not probe.is_http:
            probe = Probe(
                is_http=False,
                status=None,
                title="",
                server="",
                attempts=previous.attempts + 1,
                at=probe.at,
            )
        self._results[key] = probe
