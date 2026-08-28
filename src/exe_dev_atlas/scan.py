from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import psutil
from without_http import Client

from exe_dev_atlas import zellij
from exe_dev_atlas.identity import Identity
from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.listeners import Process
from exe_dev_atlas.probes import Probe
from exe_dev_atlas.probes import Probes

SCAN_INTERVAL: Final = timedelta(seconds=1)

# Injected rather than imported, so a test drives a scan over a machine it wrote: a listing of
# its own, and processes behind it that no test could arrange for real. What a row says about
# a zellij web server in particular is decided from these two answers alone.
type ReadListeners = Callable[[], list[Listener]]
type ReadProcess = Callable[[int | None], Process]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Row:
    """Everything known about one listening port, ready to render."""

    port: int
    addresses: tuple[str, ...]
    pid: int | None
    command_name: str
    command_line: str
    directory: str
    user: str
    started_at: int | None
    is_http: bool | None
    status: int | None
    title: str
    server: str

    def as_dict(self) -> dict[str, object]:
        """
        The row as the browser receives it, with every field that crosses named here.

        Written out rather than reached for with `asdict`, which recurses and deep-copies
        every value on the way. Naming the fields is also what makes the omission of
        `Process.executable` visible at the point it is decided, rather than a property of
        which dataclass happened to be passed in.
        """
        return {
            "port": self.port,
            "addresses": self.addresses,
            "pid": self.pid,
            "command_name": self.command_name,
            "command_line": self.command_line,
            "directory": self.directory,
            "user": self.user,
            "started_at": self.started_at,
            "is_http": self.is_http,
            "status": self.status,
            "title": self.title,
            "server": self.server,
        }


def build_row(listener: Listener, process: Process, probe: Probe | None) -> Row:
    """
    One listener, its process, and whatever the probe found, as the value the page renders.

    `Process` deliberately does not survive into the `Row`: the row is serialized straight
    to every connected browser, and the executable path the scan needs for zellij is not
    something to hand out.
    """
    return Row(
        port=listener.port,
        addresses=listener.addresses,
        pid=listener.pid,
        command_name=process.command_name,
        command_line=process.command_line,
        directory=process.directory,
        user=process.user,
        started_at=process.started_at,
        is_http=None if probe is None else probe.is_http,
        status=None if probe is None else probe.status,
        title="" if probe is None else probe.title,
        server="" if probe is None else probe.server,
    )


class Broadcast:
    """
    The latest payload, and a way to wait for the next different one.

    Serialized here rather than in the handler, so the cost is one per scan however many
    connections are held, and every connection is served the same string.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._version = 0
        self._payload = "{}"

    async def publish(self, payload: str) -> None:
        async with self._condition:
            if payload == self._payload:
                return
            self._payload = payload
            self._version += 1
            self._condition.notify_all()

    async def wait(self, seen: int) -> tuple[int, str]:
        """
        Block until the payload differs from `seen`, then return it and its version.

        A caller that has seen nothing passes a version no payload can have, so it receives
        whatever is current without waiting for the next scan to find news.
        """
        async with self._condition:
            await self._condition.wait_for(lambda: self._version != seen)
            return self._version, self._payload


async def scan_once(
    broadcast: Broadcast,
    probes: Probes,
    read_listeners: ReadListeners,
    read_process: ReadProcess,
    own_port: int,
    identity: Identity,
) -> None:
    """Read the machine once and publish the payload, if it says anything new."""
    listeners = read_listeners()
    probes.refresh(listeners)

    # One read per listener, held long enough to serve both the row and the zellij lookup
    # below, which needs the executable path a row must not carry.
    scanned = [(listener, read_process(listener.pid)) for listener in listeners]
    rows = [build_row(listener, process, probes.get(listener)) for listener, process in scanned]

    # Keyed by position rather than by port, because two processes can hold one port number
    # between them, and gathered rather than awaited in turn: one zellij server that stopped
    # answering would otherwise hold every other row behind its own timeout, which is the
    # same stall `Probes` exists to keep off this loop.
    servers = {
        index: (listener, process)
        for index, (listener, process) in enumerate(scanned)
        if zellij.is_zellij_web(process.command_name, process.command_line)
    }
    listed = await asyncio.gather(
        *(zellij.read_sessions(listener.pid, process.executable) for listener, process in servers.values())
    )
    sessions = dict(zip(servers, listed, strict=True))

    # The session list is what marks a row as a session server, so a server serving nothing
    # carries an empty one rather than being indistinguishable from any other port: the page
    # decides on the field's presence, and that empty list is exactly the row the new-session
    # link exists for.
    listing = [
        row.as_dict() | {"sessions": list(sessions[index])} if index in sessions else row.as_dict()
        for index, row in enumerate(rows)
    ]

    # Read here rather than passed in, because the refresh loop can have replaced them since
    # the last scan: a renamed VM reaches every open page on the next payload. Nothing awaits
    # between these three, and nothing but `Identity.update` writes them, so they are always
    # the same answer rather than two halves of consecutive ones.
    await broadcast.publish(
        json.dumps(
            {
                "own_port": own_port,
                "vm_name": identity.vm.name,
                "vm_emoji": identity.vm.emoji,
                "vscode_url": identity.vscode_url,
                "rows": listing,
            },
            sort_keys=True,
        )
    )


async def scan_forever(
    broadcast: Broadcast,
    client: Client,
    read_listeners: ReadListeners,
    read_process: ReadProcess,
    own_port: int,
    identity: Identity,
) -> None:
    """
    Rescan on a fixed cadence, for as long as the server this is bound to runs.

    A scan that could not read the machine is logged and skipped rather than ending the loop.
    A refused or vanished `/proc` entry is a transient thing and the loop is already the
    retry, while a scan task that dies takes nothing visible with it: the page holds the last
    payload it was sent, the heartbeat keeps its connection open, and it reads "live" over a
    listing that stopped moving. An unexpected failure still ends the scan, but says so on the
    way out, because `background_task` surfaces it only when the server itself shuts down.
    """
    probes = Probes(client)
    try:
        while True:
            try:
                await scan_once(broadcast, probes, read_listeners, read_process, own_port, identity)
            except psutil.Error as unreadable:
                logger.warning(f"This scan read nothing and the last listing stands: {unreadable!r}")
            except Exception:
                logger.exception("The scan loop is stopping, so the listing will not change again")
                raise
            await asyncio.sleep(SCAN_INTERVAL.total_seconds())
    finally:
        await probes.aclose()
