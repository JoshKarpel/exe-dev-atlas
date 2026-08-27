from __future__ import annotations

import os
import pwd
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import psutil

# The range exe.dev's proxy forwards verbatim. A listener outside it is real but
# unreachable from outside the VM, so listing it would offer a dead link.
ROUTED_PORTS: Final = range(3000, 10_000)


@dataclass(frozen=True, slots=True)
class Binding:
    """One listening socket: one address, one port, one process."""

    port: int
    pid: int | None
    address: str


@dataclass(frozen=True, slots=True)
class Listener:
    """A port with something bound to it, as the kernel describes it."""

    port: int
    pid: int | None
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Process:
    """What the kernel says about the process behind a listener."""

    command_name: str
    command_line: str
    directory: str
    user: str
    started_at: int | None
    executable: str


NO_PROCESS: Final = Process(
    command_name="",
    command_line="",
    directory="",
    user="",
    started_at=None,
    executable="",
)


def group_listeners(bindings: Iterable[Binding]) -> list[Listener]:
    """
    Collapse one socket per binding into one Listener per listening process.

    Grouped by port *and* pid, which is what decides the two cases that look alike on the
    wire. A port bound on both IPv4 and IPv6 arrives as two bindings naming one pid and leaves
    as one row, since it is one service and would be one link. Two different processes on one
    port number, one on loopback and one on a LAN address, arrive as two bindings naming two
    pids and stay two rows: grouping on the port alone would show one process's addresses
    beside the other's command line, working directory and user, and run a session lookup
    against the wrong pid.

    A pre-forking server whose workers each bind the same address with `SO_REUSEPORT` is
    therefore one row per worker, which is what the kernel reports and what the pid on each
    binding says. Sockets that carry no pid (another user's) do collapse into one row per
    port; there is nothing left to tell them apart by.
    """
    addresses: dict[tuple[int, int | None], set[str]] = {}
    for binding in bindings:
        if binding.port not in ROUTED_PORTS:
            continue
        addresses.setdefault((binding.port, binding.pid), set()).add(binding.address)
    return [
        Listener(port=port, pid=pid, addresses=tuple(sorted(bound)))
        for (port, pid), bound in sorted(addresses.items(), key=lambda found: _in_order(*found[0]))
    ]


def _in_order(port: int, pid: int | None) -> tuple[int, int]:
    """A sort key over rows, since a pid of `None` cannot be compared against a number."""
    return (port, -1 if pid is None else pid)


def read_listeners() -> list[Listener]:
    """
    Every routed port with something bound to it, right now.

    Synchronous inside an async scan on purpose, as the `/proc` reads below are. psutil reads
    `/proc/net/tcp` and walks `/proc/<pid>/fd` to name the process holding each socket, which
    is the same work `ss -p` does and which measures in single-digit milliseconds once a
    second; handing it to `asyncio.to_thread` would buy nothing at that cadence.

    A socket owned by another user arrives with `pid=None`, since its `/proc/<pid>/fd` is not
    ours to read. That is the honest answer and the row renders without a process.
    """
    return group_listeners(
        Binding(port=local.port, pid=connection.pid, address=local.ip)
        for connection in psutil.net_connections(kind="tcp")
        if connection.status == psutil.CONN_LISTEN
        # A TCP socket always carries a local address. The empty tuple psutil's type admits
        # here is for the unnamed UNIX sockets that `kind="tcp"` never returns.
        if (local := connection.laddr)
    )


def read_process(pid: int | None) -> Process:
    """
    Everything this machine will say about one pid.

    Every field is asked for independently: a process owned by another user hides its cwd and
    command line from us but still has a name, and a process that exits mid-scan should cost
    one blank field rather than the whole row.
    """
    if pid is None:
        return NO_PROCESS

    try:
        found = psutil.Process(pid)
    except psutil.Error:
        return NO_PROCESS

    return Process(
        command_name=_text(found.name),
        command_line=_command_line(found),
        directory=_text(found.cwd),
        user=_text(found.username),
        started_at=_started_at(found),
        # The binary actually running, which is how a subprocess reaches the *same* program
        # rather than whatever a PATH lookup in this daemon's environment happens to find.
        executable=_text(found.exe),
    )


def read_environ(pid: int) -> dict[str, str]:
    """The environment a running process was started with, or {} if it cannot be read."""
    try:
        return psutil.Process(pid).environ()
    except psutil.Error:
        return {}


def _text(field: Callable[[], str]) -> str:
    """One field of a process, or "" where it will not answer for this one."""
    try:
        return field()
    except psutil.Error:
        return ""


def _command_line(process: psutil.Process) -> str:
    try:
        return " ".join(process.cmdline())
    except psutil.Error:
        return ""


def _started_at(process: psutil.Process) -> int | None:
    """
    Wall-clock epoch second the process started.

    Rounded to a whole second, and stable across scans because of where psutil derives it: the
    `starttime` ticks in `/proc/<pid>/stat` plus `btime` from `/proc/stat`, both integers the
    kernel already settled. The obvious alternative, `time.time()` minus `/proc/uptime`, is
    unstable in a way that costs real traffic: uptime is formatted to centiseconds, so the
    derived boot instant wanders across a 10ms band and a process whose start time lands near
    a half-second boundary rounds differently on each scan, which republishes the whole payload
    and re-renders every client once a second for as long as it runs.
    """
    try:
        return round(process.create_time())
    except psutil.Error:
        return None


def home_directory() -> str:
    """
    Where this user's home directory is, per the passwd database rather than `$HOME`.

    Asked of the kernel because the answer names a directory on the machine an SSH session
    will land in, and the environment this daemon happens to have been started with is not
    evidence about that: a unit run with no `HOME`, or one an operator's shell exported
    something else into, would otherwise put a directory nobody asked for in the VS Code link.
    """
    return pwd.getpwuid(os.getuid()).pw_dir
