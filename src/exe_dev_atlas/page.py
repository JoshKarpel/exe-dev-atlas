# The document shell, which is the whole of the server-rendered HTML.
#
# Everything that varies arrives over SSE and is written into this skeleton by the browser, so
# the shell is a constant: rendered once at startup and served from that value. What is here
# is only what the page needs before its first event, which is the element ids the script
# looks up and the two asset links.

from __future__ import annotations

from typing import Final

from without_asgi import Response
from without_asgi import html_content
from without_html import DOCTYPE
from without_html import body
from without_html import footer
from without_html import h1
from without_html import head
from without_html import header
from without_html import html
from without_html import kbd
from without_html import link
from without_html import main
from without_html import meta
from without_html import nav
from without_html import p
from without_html import render
from without_html import script
from without_html import span
from without_html import title
from without_html import ul

from exe_dev_atlas.listeners import ROUTED_PORTS

STYLESHEET_URL: Final = "/static/atlas.css"
SCRIPT_URL: Final = "/static/atlas.js"


def shell(vm_name: str) -> str:
    """
    The document, as a string, with no per-request content in it.

    The VM's name is not per-request: reflection answers once at startup and the answer holds
    for the life of the process, so the tab is named before the first event rather than by it.
    """
    return render(
        [
            DOCTYPE,
            html(
                attrs={"lang": "en"},
                children=[
                    head(
                        children=[
                            meta(attrs={"charset": "utf-8"}),
                            meta(attrs={"name": "viewport", "content": "width=device-width, initial-scale=1"}),
                            # `atlas.js` rewrites this with the host the browser used where
                            # reflection named no VM, which is the one thing only the
                            # browser knows.
                            title(children=vm_name),
                            # An empty data URI, so the browser does not go asking for
                            # /favicon.ico before the VM's emoji arrives to replace it.
                            link(attrs={"id": "favicon", "rel": "icon", "href": "data:,"}),
                            link(attrs={"rel": "stylesheet", "href": STYLESHEET_URL}),
                            # `defer` rather than placing the script last in the body: it
                            # runs after parsing either way, and the head is where the
                            # page's dependencies belong.
                            script(attrs={"src": SCRIPT_URL, "defer": True}),
                        ]
                    ),
                    body(
                        children=main(
                            children=[
                                header(
                                    children=[
                                        # The VM's own emoji once the first event lands. The
                                        # product's name until then, and wherever reflection
                                        # answered with no emoji, so the heading is never a
                                        # blank space where a glyph should be.
                                        h1(attrs={"id": "emblem"}, children="Atlas"),
                                        # The VM's own name, and under it the host the
                                        # browser actually used, which through a tunnel is
                                        # `localhost` and names no VM at all.
                                        span(attrs={"id": "vm", "hidden": True}),
                                        span(attrs={"id": "host"}),
                                        span(attrs={"id": "state"}, children="connecting"),
                                    ]
                                ),
                                nav(attrs={"id": "workspaces", "hidden": True}),
                                ul(attrs={"id": "ports"}),
                                p(
                                    attrs={"id": "empty", "hidden": True},
                                    children=(
                                        f"Nothing listening on a proxied port "
                                        f"({ROUTED_PORTS.start}-{ROUTED_PORTS.stop - 1})."
                                    ),
                                ),
                                footer(
                                    children=[
                                        "Press ",
                                        kbd(children="1"),
                                        "\N{EN DASH}",
                                        kbd(children="9"),
                                        " to open a service. Updates live.",
                                    ]
                                ),
                            ]
                        )
                    ),
                ],
            ),
        ]
    )


def page_response(vm_name: str) -> Response:
    """
    The shell as a finished response, built once by the caller and served from that value.

    `no-store` because the shell links two assets whose names carry no fingerprint and holds
    the element ids the script depends on: a cached shell paired with a fresh script is the
    one combination that would break silently.
    """
    return Response.from_content(
        200,
        html_content(shell(vm_name)),
        headers=((b"cache-control", b"no-store"),),
    )
