from __future__ import annotations

import json
from urllib.parse import unquote

import pytest
from without_asgi import headers

from exe_dev_atlas.manifest import icon_glyph
from exe_dev_atlas.manifest import icon_uri
from exe_dev_atlas.manifest import manifest
from exe_dev_atlas.manifest import manifest_response
from exe_dev_atlas.reflection import Reflection

VM = Reflection(name="cumulus", emoji="\N{THOUGHT BALLOON}")
PLAIN = Reflection(name="nimbus", emoji="")

SVG_PREFIX = "data:image/svg+xml,"


def drawn(vm: Reflection) -> str:
    """The SVG the icon's data URI carries, decoded back to markup."""
    uri = icon_uri(vm)
    assert uri.startswith(SVG_PREFIX)
    return unquote(uri.removeprefix(SVG_PREFIX))


class TestIconGlyph:
    def test_a_vm_with_an_emoji_is_drawn_as_that_emoji(self) -> None:
        assert icon_glyph(VM) == VM.emoji

    def test_a_vm_with_no_emoji_is_drawn_as_its_capitalized_initial(self) -> None:
        # A manifest with no usable icon is not installable, so the blank the favicon falls
        # back to is not available here.
        assert icon_glyph(PLAIN) == "N"


class TestIconUri:
    def test_the_icon_draws_the_glyph(self) -> None:
        assert f">{VM.emoji}</text>" in drawn(VM)

    def test_the_icon_is_a_standalone_svg_document(self) -> None:
        # Loaded as an image rather than inlined in HTML, so the namespace is what makes it
        # SVG at all.
        assert drawn(VM).startswith('<svg xmlns="http://www.w3.org/2000/svg"')

    def test_markup_in_a_name_is_escaped_rather_than_drawn_into_the_svg(self) -> None:
        # The initial is the name's first character, which reflection does not promise is a
        # letter, and an unescaped `<` would end the drawing early.
        assert ">&lt;</text>" in drawn(Reflection(name="<vm>", emoji=""))


class TestManifest:
    def test_the_app_is_named_for_the_vm(self) -> None:
        assert manifest(VM)["name"] == VM.name

    def test_the_app_opens_in_its_own_window(self) -> None:
        # Chrome's installability criteria accept `standalone`, `fullscreen`, `minimal-ui`
        # and `window-controls-overlay`; `browser` makes it a bookmark.
        assert manifest(VM)["display"] == "standalone"

    def test_the_app_starts_and_is_identified_at_the_root(self) -> None:
        document = manifest(VM)

        assert (document["id"], document["start_url"], document["scope"]) == ("/", "/", "/")

    def test_the_one_icon_scales_to_every_size_the_browser_asks_for(self) -> None:
        # Chrome needs a 192px and a 512px icon, which a single SVG declared `any` covers.
        assert manifest(VM)["icons"] == [{"src": icon_uri(VM), "sizes": "any", "type": "image/svg+xml"}]


class TestManifestResponse:
    def test_the_response_is_a_manifest_and_successful(self) -> None:
        response = manifest_response(VM)

        assert response.status == 200
        assert headers.first(response.headers, b"content-type") == b"application/manifest+json"

    def test_the_manifest_is_not_stored_by_caches(self) -> None:
        # A rename changes it, and a cached copy would keep the installed app on the old name.
        assert headers.first(manifest_response(VM).headers, b"cache-control") == b"no-store"

    @pytest.mark.parametrize("vm", [VM, PLAIN])
    def test_the_body_is_the_manifest_as_json(self, vm: Reflection) -> None:
        assert json.loads(manifest_response(vm).body) == manifest(vm)
