from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

from exe_dev_atlas.processes import run

logger = logging.getLogger(__name__)

# The range exe.dev's proxy forwards verbatim. A listener outside it is real but
# unreachable from outside the VM, so listing it would offer a dead link.
ROUTED_PORTS: Final = range(3000, 10_000)

CLOCK_TICKS_PER_SECOND: Final = os.sysconf("SC_CLK_TCK")

LISTEN_PID_PATTERN: Final = re.compile(r"pid=(\d+)")


@dataclass(frozen=True, slots=True)
class Listener:
    """A port with something bound to it, as the kernel describes it."""

    port: int
    pid: int | None
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Process:
    """What `/proc` says about the process behind a listener."""

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


class NoSocketStatistics(RuntimeError):
    """
    `ss` is not on this machine, so there is no way to learn what is listening.

    Raised at startup rather than discovered once a second forever: the whole program is a
    view of what `ss` reports, so a box without `iproute2` has nothing to serve and should
    say so while somebody is still watching the terminal.
    """


def socket_statistics_command() -> str:
    """The absolute path of `ss`, or a refusal naming what is missing."""
    found = shutil.which("ss")
    if found is None:
        raise NoSocketStatistics("`ss` is not on PATH, so nothing can be discovered; install iproute2")
    return found


def parse_listeners(ss_output: str) -> list[Listener]:
    """
    Turn `ss -tlnpH` output into one Listener per listening process.

    Grouped by port *and* pid, which is what decides the two cases that look alike on the
    wire. A port bound on both IPv4 and IPv6 arrives as two lines naming one pid and leaves
    as one row, since it is one service and would be one link. Two different processes on one
    port number, one on loopback and one on a LAN address, arrive as two lines naming two
    pids and stay two rows: grouping on the port alone would show one process's addresses
    beside the other's command line, working directory and user, and run a session lookup
    against the wrong pid.

    A pre-forking server whose workers each bind the same address with `SO_REUSEPORT` is
    therefore one row per worker, which is what `ss` reports and what the pid on each row
    says. Sockets `ss` will not name a process for (another user's) carry no pid, so several
    of those on one port do collapse into one row; there is nothing left to tell them apart
    by.
    """
    addresses: dict[tuple[int, int | None], set[str]] = {}
    for line in ss_output.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 4:
            continue
        address, _, port_text = fields[3].rpartition(":")
        if not port_text.isdigit():
            continue
        port = int(port_text)
        if port not in ROUTED_PORTS:
            continue
        pid_match = LISTEN_PID_PATTERN.search(fields[5] if len(fields) > 5 else "")
        pid = int(pid_match.group(1)) if pid_match else None
        addresses.setdefault((port, pid), set()).add(address.strip("[]"))
    return [
        Listener(port=port, pid=pid, addresses=tuple(sorted(bound)))
        for (port, pid), bound in sorted(addresses.items(), key=lambda found: _in_order(*found[0]))
    ]


def _in_order(port: int, pid: int | None) -> tuple[int, int]:
    """A sort key over rows, since a pid of `None` cannot be compared against a number."""
    return (port, -1 if pid is None else pid)


async def read_listeners(command: str) -> list[Listener]:
    """
    Every routed port with something bound to it, right now.

    A failure raises rather than yielding an empty listing. An empty listing is a real and
    ordinary answer (a box with nothing running), so returning one for a broken `ss` would
    render as "nothing is listening" on a page whose entire job is to say what is.
    """
    ran = await run(command, "--tcp", "--listening", "--numeric", "--processes", "--no-header")
    if not ran.ok:
        raise NoSocketStatistics(f"`ss` exited {ran.exit_code}: {ran.stderr.strip()}")
    return parse_listeners(ran.stdout)


def read_process(pid: int | None) -> Process:
    """
    Everything `/proc` will say about one pid.

    Every field is read independently: a process owned by another user hides its cwd and
    command line from us but still has a name, and a process that exits mid-scan should cost
    one blank field rather than the whole row.

    These reads are synchronous inside an async scan on purpose. `/proc` is a virtual
    filesystem, so each is a microsecond memory formatting call with no device behind it to
    block on, and there are at most a few dozen per scan; handing each to `asyncio.to_thread`
    would cost more in dispatch than the reads themselves take.
    """
    if pid is None:
        return NO_PROCESS

    return Process(
        command_name=read_entry(pid, "comm").strip(),
        command_line=" ".join(read_entry(pid, "cmdline").split("\0")).strip(),
        directory=_link(pid, "cwd"),
        user=_owner(pid),
        started_at=read_start_time(pid),
        # The binary actually running, which is how a subprocess reaches the *same* program
        # rather than whatever a PATH lookup in this daemon's environment happens to find.
        executable=_link(pid, "exe"),
    )


def _entry(pid: int, name: str) -> Path:
    return Path("/proc") / str(pid) / name


def read_entry(pid: int, name: str) -> str:
    """One `/proc/<pid>/<name>` as text, or "" where it could not be read."""
    try:
        return _entry(pid, name).read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def _link(pid: int, name: str) -> str:
    try:
        return str(_entry(pid, name).readlink())
    except OSError:
        return ""


def _owner(pid: int) -> str:
    try:
        return pwd.getpwuid(_entry(pid, "").stat().st_uid).pw_name
    except OSError, KeyError:
        return ""


def ticks_from_stat(stat: str) -> int | None:
    """
    The `starttime` field of `/proc/<pid>/stat`, in clock ticks since boot.

    The `comm` field is parenthesised and may itself contain spaces and parentheses, so the
    fixed-position fields can only be found after its *last* closing paren. Splitting the
    whole line on whitespace, which is the obvious reading, mis-indexes every field for a
    process whose name contains a space.
    """
    try:
        fields = stat[stat.rindex(")") + 2 :].split()
        return int(fields[19])
    except ValueError, IndexError:
        return None


def parse_boot_epoch(proc_stat: str) -> int | None:
    """The `btime` field of `/proc/stat`: the epoch second this machine booted."""
    for line in proc_stat.splitlines():
        name, _, value = line.partition(" ")
        if name == "btime":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


@cache
def boot_epoch() -> int | None:
    """
    When this machine booted, asked once and held for the life of the process.

    `btime` rather than `time.time() - /proc/uptime`, which is the obvious reading and is
    unstable in a way that costs real traffic. `/proc/uptime` is formatted to centiseconds,
    so the derived boot instant wanders across a 10ms band from one read to the next; a
    process whose start time lands within that band of a half-second boundary then rounds to
    a different epoch second on each scan, the payload pair differs, and a full re-serialize
    and a full client re-render go out once a second for as long as it runs. `btime` is an
    integer the kernel already settled and does not move.

    Cached rather than read per call, which is also what bounds the complaint below to one
    line: an unreadable `/proc/stat` costs every row its uptime for good, so the one thing
    it must not do is fail quietly.
    """
    try:
        booted = parse_boot_epoch(Path("/proc/stat").read_text())
    except OSError as unreadable:
        logger.warning(f"No uptimes: /proc/stat could not be read, and it is read once: {unreadable!r}")
        return None
    if booted is None:
        logger.warning("No uptimes: /proc/stat carries no readable `btime`, and it is read once")
    return booted


def read_start_time(pid: int) -> int | None:
    """
    Wall-clock epoch second the process started.

    Derived from boot time rather than sent as an age, so the value is stable across scans
    and does not push a change every second just by getting older.
    """
    booted = boot_epoch()
    if booted is None:
        return None
    stat = read_entry(pid, "stat")
    if not stat:
        return None
    ticks = ticks_from_stat(stat)
    if ticks is None:
        return None
    return round(booted + ticks / CLOCK_TICKS_PER_SECOND)


def home_directory() -> str:
    return pwd.getpwuid(os.getuid()).pw_dir
