from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from without_http import ConnectionPool

from exe_dev_atlas.probes import Probes
from exe_dev_atlas.reflection import Vm
from exe_dev_atlas.scan import Broadcast
from exe_dev_atlas.scan import scan_forever
from exe_dev_atlas.scan import scan_once

VM = Vm(name="parrot", emoji="🦜")
VSCODE_URL = "vscode://vscode-remote/ssh-remote+parrot.exe.xyz/home/pilot?windowId=_blank"
OWN_PORT = 8123

# High enough that no process holds it, so `/proc` answers nothing and the row carries the
# blanks a listener whose process could not be read renders with.
ABSENT_PID = 4_194_303

FIRST_PUBLIC = '{"rows": [{"port": 4321}]}'
FIRST_OWNER = '{"rows": [{"port": 4321, "sessions": ["work"]}]}'
SECOND_PUBLIC = '{"rows": [{"port": 8765}]}'
SECOND_OWNER = '{"rows": [{"port": 8765, "sessions": ["notes"]}]}'

# No payload can carry this, so a connection that has seen nothing is served whatever is
# current instead of waiting for the next scan to find news.
NOTHING_SEEN = -1


async def test_a_connection_that_has_seen_nothing_is_served_at_once() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)

    version, payload = await broadcast.wait(NOTHING_SEEN, is_owner=False)

    assert payload == FIRST_PUBLIC
    assert version != NOTHING_SEEN


async def test_an_owner_and_everybody_else_are_served_the_two_halves_of_one_scan() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)

    owner_version, owner_payload = await broadcast.wait(NOTHING_SEEN, is_owner=True)
    public_version, public_payload = await broadcast.wait(NOTHING_SEEN, is_owner=False)

    assert owner_payload == FIRST_OWNER
    assert public_payload == FIRST_PUBLIC
    # One scan, so both halves carry the same version and neither connection sees the other's
    # read as news of its own.
    assert owner_version == public_version


async def test_a_connection_holding_the_current_payload_waits_for_the_next_one() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)
    version, _ = await broadcast.wait(NOTHING_SEEN, is_owner=False)

    waiting = asyncio.ensure_future(broadcast.wait(version, is_owner=False))
    # Long enough for the wait to reach the condition, which is where it must park: a
    # completed task here would mean a connection spinning on news it already has.
    done, _pending = await asyncio.wait((waiting,), timeout=0)
    assert not done

    await broadcast.publish(SECOND_PUBLIC, SECOND_OWNER)

    next_version, next_payload = await waiting
    assert next_payload == SECOND_PUBLIC
    assert next_version != version


async def test_a_scan_that_found_nothing_new_is_not_news() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)
    version, _ = await broadcast.wait(NOTHING_SEEN, is_owner=False)

    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)

    waiting = asyncio.ensure_future(broadcast.wait(version, is_owner=False))
    done, _pending = await asyncio.wait((waiting,), timeout=0)
    waiting.cancel()

    assert not done


def fake_socket_statistics(tmp_path: Path, listing: str, exit_code: int = 0) -> str:
    """A stand-in for `ss` that reports `listing` and exits however a test wants."""
    command = tmp_path / "ss"
    command.write_text(f"#!/bin/sh\ncat <<'LISTING'\n{listing}\nLISTING\nexit {exit_code}\n")
    command.chmod(0o755)
    return str(command)


async def test_a_scan_publishes_a_row_for_every_listener(tmp_path: Path) -> None:
    listing = "\n".join(
        [
            f'LISTEN 0 4096 127.0.0.1:3456 0.0.0.0:* users:(("server",pid={ABSENT_PID},fd=3))',
            f'LISTEN 0 4096 192.168.1.5:3456 0.0.0.0:* users:(("other",pid={ABSENT_PID - 1},fd=3))',
        ]
    )
    broadcast = Broadcast()

    async with ConnectionPool() as pool:
        probes = Probes(pool)
        try:
            await scan_once(broadcast, probes, fake_socket_statistics(tmp_path, listing), OWN_PORT, VM, VSCODE_URL)
        finally:
            await probes.aclose()

    _version, payload = await broadcast.wait(NOTHING_SEEN, is_owner=False)
    published = json.loads(payload)
    assert [(row["port"], row["pid"]) for row in published["rows"]] == [(3456, ABSENT_PID - 1), (3456, ABSENT_PID)]
    assert published["vm_name"] == "parrot"


async def test_a_scan_that_could_not_read_the_machine_says_so_and_scans_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The failure this guards against is silent and permanent: a scan task that dies leaves
    # the page holding its last payload, heartbeated, and reading "live" forever.
    broken = fake_socket_statistics(tmp_path, "ss: cannot open netlink socket", exit_code=1)
    broadcast = Broadcast()

    async with ConnectionPool() as pool:
        scanning = asyncio.ensure_future(scan_forever(broadcast, pool, broken, OWN_PORT, VM, VSCODE_URL))
        try:
            for _ in range(500):
                if caplog.records:
                    break
                await asyncio.sleep(0.01)
            assert not scanning.done()
        finally:
            scanning.cancel()
            await asyncio.gather(scanning, return_exceptions=True)

    (complaint,) = caplog.records
    assert complaint.levelname == "WARNING"
    assert "exited 1" in complaint.message


async def test_a_change_only_the_owner_can_see_still_reaches_the_owner() -> None:
    # Both payloads are published together and versioned as one, so a scan whose public half
    # is unchanged must still wake an owner connection.
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)
    version, _ = await broadcast.wait(NOTHING_SEEN, is_owner=True)

    await broadcast.publish(FIRST_PUBLIC, SECOND_OWNER)

    _next_version, payload = await broadcast.wait(version, is_owner=True)
    assert payload == SECOND_OWNER
