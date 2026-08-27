from __future__ import annotations

import asyncio
import gc
import sys
from collections.abc import AsyncIterator
from collections.abc import Iterator
from datetime import timedelta

import pytest

from exe_dev_atlas.processes import ProgramFailed
from exe_dev_atlas.processes import inheriting
from exe_dev_atlas.processes import run

# A child that announces itself by connecting, then outlives any patience the test has. The
# connection is the whole instrument: the kernel closes it when the process dies, so the
# server side learns of the death without polling for it.
ANNOUNCE_THEN_LINGER = """
import socket, sys, time

held = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
time.sleep(30)
"""


@pytest.fixture
async def announcements() -> AsyncIterator[tuple[int, asyncio.Event, asyncio.Event]]:
    """A port a child can connect to, and the two events its life and death set."""
    connected = asyncio.Event()
    disconnected = asyncio.Event()

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected.set()
        await reader.read()
        disconnected.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    yield port, connected, disconnected

    server.close()
    await server.wait_closed()


@pytest.fixture
def uncollected() -> Iterator[None]:
    """
    Hold the cyclic collector still for the duration of the test.

    Finalizing an abandoned subprocess transport kills its child as a side effect, so a
    collection landing in the wrong place makes a `run` that leaves children behind look like
    one that cleans up after itself, on whichever runs the collector happens to fire in.
    """
    gc.disable()
    yield
    gc.enable()


class TestRunningAProgram:
    async def test_a_program_that_succeeds_reports_what_it_wrote(self) -> None:
        ran = await run("sh", "-c", "echo out; echo err >&2")

        assert ran.ok is True
        assert ran.exit_code == 0
        assert ran.stdout == "out\n"
        assert ran.stderr == "err\n"

    async def test_a_program_that_fails_comes_back_as_a_value_rather_than_an_exception(self) -> None:
        ran = await run("sh", "-c", "exit 3")

        assert ran.ok is False
        assert ran.exit_code == 3
        assert ran.timed_out is False

    async def test_a_program_that_could_not_be_started_reports_why(self) -> None:
        ran = await run("exe-dev-atlas-no-such-program")

        assert ran.ok is False
        assert ran.timed_out is False
        assert "exe-dev-atlas-no-such-program" in ran.stderr

    async def test_checked_raises_carrying_the_whole_result(self) -> None:
        ran = await run("sh", "-c", "echo the unit could not be loaded >&2; exit 5")

        with pytest.raises(ProgramFailed) as failure:
            ran.checked()

        assert failure.value.ran == ran
        assert "the unit could not be loaded" in str(failure.value)

    async def test_a_given_environment_replaces_this_process_environment(self) -> None:
        ran = await run("env", env={"ATLAS_ONLY": "yes"})

        assert ran.stdout.splitlines() == ["ATLAS_ONLY=yes"]

    def test_inheriting_keeps_this_process_environment_under_the_overrides(self) -> None:
        inherited = inheriting({"ATLAS_ONLY": "yes"})

        assert inherited["ATLAS_ONLY"] == "yes"
        assert inherited["PATH"]


class TestEndingAProgramEarly:
    async def test_a_program_that_runs_out_of_time_is_killed_and_says_so(
        self, announcements: tuple[int, asyncio.Event, asyncio.Event]
    ) -> None:
        port, connected, disconnected = announcements

        ran = await run(sys.executable, "-c", ANNOUNCE_THEN_LINGER, str(port), limit=timedelta(seconds=0.5))

        assert ran.timed_out is True
        assert ran.ok is False
        assert connected.is_set()
        async with asyncio.timeout(5):
            await disconnected.wait()

    async def test_cancelling_a_run_kills_the_child_rather_than_leaving_it_behind(
        self, announcements: tuple[int, asyncio.Event, asyncio.Event], uncollected: None
    ) -> None:
        # Shutdown cancels the scan loop, and the cancellation travels into whatever zellij
        # lookup was in flight. Handled only as a timeout, the child outlives the server that
        # started it.
        port, connected, disconnected = announcements
        running = asyncio.create_task(run(sys.executable, "-c", ANNOUNCE_THEN_LINGER, str(port)))
        await connected.wait()

        running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await running
        async with asyncio.timeout(5):
            await disconnected.wait()
