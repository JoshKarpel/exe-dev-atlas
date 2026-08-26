from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from conftest import as_caller
from conftest import scope
from without_asgi import HttpScope
from without_asgi import Outbound
from without_asgi import ResponseBody
from without_asgi import ResponseStart
from without_asgi import headers

from exe_dev_atlas.app import Atlas
from exe_dev_atlas.app import events
from exe_dev_atlas.page import page_response
from exe_dev_atlas.scan import Broadcast

OWNER = "owner@example.com"

# Distinct on both halves, so a connection served the wrong one is visible rather than
# merely differing in a field somebody forgot to assert on.
PUBLIC = '{"rows": [{"port": 4321}]}'
OWNER_ONLY = '{"rows": [{"port": 4321, "sessions": ["work"]}], "vscode_url": "vscode://open"}'


def atlas(broadcast: Broadcast) -> Atlas:
    return Atlas(broadcast=broadcast, owner_email=OWNER, page=page_response())


async def connect(broadcast: Broadcast, caller: HttpScope, *, take: int) -> list[Outbound]:
    """Open the events route, collect the first `take` outbound events, and hang up."""
    reply = await events(atlas(broadcast), caller)
    assert isinstance(reply, AsyncGenerator), "the events route replies with a stream, not a buffered response"
    collected: list[Outbound] = []
    try:
        async for event in reply:
            collected.append(event)
            if len(collected) == take:
                break
    finally:
        await reply.aclose()
    return collected


@pytest.fixture
async def published() -> Broadcast:
    broadcast = Broadcast()
    await broadcast.publish(PUBLIC, OWNER_ONLY)
    return broadcast


async def test_the_response_head_declares_an_event_stream(published: Broadcast) -> None:
    head, *_ = await connect(published, scope(), take=1)

    assert isinstance(head, ResponseStart)
    assert head.status == 200
    assert headers.first(head.headers, b"content-type") == b"text/event-stream"


async def test_the_response_head_tells_the_proxy_not_to_buffer(published: Broadcast) -> None:
    # Without it exe.dev's proxy holds every event until a stream that never ends, which
    # presents as a page that connects and then shows nothing.
    head, *_ = await connect(published, scope(), take=1)

    assert isinstance(head, ResponseStart)
    assert headers.first(head.headers, b"x-accel-buffering") == b"no"


@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        pytest.param(scope(), PUBLIC, id="unauthenticated"),
        pytest.param(as_caller(OWNER), OWNER_ONLY, id="the-owner"),
        pytest.param(as_caller("someone@else.com"), PUBLIC, id="another-user"),
    ],
)
async def test_each_caller_receives_the_half_of_the_last_scan_they_are_entitled_to(
    published: Broadcast, caller: HttpScope, expected: str
) -> None:
    # Also the "without waiting" case: the payload is already published, so it arrives on
    # connect rather than at the next scan.
    _, body = await connect(published, caller, take=2)

    assert isinstance(body, ResponseBody)
    assert body.body == f"data: {expected}\n\n".encode()


async def test_each_frame_is_its_own_chunk_so_the_client_sees_it_when_it_happens(
    published: Broadcast,
) -> None:
    _, body = await connect(published, scope(), take=2)

    assert isinstance(body, ResponseBody)
    assert body.more_body is True


async def test_a_connection_that_arrives_before_the_first_scan_receives_the_empty_payload() -> None:
    # The page renders whatever it is given, so an empty payload is a legitimate first
    # message: waiting for the first scan instead would leave the shell blank for a second.
    _, body = await connect(Broadcast(), scope(), take=2)

    assert isinstance(body, ResponseBody)
    assert body.body == b"data: {}\n\n"
