# Server-sent event framing, as pure functions of the data being sent.
#
# Nothing here knows about HTTP beyond the header tuple: framing an event is a string
# transformation, and the transport is the caller's. That split is why this is testable as a
# table and why it is a candidate for `without-asgi` once its shape has settled against a
# real consumer.

from __future__ import annotations

from typing import Final

# The `text/event-stream` media type carries no charset parameter: the format is defined as
# UTF-8, and a browser ignores anything said to the contrary.
SSE_HEADERS: Final = (
    (b"content-type", b"text/event-stream"),
    (b"cache-control", b"no-cache"),
    # Names an nginx-specific behaviour, and costs nothing where no nginx is in the path:
    # without it a buffering proxy holds every event until the stream it is never going to
    # close, ends.
    (b"x-accel-buffering", b"no"),
)

# A frame with no data field at all, which the spec requires a client to ignore. It exists
# to put bytes on a connection that has had nothing to say for a while, so an idle timeout
# somewhere in the path does not decide the stream is dead.
COMMENT: Final = b": heartbeat\n\n"


def frame(data: str, *, event: str = "") -> bytes:
    r"""
    One event, ready to write.

    The per-line split is the whole of it and the reason this is not an f-string at the call
    site: a newline inside `data` terminates the field, so a payload containing one arrives
    truncated at every line but the first unless each gets its own `data:`. The client
    rejoins them with `\n`, so the round trip is exact.

    An empty `data` still emits one empty `data:` line rather than none, which keeps this
    distinguishable from a comment.
    """
    lines = [f"event: {event}"] if event else []
    lines += [f"data: {line}" for line in data.split("\n")]
    return ("\n".join(lines) + "\n\n").encode()
