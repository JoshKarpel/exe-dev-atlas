from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final

import pytest
from without_asgi import Asgi
from without_asgi import HttpScope
from without_http import ConnectionPool

from exe_dev_atlas.app import CALLER_EMAIL_HEADER


@pytest.fixture
async def pool() -> AsyncIterator[ConnectionPool]:
    """An open connection pool, torn down with the test that asked for one."""
    async with ConnectionPool() as open_pool:
        yield open_pool


# One `ss --tcp --listening --numeric --processes --no-header` line, with the columns it
# actually emits: State, Recv-Q, Send-Q, Local, Peer, Process. Padded as `ss` pads them, so
# the column split is exercised against the spacing it really has to survive.
LISTEN: Final = 'LISTEN 0      4096   {local}      0.0.0.0:*    users:(("{name}",pid={pid},fd=3))'


def line(local: str, name: str = "server", pid: int = 4711) -> str:
    """One listening row as `ss` would report it."""
    return LISTEN.format(local=local, name=name, pid=pid)


def scope(*headers: tuple[bytes, bytes]) -> HttpScope:
    """A request for the events route, carrying whatever headers the caller sent."""
    return HttpScope(
        asgi=Asgi(version="3.0", spec_version="2.3"),
        http_version="1.1",
        method="GET",
        scheme="http",
        path="/events",
        raw_path=b"/events",
        query_string=b"",
        root_path="",
        headers=headers,
        client=("127.0.0.1", 54321),
        server=("127.0.0.1", 8000),
        extensions={},
    )


def as_caller(email: str) -> HttpScope:
    """A request the proxy authenticated as `email`."""
    return scope((CALLER_EMAIL_HEADER, email.encode()))
