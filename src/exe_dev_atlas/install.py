# Putting the atlas on a machine, which on a Linux box means one user systemd unit.
#
# The unit names the interpreter that ran the install. That is the whole mechanism and
# everything else follows from it: `exe-dev-atlas install` is invoked *by* the installed CLI,
# so `sys.executable` is already the absolute path of an interpreter holding this package and
# its dependencies. Nothing has to be looked up on `PATH`, derived from a login shell, or
# guessed, and every way of installing the package (`uv tool`, `pipx`, `pip install --user`, a
# checkout) is served by the same rendering.
#
# What this deliberately does not do is fetch, build, or manage a Python environment. Whoever
# put the package here already chose a version; this only points systemd at it. So there is no
# `--version` flag and no second package manager to disagree with the one actually used.

from __future__ import annotations

import os
import sys
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from exe_dev_atlas.processes import Ran
from exe_dev_atlas.processes import inheriting
from exe_dev_atlas.processes import run

SERVICE: Final = "exe-dev-atlas"

UNIT: Final = """\
[Unit]
Description=Index this VM's ports, sessions, and workspaces on the default hostname
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
# A user unit inherits none of a login shell's PATH, so this is the standard system set and
# nothing more. Nothing the service runs is looked up on it: sockets come from psutil rather
# than a subprocess, and the zellij binary a session lookup runs is read from /proc/<pid>/exe
# and handed an environment of its own rather than this one. It is here as an ordinary
# default for anything that does inherit the unit's environment.
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={executable} -m exe_dev_atlas serve --port {port} {vscode_flag}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

# What systemctl is asked, as the only shape of call this makes: arguments after `--user`,
# and the whole result back. Injected rather than reached for, so a test drives convergence
# without a service manager and asserts on what was asked rather than on what happened.
type Systemctl = Callable[[tuple[str, ...]], Awaitable[Ran]]


@dataclass(frozen=True, slots=True)
class Converged:
    """What an install actually changed, which on most runs is nothing."""

    unit: Path
    unit_changed: bool


def config_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def unit_path(config_home: Path) -> Path:
    return config_home / "systemd" / "user" / f"{SERVICE}.service"


def unit_text(executable: Path, port: int, *, vscode_link: bool) -> str:
    """
    The unit as this installation would have it, with every setting named either way.

    The VS Code flag is rendered in both directions rather than appended only when the link
    is off, so the installed unit is a record of what was asked for rather than a record of
    one half of it: `--vs-code-link` says the link is on now and keeps saying it if the
    command's default ever moves, where an absent flag would quietly start meaning the
    opposite.
    """
    return UNIT.format(
        executable=executable,
        port=port,
        vscode_flag="--vs-code-link" if vscode_link else "--no-vs-code-link",
    )


class NoInterpreter(RuntimeError):
    """
    There is no interpreter path to put in the unit, or the one there is cannot run the atlas.

    Raised at install time on purpose. The alternative is a unit that installs cleanly and
    then fails at every start with `No module named exe_dev_atlas`, restarting every five
    seconds, discovered whenever somebody next looks at the machine.
    """


def running_executable() -> Path:
    """
    The interpreter to name in the unit, which is this process's own.

    `sys.executable` rather than `sys.argv[0]`: the former is documented as the absolute path
    of the running interpreter, while the latter is a console-script shim that can be
    relocated independently of the environment it points into, and is a `site-packages` path
    when the CLI was reached through `python -m` at all.

    Deliberately *not* resolved. A virtualenv's `bin/python` is a symlink to the base
    interpreter it was built from, and that base has none of the venv's packages on its path,
    so `Path(...).resolve()` yields an interpreter that cannot import this one. Measured
    against a real `uv tool install`: the raw value is the tool venv's own `bin/python` and
    the resolved value is `~/.local/share/uv/python/cpython-3.14.7-.../bin/python3.14`, which
    answers `No module named exe_dev_atlas`. The symlink is the thing that makes the venv a
    venv, so following it is exactly wrong.
    """
    if not sys.executable:
        raise NoInterpreter("this Python reports no executable path, so there is nothing to put in a unit")
    return Path(sys.executable)


async def can_run_the_atlas(executable: Path) -> bool:
    """
    Whether `executable` can actually import this package, asked rather than assumed.

    True by construction for the ordinary install, since the command doing the asking is
    running on that interpreter. Asked anyway because it is one cheap subprocess against a
    failure that is silent at install and loud only much later, and because the assumption
    has already been wrong once: it does not hold for a resolved venv symlink.
    """
    return (await run(str(executable), "-c", "import exe_dev_atlas")).ok


def systemctl_for(environ: Mapping[str, str]) -> Systemctl:
    """
    `systemctl --user` on this machine, with the one variable it cannot do without.

    A non-login context (a hook, a provisioning script, a command over ssh) usually lacks
    `XDG_RUNTIME_DIR`, and without it every call fails with "Failed to connect to bus: No
    medium found", which names nothing a person could act on.
    """
    env = inheriting({"XDG_RUNTIME_DIR": environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"})

    async def call(arguments: tuple[str, ...]) -> Ran:
        return await run("systemctl", "--user", *arguments, env=env)

    return call


async def is_lingering(user: str) -> bool:
    """
    Whether this user's manager starts at boot rather than at first login.

    Worth asking because the failure without it is silent in the worst way: the unit is
    enabled, the file is correct, and after a reboot nothing is running until somebody
    happens to log in.
    """
    shown = await run("loginctl", "show-user", user, "--property=Linger")
    return shown.ok and shown.stdout.strip().endswith("=yes")


async def converge(executable: Path, unit: Path, port: int, systemctl: Systemctl, *, vscode_link: bool) -> Converged:
    """
    Make this machine's service match this interpreter: the unit file, and the code it runs.

    Comparing the unit text is not enough, and the reading that stops there is wrong in the
    worst way available. The text is a function of an interpreter path and the settings
    `serve` was asked for, so an upgrade in place (which is what `uv tool upgrade` does)
    renders identically, and an install that reloaded and restarted only on a difference
    would report success while leaving the old code serving: it says the unit is already
    current, which is true, and a reader takes it for a statement about the process.

    So the restart is unconditional and the reload is not: the manager needs re-reading only
    when the file it read has changed.

    The restart costs a scan abandoned wherever it had got to, which is nothing: the next
    scan re-derives the whole listing from the kernel, holding no state from the last one.

    Enabling is unconditional because enabling an already-enabled unit changes nothing, and a
    unit that is installed but not enabled is the failure this exists to prevent. It needs no
    `--now`: `restart` starts a loaded unit that is not running, so a second way of starting
    it would be redundant.
    """
    changed = write_unit(unit, unit_text(executable, port, vscode_link=vscode_link))
    if changed:
        (await systemctl(("daemon-reload",))).checked()

    (await systemctl(("enable", SERVICE))).checked()
    (await systemctl(("restart", SERVICE))).checked()
    return Converged(unit=unit, unit_changed=changed)


def write_unit(unit: Path, wanted: str) -> bool:
    """
    Put `wanted` at `unit` if it is not already there, and say whether anything changed.

    The returned bool is what `converge` gates `daemon-reload` on, which is the only reason
    the comparison happens at all: the manager needs re-reading exactly when the file it read
    has changed, and never otherwise.
    """
    unit.parent.mkdir(parents=True, exist_ok=True)
    if unit.exists() and unit.read_text() == wanted:
        return False
    unit.write_text(wanted)
    unit.chmod(0o644)
    return True
