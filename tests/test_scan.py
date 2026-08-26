from __future__ import annotations

import asyncio

from exe_dev_atlas.scan import Broadcast

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


async def test_a_change_only_the_owner_can_see_still_reaches_the_owner() -> None:
    # Both payloads are published together and versioned as one, so a scan whose public half
    # is unchanged must still wake an owner connection.
    broadcast = Broadcast()
    await broadcast.publish(FIRST_PUBLIC, FIRST_OWNER)
    version, _ = await broadcast.wait(NOTHING_SEEN, is_owner=True)

    await broadcast.publish(FIRST_PUBLIC, SECOND_OWNER)

    _next_version, payload = await broadcast.wait(version, is_owner=True)
    assert payload == SECOND_OWNER
