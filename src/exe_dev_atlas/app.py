# The composition root: what is constructed once, what is held for the process's life, and
# how the three routes reach it.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from without_asgi import NOT_FOUND
from without_asgi import ASGIApp
from without_asgi import Event
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
from without_web import static_files

from exe_dev_atlas import page
from exe_dev_atlas import reflection
from exe_dev_atlas.listeners import home_directory
from exe_dev_atlas.listeners import read_listeners
from exe_dev_atlas.scan import Broadcast
from exe_dev_atlas.scan import scan_forever

STATIC_ROOT: Final = Path(__file__).parent / "static"
STATIC_PREFIX: Final = "/static"

# exe.dev fronts the VM with a proxy, and this is how the nginx family is told not to hold a
# response: without it the proxy buffers events until the response ends, which for a stream
# held open for as long as the page is, is never. `event_stream` leaves it to the caller,
# since it is one vendor's deployment policy rather than a property of the format.
UNBUFFERED: Final = ((b"x-accel-buffering", b"no"),)


@dataclass(frozen=True, slots=True)
class Atlas:
    """Everything a request handler is allowed to see, assembled once before the first one."""

    broadcast: Broadcast
    page: Response


async def payloads(broadcast: Broadcast) -> AsyncIterator[ServerSentEvent]:
    """
    Each payload the scan publishes, from whatever it holds now, as the frame that carries it.

    Unnamed, so the page's `onmessage` receives it; the browser has one kind of news to hear
    and naming it would route it to a listener nothing registers.
    """
    seen = -1
    while True:
        seen, payload = await broadcast.wait(seen)
        yield Event(data=payload)


async def events(atlas: Atlas) -> Reply:
    """
    One SSE connection, held open until the client goes away.

    Every connection is served the same payload, whoever is asking: the page is exactly as
    private as the VM it runs on, and the README says so where somebody deciding how to share
    a VM will read it.

    A quiet machine publishes nothing for as long as it stays quiet, so the stream is
    heartbeated: a comment every so often keeps an intermediary from reaping a connection
    the page is still holding.
    """
    return event_stream(with_heartbeat(payloads(atlas.broadcast)), headers=UNBUFFERED)


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
            get("/events")(events),
        ),
        fallback=handle(fn=_not_found),
    )


async def _not_found(_atlas: Atlas) -> Response:
    return NOT_FOUND


def build_app(port: int, *, vscode_link: bool = True) -> ASGIApp:
    """
    The ASGI app, with the scan loop bound to its lifespan.

    The scan runs for exactly as long as the server does: `background_task` starts it on
    entry and cancels it on exit, so there is no thread to outlive a shutdown and no
    lifetime to manage by hand.

    Whether to offer the VS Code link is settled here rather than carried any further in:
    the scan loop is handed a URL and an empty one is already how it says there is no link
    to offer, which is what a reflection lookup that did not answer produces anyway.
    """
    router = build_router()

    @asynccontextmanager
    async def lifespan() -> AsyncIterator[Atlas]:
        async with ConnectionPool() as pool:
            vm = await reflection.read_vm(pool)
            atlas = Atlas(broadcast=Broadcast(), page=page.page_response(vm.name))
            scan = scan_forever(
                atlas.broadcast,
                pool,
                read_listeners,
                port,
                vm,
                reflection.vscode_url(vm.name, home_directory()) if vscode_link else "",
            )
            async with background_task(scan):
                yield atlas

    return make_asgi_app(lifespan, http=router.dispatch)


async def serve(port: int, host: str = "127.0.0.1", *, vscode_link: bool = True) -> None:
    """Run until cancelled, which for the CLI means until a signal stops the process."""
    async with serving(build_app(port, vscode_link=vscode_link), host=host, port=port):
        await sleep_forever()


def serve_until_stopped(port: int, host: str = "127.0.0.1", *, vscode_link: bool = True) -> None:
    try:
        asyncio.run(serve(port, host, vscode_link=vscode_link))
    except KeyboardInterrupt:
        return
