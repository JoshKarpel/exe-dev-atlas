from __future__ import annotations

import pytest

from exe_dev_atlas.sse import COMMENT
from exe_dev_atlas.sse import SSE_HEADERS
from exe_dev_atlas.sse import frame


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param("hello", b"data: hello\n\n", id="one-line"),
        pytest.param('{"port": 3456}', b'data: {"port": 3456}\n\n', id="json"),
        pytest.param("", b"data: \n\n", id="empty-still-emits-a-field"),
    ],
)
def test_a_single_line_payload_becomes_one_data_field(data: str, expected: bytes) -> None:
    assert frame(data) == expected


def test_every_line_of_a_multiline_payload_gets_its_own_data_field() -> None:
    # The whole reason this is a function and not an f-string: a bare newline inside the
    # payload terminates the field, so without the split a client sees only "first".
    assert frame("first\nsecond\nthird") == b"data: first\ndata: second\ndata: third\n\n"


def test_a_trailing_newline_becomes_a_trailing_empty_data_field() -> None:
    # Not cosmetic: the client rejoins fields with "\n", so dropping the empty one would
    # hand back "text" where "text\n" was sent.
    assert frame("text\n") == b"data: text\ndata: \n\n"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("plain", id="no-newlines"),
        pytest.param("two\nlines", id="one-newline"),
        pytest.param("\nleading", id="leading-newline"),
        pytest.param("trailing\n", id="trailing-newline"),
        pytest.param("a\n\nb", id="blank-line-between"),
        pytest.param('{"vm": "cumulus", "note": "line\\nbreak"}', id="json-with-escaped-newline"),
    ],
)
def test_a_payload_survives_the_round_trip_a_client_performs(payload: str) -> None:
    """Reassemble the way an EventSource does: take each data field, join with newlines."""
    body = frame(payload).decode().removesuffix("\n\n")
    fields = [line.removeprefix("data: ") for line in body.split("\n")]

    assert "\n".join(fields) == payload


def test_a_named_event_precedes_its_data() -> None:
    assert frame("payload", event="listeners") == b"event: listeners\ndata: payload\n\n"


def test_an_unnamed_event_emits_no_event_field_at_all() -> None:
    # An absent `event` means the client's `onmessage` fires, which is what the page listens
    # on; emitting `event: ` would route it to a named listener nothing registers.
    assert b"event:" not in frame("payload")


def test_a_comment_carries_no_data_field_so_a_client_ignores_it() -> None:
    assert COMMENT.startswith(b":")
    assert b"data:" not in COMMENT
    assert COMMENT.endswith(b"\n\n")


def test_the_headers_declare_the_stream_unbuffered_and_uncached() -> None:
    headers = dict(SSE_HEADERS)

    assert headers[b"content-type"] == b"text/event-stream"
    assert headers[b"cache-control"] == b"no-cache"
    # Without this an nginx in the path holds every event until a stream that never ends.
    assert headers[b"x-accel-buffering"] == b"no"


def test_the_content_type_states_no_charset() -> None:
    # `text/event-stream` is defined as UTF-8 and a browser ignores a charset parameter,
    # so stating one would be noise a reader could mistake for a knob.
    assert b"charset" not in dict(SSE_HEADERS)[b"content-type"]
