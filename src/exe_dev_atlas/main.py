# The two things you do to the atlas: run it, and put it on a machine.
#
# Neither command decides anything. `serve` builds the app and runs it; `install` renders a
# unit and converges it; everything either one needs to be told arrives as an argument.

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import shutil
from typing import Annotated
from typing import Final

import typer

from exe_dev_atlas import app
from exe_dev_atlas.app import DidNotStart
from exe_dev_atlas.install import SETTLE
from exe_dev_atlas.install import BadSuffix
from exe_dev_atlas.install import Converged
from exe_dev_atlas.install import NoInterpreter
from exe_dev_atlas.install import Unit
from exe_dev_atlas.install import can_run_the_atlas
from exe_dev_atlas.install import config_home
from exe_dev_atlas.install import converge
from exe_dev_atlas.install import is_lingering
from exe_dev_atlas.install import running_executable
from exe_dev_atlas.install import service_name
from exe_dev_atlas.install import systemctl_for

# What exe.dev's proxy points the bare `https://<vm>.exe.xyz/` hostname at, which is the
# whole reason this program has a default port at all: served here, the box's front door is
# an index of everything else worth opening rather than any one of those things.
DEFAULT_PORT: Final = 8000

Port = Annotated[
    int,
    typer.Option(
        "--port",
        "-p",
        envvar="EXE_DEV_ATLAS_PORT",
        help="the port to serve on, which exe.dev proxies to https://<vm>.exe.xyz:<port>/",
    ),
]

# Rendered into the unit either way, so an install records the choice rather than inheriting
# whatever this command's default happens to be at the time (see `install.Unit.text`).
VsCodeLink = Annotated[
    bool,
    typer.Option(
        "--vs-code-link/--no-vs-code-link",
        envvar="EXE_DEV_ATLAS_VS_CODE_LINK",
        help="offer the VS Code Remote-SSH link under the header",
    ),
]

# Not read from the environment, unlike the settings above: it names the unit an install
# converges rather than anything the server does, and a stray variable that quietly redirects
# an install to another unit is a worse failure than typing it out.
SystemdUnitSuffix = Annotated[
    str,
    typer.Option(
        "--systemd-unit-suffix",
        help="install as `exe-dev-atlas-<suffix>` instead, so this atlas sits beside the default one",
    ),
]

exe_dev_atlas = typer.Typer(
    help="Serve an index of this VM's ports, sessions, and workspaces, or install it on this machine.",
    no_args_is_help=True,
    add_completion=False,
)


@exe_dev_atlas.command()
def serve(port: Port = DEFAULT_PORT, vscode_link: VsCodeLink = True) -> None:
    """
    Run the atlas in the foreground, until a signal stops it.

    A startup that could not read this VM's name from exe.dev's reflection integration is a
    failure rather than a page that cannot say which box it is describing, so it is reported
    as one line and a non-zero exit. Under the unit that is `Restart=always` trying again
    every five seconds, and `journalctl --user -u <unit>` holds the reason.
    """
    start_logging()
    try:
        app.serve_until_stopped(port, vscode_link=vscode_link)
    except DidNotStart as unstarted:
        typer.echo(str(unstarted), err=True)
        raise typer.Exit(1) from None


@exe_dev_atlas.command()
def install(
    port: Port = DEFAULT_PORT,
    vscode_link: VsCodeLink = True,
    systemd_unit_suffix: SystemdUnitSuffix = "",
) -> None:
    """
    Converge a user systemd unit and restart the atlas onto this interpreter.

    The unit names the interpreter running this command, so what an install means is "the
    running service is this installation of the package". Run it after upgrading the package:
    an upgrade in place leaves the unit text identical, so only the restart puts the new code
    in front of anything.

    One machine can hold several: `--systemd-unit-suffix dev --port 8001` converges
    `exe-dev-atlas-dev` and leaves `exe-dev-atlas` alone. Give each its own port, since
    nothing stops two units from being told to bind the same one.

    Safe to run as often as you like. A restarted scan re-derives the whole listing from the
    kernel and holds nothing from the one it replaced.
    """
    try:
        service = service_name(systemd_unit_suffix)
    except BadSuffix as bad:
        typer.echo(str(bad), err=True)
        raise typer.Exit(1) from None

    if shutil.which("systemctl") is None:
        typer.echo("no systemctl here, so there is no service to install", err=True)
        raise typer.Exit(1)

    try:
        executable = running_executable()
    except NoInterpreter as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(1) from None

    # Asked before anything is written, so a unit that could not start is never installed.
    if not asyncio.run(can_run_the_atlas(executable)):
        typer.echo(
            f"{executable} cannot import exe_dev_atlas, so the service would fail at every start.\n"
            f"Install the package and run `exe-dev-atlas install` from that installation.",
            err=True,
        )
        raise typer.Exit(1)

    unit = Unit(
        service=service,
        config_home=config_home(os.environ),
        executable=executable,
        port=port,
        vscode_link=vscode_link,
    )

    # Said before the work rather than after it, because the watch below is most of the time
    # this command takes and a silent pause reads as a hang.
    typer.echo(f"converging {service}, and watching it for {SETTLE.total_seconds():.0f}s afterwards")
    converged = asyncio.run(converge(unit, systemctl_for(os.environ)))
    _report(converged)
    if not converged.is_running:
        raise typer.Exit(1)

    if not asyncio.run(is_lingering(getpass.getuser())):
        typer.echo(
            f"\nnote: lingering is off for this user, so {service} starts at your first login\n"
            f"rather than at boot. `loginctl enable-linger {getpass.getuser()}` fixes that.",
            err=True,
        )


def start_logging() -> None:
    """
    Send the server's own account of itself to stderr, which is where the journal reads it.

    The unit sets no `StandardError=`, so systemd's default puts stderr in the journal and
    `journalctl --user -u <the unit it was installed as>` is the whole log story. Nothing
    here carries a timestamp, because the journal stamps every line it receives and running
    in the foreground is the same output without one rather than a different format.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _report(converged: Converged) -> None:
    """
    Say what changed about the unit, then say what the service did about it.

    "Already current" is the answer about the *file* on most runs, and on its own it reads as
    "nothing happened", which is the misunderstanding the second line exists to prevent: the
    restart is the point of running this after an upgrade, and the unit text cannot show a
    change in the code it starts.

    The second line is what systemd was asked rather than what it was told, so a service that
    started and then gave up says so here instead of being reported as serving a port nothing
    is bound to. It names what to read next, since the reason is in the journal and nowhere
    this command can reach.

    Both lines name the service, because on a machine holding more than one atlas the only
    thing distinguishing this report from the other install's is which unit it is about.
    """
    unit = converged.unit
    typer.echo(f"installed {unit.path}" if converged.text_changed else f"{unit.path} is already current")
    if converged.is_running:
        typer.echo(f"restarted {unit.service}, serving on port {unit.port} from {unit.executable}")
        return
    typer.echo(
        f"{unit.service} is {converged.state or 'unknown'} rather than running, so nothing is serving "
        f"port {unit.port}.\n"
        f"`journalctl --user -u {unit.service} -e` says why. A port another program already holds "
        f"and an unanswered reflection lookup are the two usual reasons.",
        err=True,
    )


def main() -> None:
    exe_dev_atlas()


if __name__ == "__main__":
    main()
