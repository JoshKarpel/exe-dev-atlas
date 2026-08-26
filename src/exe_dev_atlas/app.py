# The composition root: what is constructed once, what is held for the process's life, and
# how the three routes reach it.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from without_asgi import ASGIApp
from without_asgi import Event
from without_asgi import HttpScope
from without_asgi import Response
from without_asgi import ServerSentEvent
from without_asgi import event_stream
from without_asgi import inventory
from without_asgi import make_asgi_app
from without_asgi import with_heartbeat
from without_async import background_task
from without_async import sleep_forever
from without_http import ConnectionPool
from without_http import serving
from without_web import Reply
from without_web import Router
from without_web import get
from without_web import handle
from without_web import http_scope
from without_web import static_files

from exe_dev_atlas import page
from exe_dev_atlas import reflection
from exe_dev_atlas.listeners import home_directory
from exe_dev_atlas.listeners import socket_statistics_command
from exe_dev_atlas.scan import Broadcast
from exe_dev_atlas.scan import scan_forever

STATIC_ROOT: Final = Path(__file__).parent / "static"
STATIC_PREFIX: Final = "/static"

# exe.dev's proxy adds this from the authenticated session, and omits it entirely when the
# caller is unauthenticated, which is why the comparison below fails closed.
#
# The proxy is the only thing authenticating anyone here, so this is a claim about the hop and
# not about the request. Anything that reaches this port without making that hop, another user
# on the box, an SSH tunnel, sends whatever address it likes and is believed. That is the
# boundary the owner-only half rests on, and the README says so where somebody deciding how to
# share a VM will read it.
CALLER_EMAIL_HEADER: Final = b"x-exedev-email"

# exe.dev fronts the VM with a proxy, and this is how the nginx family is told not to hold a
# response: without it the proxy buffers events until the response ends, which for a stream
# held open for as long as the page is, is never. `event_stream` leaves it to the caller,
# since it is one vendor's deployment policy rather than a property of the format.
UNBUFFERED: Final = ((b"x-accel-buffering", b"no"),)


@dataclass(frozen=True, slots=True)
class Atlas:
    """
    Everything a request handler is allowed to see, assembled once before the first one.

    The owner's address is resolved at startup rather than per request: it is a remote call,
    it decides an authorization question, and the answer changes about never. A box whose
    reflection lookup failed serves the public view to everyone until it is restarted, which
    is the safe direction to fail.
    """

    broadcast: Broadcast
    owner_email: str
    page: Response


def is_owner(scope: HttpScope, owner_email: str) -> bool:
    """
    Whether the caller is the VM's owner, per exe.dev's own authentication.

    The *last* value wins where the header arrives more than once. A proxy that appends
    rather than replaces leaves whatever the client sent first in the list, so reading the
    first one would let a caller name themselves by sending the header twice.

    Decoded as UTF-8, which is what the address was encoded as on the way in. Reading it as
    latin-1 mangles every non-ASCII address into one that matches nobody, which fails in the
    safe direction and locks the owner out of their own box for good.

    Both sides must be non-empty. Reflection failing at startup and an unauthenticated
    caller both produce "", and `"" == ""` would otherwise disclose session names to
    everyone at exactly the moment this process knows least.
    """
    caller = ""
    for key, value in scope.headers:
        if key.lower() == CALLER_EMAIL_HEADER:
            caller = value.decode("utf-8", "replace")
    owner = owner_email.strip().casefold()
    return bool(owner) and caller.strip().casefold() == owner


async def payloads(broadcast: Broadcast, *, owner: bool) -> AsyncIterator[ServerSentEvent]:
    """
    Each payload the scan publishes, from whatever it holds now, as the frame that carries it.

    Unnamed, so the page's `onmessage` receives it; the browser has one kind of news to hear
    and naming it would route it to a listener nothing registers.
    """
    seen = -1
    while True:
        seen, payload = await broadcast.wait(seen, is_owner=owner)
        yield Event(data=payload)


async def events(atlas: Atlas, scope: HttpScope) -> Reply:
    """
    One SSE connection, held open until the client goes away.

    Which of the two payloads this connection receives is decided once, here, because this
    is the only place that holds the caller's headers; the scan loop serializes both and
    knows about neither.

    A quiet machine publishes nothing for as long as it stays quiet, so the stream is
    heartbeated: a comment every so often keeps an intermediary from reaping a connection
    the page is still holding.
    """
    return event_stream(
        with_heartbeat(payloads(atlas.broadcast, owner=is_owner(scope, atlas.owner_email))),
        headers=UNBUFFERED,
    )


async def index(atlas: Atlas) -> Response:
    return atlas.page


def build_router() -> Router[Atlas]:
    """
    The three routes, over an inventory of the static tree walked once, here.

    `inventory` is not a directory mount: it walks the tree at this call and answers every
    later request out of the resulting mapping, so a request never contributes a filesystem
    path and there is no traversal to get wrong. The cost is the one in the name, that
    nothing may write into `static/` while the process runs.
    """
    assets = inventory(STATIC_ROOT)
    return Router(
        routes=(
            get("/")(index),
            static_files(STATIC_PREFIX, assets),
            get("/events", http_scope())(events),
        ),
        fallback=handle(http_scope(), fn=_not_found),
    )


async def _not_found(_atlas: Atlas, _scope: HttpScope) -> Response:
    return Response(
        status=404,
        headers=((b"content-type", b"text/plain; charset=utf-8"),),
        body=b"not found\n",
    )


def build_app(port: int) -> ASGIApp:
    """
    The ASGI app, with the scan loop bound to its lifespan.

    The scan runs for exactly as long as the server does: `background_task` starts it on
    entry and cancels it on exit, so there is no thread to outlive a shutdown and no
    lifetime to manage by hand.
    """
    socket_statistics = socket_statistics_command()
    router = build_router()

    @asynccontextmanager
    async def lifespan() -> AsyncIterator[Atlas]:
        async with ConnectionPool() as pool:
            vm = await reflection.read_vm(pool)
            atlas = Atlas(
                broadcast=Broadcast(),
                owner_email=await reflection.read_owner_email(pool),
                page=page.page_response(),
            )
            scan = scan_forever(
                atlas.broadcast,
                pool,
                socket_statistics,
                port,
                vm,
                reflection.vscode_url(vm.name, home_directory()),
            )
            async with background_task(scan):
                yield atlas

    return make_asgi_app(lifespan, http=router.dispatch)


async def serve(port: int, host: str = "127.0.0.1") -> None:
    """Run until cancelled, which for the CLI means until a signal stops the process."""
    async with serving(build_app(port), host=host, port=port):
        await sleep_forever()


def serve_until_stopped(port: int, host: str = "127.0.0.1") -> None:
    try:
        asyncio.run(serve(port, host))
    except KeyboardInterrupt:
        return
