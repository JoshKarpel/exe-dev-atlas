# The web app manifest, which is what makes the page installable as an app of its own.
#
# Like the shell, it varies only with which VM this is, so it is rendered once per answer from
# reflection and served from that value. There is no service worker: the page is a live view
# of the machine, and anything a worker could serve with the network gone would be a listing
# that reads as current while describing a box nobody could reach.

from __future__ import annotations

import json
from typing import Final
from urllib.parse import quote

from without_asgi import Content
from without_asgi import Response
from without_html import element
from without_html import render
from without_html import svg

from exe_dev_atlas.reflection import Reflection

MANIFEST_URL: Final = "/manifest.webmanifest"
MANIFEST_MEDIA_TYPE: Final = b"application/manifest+json"


def icon_glyph(vm: Reflection) -> str:
    """
    What the installed app's icon draws: the VM's emoji, or the first letter of its name.

    The tab's favicon can go blank for a VM with no emoji, but an installed app cannot: a
    manifest with no usable icon is not installable at all. The initial is the fallback rather
    than a fixed glyph because the emoji's whole job is telling one VM's window from
    another's, and a fixed glyph would make every emoji-less VM's app look the same.
    """
    return vm.emoji or vm.name[0].upper()


def icon_uri(vm: Reflection) -> str:
    """
    The icon as an SVG data URI, the same drawing `atlas.js` makes for the favicon.

    Inline rather than a route of its own, because the browser fetches manifest icons on its
    own terms, and whether it sends a private VM's login cookie with them is not ours to
    decide. A data URI has nothing to fetch. One SVG with `sizes: any`
    satisfies Chrome's 192px and 512px requirement, since it scales to both.
    """
    drawing = svg(
        attrs={"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 100 100"},
        children=element(
            "text",
            attrs={
                "x": "50",
                "y": "50",
                "font-size": "88",
                "text-anchor": "middle",
                "dominant-baseline": "central",
            },
            children=icon_glyph(vm),
        ),
    )
    return f"data:image/svg+xml,{quote(render(drawing))}"


def manifest(vm: Reflection) -> dict[str, object]:
    """
    The manifest document, named for the VM so each installed atlas says which box it is.

    `id` is stated rather than left to default to `start_url`, so the installed app's identity
    is pinned to this origin's root and survives any later change to where it starts. Every
    VM is its own origin, so two VMs' atlases install as two apps rather than one.
    """
    return {
        "id": "/",
        "name": vm.name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "icons": [{"src": icon_uri(vm), "sizes": "any", "type": "image/svg+xml"}],
    }


def manifest_response(vm: Reflection) -> Response:
    """
    The manifest as a finished response, built once by the caller and served from that value.

    `no-store` for the shell's reason: it changes when the VM is renamed, and a cached copy
    would keep the installed app's name and icon on the old answer.
    """
    return Response.from_content(
        200,
        Content(json.dumps(manifest(vm)).encode(), ((b"content-type", MANIFEST_MEDIA_TYPE),)),
        headers=((b"cache-control", b"no-store"),),
    )
