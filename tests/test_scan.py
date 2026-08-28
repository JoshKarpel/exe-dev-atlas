from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import fields

import psutil
import pytest
from conftest import ABSENT_PID
from without_http import ConnectionPool

from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.probes import Probes
from exe_dev_atlas.reflection import Vm
from exe_dev_atlas.scan import Broadcast
from exe_dev_atlas.scan import Row
from exe_dev_atlas.scan import scan_forever
from exe_dev_atlas.scan import scan_once

VM = Vm(name="parrot", emoji="🦜")
VSCODE_URL = "vscode://vscode-remote/ssh-remote+parrot.exe.xyz/home/pilot?windowId=_blank"
OWN_PORT = 8123

FIRST = '{"rows": [{"port": 4321, "sessions": ["work"]}]}'
SECOND = '{"rows": [{"port": 8765, "sessions": ["notes"]}]}'

# No payload can carry this, so a connection that has seen nothing is served whatever is
# current instead of waiting for the next scan to find news.
NOTHING_SEEN = -1


async def test_a_connection_that_has_seen_nothing_is_served_at_once() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST)

    version, payload = await broadcast.wait(NOTHING_SEEN)

    assert payload == FIRST
    assert version != NOTHING_SEEN


async def test_a_connection_holding_the_current_payload_waits_for_the_next_one() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST)
    version, _ = await broadcast.wait(NOTHING_SEEN)

    waiting = asyncio.ensure_future(broadcast.wait(version))
    # Long enough for the wait to reach the condition, which is where it must park: a
    # completed task here would mean a connection spinning on news it already has.
    done, _pending = await asyncio.wait((waiting,), timeout=0)
    assert not done

    await broadcast.publish(SECOND)

    next_version, next_payload = await waiting
    assert next_payload == SECOND
    assert next_version != version


async def test_a_scan_that_found_nothing_new_is_not_news() -> None:
    broadcast = Broadcast()
    await broadcast.publish(FIRST)
    version, _ = await broadcast.wait(NOTHING_SEEN)

    await broadcast.publish(FIRST)

    waiting = asyncio.ensure_future(broadcast.wait(version))
    done, _pending = await asyncio.wait((waiting,), timeout=0)
    waiting.cancel()

    assert not done


def test_every_field_of_a_row_reaches_the_browser() -> None:
    # `as_dict` names its fields by hand, so nothing but this pins it to `Row`. A field added
    # to one and not the other fails silently and permanently: `atlas.js` dereferences the
    # payload unguarded, so the first message throws, the page freezes on stale rows, and
    # `state` goes on reading "live" with nothing logged on either side.
    row = Row(
        port=4321,
        addresses=("127.0.0.1",),
        pid=8812,
        command_name="grafana",
        command_line="grafana server",
        directory="/srv/grafana",
        user="pilot",
        started_at=1_787_000_123,
        is_http=True,
        status=200,
        title="Grafana",
        server="nginx/1.25",
    )

    assert set(row.as_dict()) == {field.name for field in fields(Row)}


def listing(*listeners: Listener) -> Callable[[], list[Listener]]:
    """A stand-in for the machine, reporting whatever listeners a test wants scanned."""
    return lambda: list(listeners)


async def test_a_scan_publishes_a_row_for_every_listener(pool: ConnectionPool) -> None:
    found = listing(
        Listener(port=3456, pid=ABSENT_PID - 1, addresses=("192.168.1.5",)),
        Listener(port=3456, pid=ABSENT_PID, addresses=("127.0.0.1",)),
    )
    broadcast = Broadcast()

    probes = Probes(pool)
    try:
        await scan_once(broadcast, probes, found, OWN_PORT, VM, VSCODE_URL)
    finally:
        await probes.aclose()

    _version, payload = await broadcast.wait(NOTHING_SEEN)
    published = json.loads(payload)
    assert [(row["port"], row["pid"]) for row in published["rows"]] == [(3456, ABSENT_PID - 1), (3456, ABSENT_PID)]
    assert published["vm_name"] == "parrot"


class Signalling(logging.Handler):
    """Sets an event as soon as the scan logs anything, so the test waits on the signal itself."""

    def __init__(self, complained: asyncio.Event) -> None:
        super().__init__()
        self.complained = complained

    def emit(self, record: logging.LogRecord) -> None:
        self.complained.set()


async def test_a_scan_that_could_not_read_the_machine_says_so_and_scans_again(
    caplog: pytest.LogCaptureFixture, pool: ConnectionPool
) -> None:
    # The failure this guards against is silent and permanent: a scan task that dies leaves
    # the page holding its last payload, heartbeated, and reading "live" forever.
    def refuses() -> list[Listener]:
        raise psutil.AccessDenied(pid=ABSENT_PID)

    broadcast = Broadcast()
    complained = asyncio.Event()
    logger = logging.getLogger("exe_dev_atlas.scan")
    handler = Signalling(complained)
    logger.addHandler(handler)

    scanning = asyncio.ensure_future(scan_forever(broadcast, pool, refuses, OWN_PORT, VM, VSCODE_URL))
    try:
        async with asyncio.timeout(5):
            await complained.wait()
        assert not scanning.done()
    finally:
        logger.removeHandler(handler)
        scanning.cancel()
        await asyncio.gather(scanning, return_exceptions=True)

    (complaint,) = caplog.records
    assert complaint.levelname == "WARNING"
    assert str(ABSENT_PID) in complaint.message


async def test_the_vscode_link_is_published_to_every_connection(pool: ConnectionPool) -> None:
    broadcast = Broadcast()

    probes = Probes(pool)
    try:
        await scan_once(broadcast, probes, listing(), OWN_PORT, VM, VSCODE_URL)
    finally:
        await probes.aclose()

    _version, payload = await broadcast.wait(NOTHING_SEEN)
    assert json.loads(payload)["vscode_url"] == VSCODE_URL
