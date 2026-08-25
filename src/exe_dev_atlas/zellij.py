from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final

from exe_dev_atlas.processes import run

# The scan runs once a second, so a lookup that takes longer than this is one whose answer
# would already be stale by the time it landed.
SESSION_LIST_TIMEOUT: Final = timedelta(seconds=1.5)

# The environment variables that decide which set of sessions a zellij invocation can see.
# Zellij derives its socket directory from $XDG_RUNTIME_DIR and falls back to
# /tmp/zellij-$UID, and a daemon started by systemd and a shell started by sshd disagree
# about that, so these are read from the *server's* process rather than from our own.
SOCKET_VARIABLES: Final = ("ZELLIJ_SOCKET_DIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME")


def is_zellij_web(command_name: str, command_line: str) -> bool:
    """
    Whether a listener is a zellij web server rather than anything else.

    Matched on the process, not the port: the port is configurable and this is meant to work
    on any box running one, not only on a box set up like this one.

    `web` must be a whole argument, which is what the padding on both sides buys. Matching a
    bare `" web"` prefix instead would read `zellij attach webserver` as a web server, and a
    session named for a web project is not a rare thing to have. Getting that wrong is not
    cosmetic: a row flagged as a session server is stripped of its link and then handed the
    session names of a process that is not serving any.
    """
    return command_name == "zellij" and " web " in f" {command_line} "


def read_environ(pid: int) -> dict[str, str]:
    """The environment a running process was started with, or {} if it cannot be read."""
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except OSError:
        return {}

    environ = {}
    for entry in raw.decode("utf-8", "replace").split("\0"):
        key, _, value = entry.partition("=")
        if key:
            environ[key] = value
    return environ


async def read_sessions(pid: int | None, executable: str) -> tuple[str, ...]:
    """
    Session names a zellij web server can serve, newest listing first.

    Both halves of this come from the server's own process rather than from ours. The
    `executable` is the binary at `/proc/<pid>/exe`, so the exact zellij that is serving is
    the one asked, and no PATH lookup in this daemon's environment has to find a matching
    one. The socket directory comes from that process's environment, because a daemon and a
    login shell derive it differently and asking our own would enumerate a different
    machine-local set than the server is actually serving.
    """
    if pid is None or not executable:
        return ()

    environ = read_environ(pid)
    if not environ:
        return ()

    ran = await run(
        executable,
        "list-sessions",
        "--short",
        "--no-formatting",
        env={
            "HOME": environ.get("HOME", ""),
            **{key: environ[key] for key in SOCKET_VARIABLES if key in environ},
        },
        limit=SESSION_LIST_TIMEOUT,
    )

    # A server with no sessions exits non-zero, which is not an error worth distinguishing
    # from one whose sessions we could not read: both render the port as an ordinary row with
    # nothing broken out.
    if not ran.ok:
        return ()

    return tuple(line.strip() for line in ran.stdout.splitlines() if line.strip())
