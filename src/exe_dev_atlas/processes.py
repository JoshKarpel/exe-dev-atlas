# Running a program and getting back what it did, as one value.
#
# Every subprocess here is short, quiet, and expected to finish on its own: `ss` listing
# sockets, `zellij` listing sessions, `systemctl` converging a unit. None of them streams, none
# produces output worth bounding, and none needs a two-stage kill, so this is `communicate`
# with a timeout around it rather than a process supervisor.

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta


class ProgramFailed(RuntimeError):
    """
    A program that was expected to succeed did not.

    Carries the whole result rather than a message, because what a caller wants to do about a
    failure usually depends on the output: `systemctl`'s stderr names the unit it could not
    load, and that is the diagnosis rather than a detail of it.
    """

    def __init__(self, ran: Ran) -> None:
        self.ran = ran
        super().__init__(f"{ran.summary} exited {ran.exit_code}: {ran.stderr.strip() or ran.stdout.strip()}")


@dataclass(frozen=True, slots=True)
class Ran:
    """What one program did: its own report, decoded, plus whether it ran out of time."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def summary(self) -> str:
        return " ".join(self.command)

    def checked(self) -> Ran:
        """The same result when the program succeeded, or a failure carrying it when it did not."""
        if not self.ok:
            raise ProgramFailed(self)
        return self


async def run(
    *command: str,
    env: Mapping[str, str] | None = None,
    limit: timedelta | None = None,
) -> Ran:
    """
    Run `command` to completion and report what happened, including running out of time.

    A timeout comes back as a `Ran` with `timed_out` set rather than as an exception, because
    for the callers that have one it is an outcome and not an accident: a zellij server that
    did not answer in time renders as an ordinary row with no sessions broken out, which is
    the same thing it renders when it answers that it has none. Callers that want the loud
    version ask for it with `checked()`.

    A program that could not be started at all (not installed, not executable) comes back the
    same shape, for the same reason: to a caller that was only asking, `zellij` being absent
    reads as "this question has no answer", not as an exception out of a listing.

    The environment is *replaced* where one is given rather than merged over this process's,
    and merging is left to `inheriting`. A subprocess that silently inherits whatever the
    daemon was started with is how a stray variable in an operator's shell changes what a
    child sees without saying a word about it, and one caller here depends on the replacement:
    a zellij lookup must present the *server's* socket directory, not ours.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=None if env is None else dict(env),
        )
    except OSError as unstartable:
        return Ran(command=tuple(command), exit_code=-1, stdout="", stderr=str(unstartable), timed_out=False)

    try:
        async with asyncio.timeout(None if limit is None else limit.total_seconds()):
            stdout, stderr = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        return Ran(command=tuple(command), exit_code=-1, stdout="", stderr="", timed_out=True)

    return Ran(
        command=tuple(command),
        exit_code=-1 if process.returncode is None else process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        timed_out=False,
    )


def inheriting(overrides: Mapping[str, str]) -> dict[str, str]:
    """The current environment with `overrides` applied, for a child that wants both."""
    return {**os.environ, **overrides}
