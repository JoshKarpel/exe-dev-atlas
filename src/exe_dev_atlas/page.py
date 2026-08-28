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
from exe_dev_atlas.reflection import Reflection

STYLESHEET_URL: Final = "/static/atlas.css"
SCRIPT_URL: Final = "/static/atlas.js"


def shell(vm: Reflection) -> str:
    """
    The document, as a string, with no per-request content in it.

    Which VM this is is not per-request: reflection is read before the server takes traffic
    and re-read only when the answer might have changed, so the tab and the heading name the
    box before the first event rather than being written by it.
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
                            title(children=vm.name),
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
                                        # The VM's emoji, which is what makes two of these
                                        # tabs tell themselves apart at a glance. Decoration
                                        # beside the name rather than a second reading of it,
                                        # so it is kept out of the accessibility tree, and
                                        # dropped outright where reflection answered with no
                                        # emoji, so the header never opens on a blank space.
                                        span(
                                            attrs={"id": "emblem", "aria-hidden": "true", "hidden": not vm.emoji},
                                            children=vm.emoji,
                                        ),
                                        # The VM's own name is the page's heading: it is what
                                        # the reader is here to identify, and the emoji beside
                                        # it would announce as its own Unicode name. Under it
                                        # the host the browser actually used, which through a
                                        # tunnel is `localhost` and names no VM at all.
                                        h1(attrs={"id": "vm"}, children=vm.name),
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


def page_response(vm: Reflection) -> Response:
    """
    The shell as a finished response, built once by the caller and served from that value.

    `no-store` because the shell links two assets whose names carry no fingerprint and holds
    the element ids the script depends on: a cached shell paired with a fresh script is the
    one combination that would break silently.
    """
    return Response.from_content(
        200,
        html_content(shell(vm)),
        headers=((b"cache-control", b"no-store"),),
    )
