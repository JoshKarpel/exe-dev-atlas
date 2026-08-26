from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from without_http import Client

from exe_dev_atlas import zellij
from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.listeners import NoSocketStatistics
from exe_dev_atlas.listeners import Process
from exe_dev_atlas.listeners import read_listeners
from exe_dev_atlas.listeners import read_process
from exe_dev_atlas.probes import Probe
from exe_dev_atlas.probes import Probes
from exe_dev_atlas.reflection import Vm

SCAN_INTERVAL: Final = timedelta(seconds=1)

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

    Two payloads rather than one, serialized here so the cost is per scan and not per
    connection. They differ only in whether zellij session names are present, and which one
    a connection receives is an authorization decision made in the handler that holds the
    caller's headers.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._version = 0
        self._public = "{}"
        self._owner = "{}"

    async def publish(self, public: str, owner: str) -> None:
        async with self._condition:
            if (public, owner) == (self._public, self._owner):
                return
            self._public = public
            self._owner = owner
            self._version += 1
            self._condition.notify_all()

    async def wait(self, seen: int, *, is_owner: bool) -> tuple[int, str]:
        """
        Block until the payload differs from `seen`, then return it and its version.

        A caller that has seen nothing passes a version no payload can have, so it receives
        whatever is current without waiting for the next scan to find news.
        """
        async with self._condition:
            await self._condition.wait_for(lambda: self._version != seen)
            return self._version, (self._owner if is_owner else self._public)


async def scan_once(
    broadcast: Broadcast,
    probes: Probes,
    socket_statistics: str,
    own_port: int,
    vm: Vm,
    vscode_url: str,
) -> None:
    """
    Read the machine once and publish the pair of payloads, if they say anything new.

    Both payloads are built every scan even when nobody is connected as an owner, because
    `publish` diffs against the last pair to decide whether there is news, and a pair built
    only sometimes would report a change every time the other half appeared.
    """
    listeners = await read_listeners(socket_statistics)
    probes.refresh(listeners)

    # One `/proc` read per listener, held long enough to serve both the row and the zellij
    # lookup below, which needs the executable path a row must not carry.
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

    # Flagged for everyone, unlike the session names: it withholds a link rather than
    # disclosing anything, and the command line already says `zellij web` to anyone reading
    # the row.
    public = [
        row.as_dict() | {"is_session_server": True} if index in sessions else row.as_dict()
        for index, row in enumerate(rows)
    ]
    owner = [
        row_dict | {"sessions": list(sessions[index])} if index in sessions else row_dict
        for index, row_dict in enumerate(public)
    ]

    # The VS Code link is owner-only: it only works for someone with SSH access anyway, and
    # ideally this would follow the VM's sharing grants, but reflection does not publish
    # them, so ownership is the only distinction available.
    #
    # Name and emoji are not owner-only: the name is already in the URL of whoever is
    # reading, and together they title and badge the tab for everyone.
    common: dict[str, object] = {"own_port": own_port, "vm_name": vm.name, "vm_emoji": vm.emoji}
    await broadcast.publish(
        json.dumps(common | {"rows": public}, sort_keys=True),
        json.dumps(common | {"rows": owner, "vscode_url": vscode_url}, sort_keys=True),
    )


async def scan_forever(
    broadcast: Broadcast,
    client: Client,
    socket_statistics: str,
    own_port: int,
    vm: Vm,
    vscode_url: str,
) -> None:
    """
    Rescan on a fixed cadence, for as long as the server this is bound to runs.

    A scan that could not read the machine is logged and skipped rather than ending the
    loop. `ss` exiting non-zero is a transient thing and the loop is already the retry, while
    a scan task that dies takes nothing visible with it: the page holds the last payload it
    was sent, the heartbeat keeps its connection open, and it reads "live" over a listing
    that stopped moving. An unexpected failure still ends the scan, but says so on the way
    out, because `background_task` surfaces it only when the server itself shuts down.
    """
    probes = Probes(client)
    try:
        while True:
            try:
                await scan_once(broadcast, probes, socket_statistics, own_port, vm, vscode_url)
            except NoSocketStatistics as unreadable:
                logger.warning(f"This scan read nothing and the last listing stands: {unreadable!r}")
            except Exception:
                logger.exception("The scan loop is stopping, so the listing will not change again")
                raise
            await asyncio.sleep(SCAN_INTERVAL.total_seconds())
    finally:
        await probes.aclose()
