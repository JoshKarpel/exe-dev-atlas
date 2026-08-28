# Putting the atlas on a machine, which on a Linux box means a user systemd unit.
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

import asyncio
import os
import re
import sys
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from exe_dev_atlas.processes import Ran
from exe_dev_atlas.processes import inheriting
from exe_dev_atlas.processes import run
from exe_dev_atlas.reflection import REFLECTION_TIMEOUT

SERVICE: Final = "exe-dev-atlas"

# How long a restarted unit is watched before the install says it is running. The unit is
# `Type=exec`, whose start job completes as soon as `execve` returns: a `restart` succeeds
# against a process that is about to exit because the port is already held, and reporting from
# that alone is how an install comes to claim a service that never bound anything. The window
# covers the slowest failure the atlas has, which is its reflection lookup timing out before
# the process gives up, and everything quicker lands well inside it.
SETTLE: Final = REFLECTION_TIMEOUT + timedelta(seconds=2)

# What may follow the package name in a unit's own name. systemd accepts more than this, and
# the rest is not worth what it costs to read: a `.` renders as a second filename extension,
# an `@` makes the thing a template instance, and a `/` names another unit entirely. A
# suffix is a word that tells two installs apart.
SUFFIX: Final = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")

UNIT: Final = """\
[Unit]
Description=Index this VM's ports, sessions, and workspaces, served on port {port}
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
class Unit:
    """
    One installed atlas: what its unit is called, where the file lives, and what it starts.

    A machine can hold several. Everything that can differ between them is here, so an
    install converges one of these and disturbs no other: the service name decides which
    unit is written and restarted, and the rest is what that unit runs.
    """

    service: str
    config_home: Path
    executable: Path
    port: int
    vscode_link: bool

    @property
    def path(self) -> Path:
        """
        The file this unit is written to, derived rather than carried beside the name.

        `converge` writes this path and restarts `service`, so the two being one fact is what
        keeps an install from writing one file and restarting a different unit. Held as a
        field, that agreement would rest on every construction site remembering to pair them,
        which is the failure `service_name`'s parsing already refuses one layer down.
        """
        return unit_path(self.config_home, self.service)

    @property
    def text(self) -> str:
        """
        The unit file this install would have, with every setting named either way.

        The VS Code flag is rendered in both directions rather than appended only when the
        link is off, so the installed unit is a record of what was asked for rather than a
        record of one half of it: `--vs-code-link` says the link is on now and keeps saying
        it if the command's default ever moves, where an absent flag would quietly start
        meaning the opposite.
        """
        return UNIT.format(
            executable=self.executable,
            port=self.port,
            vscode_flag="--vs-code-link" if self.vscode_link else "--no-vs-code-link",
        )


@dataclass(frozen=True, slots=True)
class Converged:
    """
    What an install actually changed, which on most runs is nothing, and what came of it.

    `state` is systemd's own `ActiveState` for the unit, read once the restart has had time to
    fail. It is carried rather than reduced to a bool because `failed` and `activating` are
    different things to be told: the first is a service that gave up, the second one caught
    between restarts, and an operator reading either wants the word systemd used.
    """

    unit: Unit
    text_changed: bool
    state: str

    @property
    def is_running(self) -> bool:
        return self.state == "active"


class BadSuffix(ValueError):
    """The suffix offered is not one a unit name can carry."""


def service_name(suffix: str) -> str:
    """
    What this install's unit is called: the package name, and a suffix if one was asked for.

    An install with no suffix is *the* atlas on this machine, which is what a machine wanting
    only one gets without asking for anything. A suffixed one sits beside it under a name of
    its own, so two installs never converge onto one unit and fight over a port.

    The package name is always the prefix rather than the whole name being the caller's to
    choose, which is what keeps `systemctl --user list-units 'exe-dev-atlas*'` an answer to
    "what atlases are on this box" and keeps a typo from writing over an unrelated unit.
    """
    if not suffix:
        return SERVICE
    if not SUFFIX.fullmatch(suffix):
        raise BadSuffix(
            f"{suffix!r} cannot go in a unit name: use letters, digits, hyphens, and "
            f"underscores, starting with a letter or a digit"
        )
    return f"{SERVICE}-{suffix}"


def config_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def unit_path(config_home: Path, service: str) -> Path:
    return config_home / "systemd" / "user" / f"{service}.service"


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


async def converge(unit: Unit, systemctl: Systemctl, settle: timedelta = SETTLE) -> Converged:
    """
    Make this unit match this interpreter: the file it is written from, and the code it runs.

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

    A `restart` that returns is not a service that is running, and the difference is the other
    half of the same lie: the unit is `Type=exec`, so its start job completes at `execve` and
    succeeds against a process that exits a moment later because something else already holds
    the port. So the state is read back after `settle`, which is a plain observation window
    rather than a race being waited out, and long enough that a start which is going to fail
    has failed inside it. Tests pass zero.

    Every call names `unit.service`, so an install reaches exactly the one unit it rendered
    and any other atlas on the machine goes on running untouched.
    """
    changed = write_unit(unit.path, unit.text)
    if changed:
        (await systemctl(("daemon-reload",))).checked()

    (await systemctl(("enable", unit.service))).checked()
    (await systemctl(("restart", unit.service))).checked()

    await asyncio.sleep(settle.total_seconds())
    shown = (await systemctl(("show", unit.service, "--property=ActiveState", "--value"))).checked()
    return Converged(unit=unit, text_changed=changed, state=shown.stdout.strip())


def write_unit(path: Path, wanted: str) -> bool:
    """
    Put `wanted` at `path` if it is not already there, and say whether anything changed.

    The returned bool is what `converge` gates `daemon-reload` on, which is the only reason
    the comparison happens at all: the manager needs re-reading exactly when the file it read
    has changed, and never otherwise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == wanted:
        return False
    path.write_text(wanted)
    path.chmod(0o644)
    return True
