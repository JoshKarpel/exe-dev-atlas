from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from exe_dev_atlas.identity import Identity
from exe_dev_atlas.identity import refresh_forever
from exe_dev_atlas.reflection import Reflection
from exe_dev_atlas.reflection import ReflectionFailed

NAMED = Reflection(name="cumulus", emoji="\N{THOUGHT BALLOON}")
RENAMED = Reflection(name="nimbus", emoji="\N{HIGH VOLTAGE SIGN}")
WORKSPACE = "/home/pilot"

# Short enough that a test drives several passes of the loop in no time, which is the only
# reason the cadence is a parameter rather than the constant the server runs with.
SOON = timedelta(seconds=0.001)


class TestIdentity:
    def test_the_shell_and_the_link_both_name_the_vm(self) -> None:
        identity = Identity(NAMED, workspace=WORKSPACE)

        assert b"<title>cumulus</title>" in identity.page.body
        assert identity.vscode_url == f"vscode://vscode-remote/ssh-remote+cumulus.exe.xyz{WORKSPACE}?windowId=_blank"

    def test_a_rename_replaces_everything_that_says_the_old_name(self) -> None:
        # The reason these are rebuilt together rather than one at a time: a page titled
        # `cumulus` beside a link opening an SSH session on `nimbus` is worse than either
        # being briefly stale.
        identity = Identity(NAMED, workspace=WORKSPACE)

        identity.update(RENAMED)

        assert identity.vm == RENAMED
        assert b"<title>nimbus</title>" in identity.page.body
        assert "nimbus.exe.xyz" in identity.vscode_url

    def test_an_atlas_installed_without_the_link_offers_no_url_at_all(self) -> None:
        # An empty URL is how the page is told there is nothing to offer, so the decision is
        # made once here rather than carried into the payload and read again in the browser.
        identity = Identity(NAMED, workspace=None)

        assert identity.vscode_url == ""

    def test_the_link_stays_off_across_a_rename(self) -> None:
        identity = Identity(NAMED, workspace=None)

        identity.update(RENAMED)

        assert identity.vscode_url == ""


class Answering:
    """
    Stands in for reflection, announcing each call so a test can wait for one rather than sleep.

    Announcing on entry rather than on return is what makes the wait exact: the loop reads,
    writes, and comes back around, so seeing the *next* call start is proof the write from the
    call before it has already landed.
    """

    def __init__(self, answer: Reflection | ReflectionFailed) -> None:
        self.answer = answer
        self.calls: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self) -> Reflection:
        await self.calls.put(None)
        if isinstance(self.answer, ReflectionFailed):
            raise self.answer
        return self.answer

    async def called_twice(self) -> None:
        await self.calls.get()
        await self.calls.get()


async def refreshing(identity: Identity, answering: Answering) -> None:
    """Run the refresh loop against `answering` until it has answered twice, then stop it."""
    loop = asyncio.ensure_future(refresh_forever(answering, identity, interval=SOON))
    try:
        async with asyncio.timeout(5):
            await answering.called_twice()
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_a_vm_renamed_under_a_running_server_reaches_its_own_page() -> None:
    identity = Identity(NAMED, workspace=WORKSPACE)
    answering = Answering(RENAMED)

    await refreshing(identity, answering)

    assert identity.vm == RENAMED
    assert b"<title>nimbus</title>" in identity.page.body
    assert "nimbus.exe.xyz" in identity.vscode_url


async def test_a_lookup_that_did_not_answer_leaves_the_last_good_name_standing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Only a success writes. A blank heading is not a better description of this VM than a
    # name that was true when it was read, and the startup lookup is what guarantees there is
    # always one of those to keep.
    identity = Identity(NAMED, workspace=WORKSPACE)
    answering = Answering(ReflectionFailed("reflection is not answering"))

    with caplog.at_level(logging.WARNING, logger="exe_dev_atlas.identity"):
        await refreshing(identity, answering)

    assert identity.vm == NAMED
    assert b"<title>cumulus</title>" in identity.page.body
    assert any("cumulus" in record.message for record in caplog.records)
