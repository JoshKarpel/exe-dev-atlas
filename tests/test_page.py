from __future__ import annotations

import re

import pytest
from without_asgi import headers

from exe_dev_atlas.app import STATIC_ROOT
from exe_dev_atlas.listeners import ROUTED_PORTS
from exe_dev_atlas.page import SCRIPT_URL
from exe_dev_atlas.page import STYLESHEET_URL
from exe_dev_atlas.page import page_response
from exe_dev_atlas.page import shell
from exe_dev_atlas.reflection import Reflection

SCRIPT_SOURCE = (STATIC_ROOT / "atlas.js").read_text()
STYLESHEET_SOURCE = (STATIC_ROOT / "atlas.css").read_text()

# What reflection answered with, which the shell renders into the tab's title and the heading.
VM = Reflection(name="cumulus", emoji="\N{THOUGHT BALLOON}")

# Every id the script looks up, read out of the script rather than listed again here. The
# shell's whole job is to be the skeleton those writes land in, so one missing id is a page
# that renders blank with no server-side error to notice, and a list maintained by hand does
# not grow when somebody adds a lookup.
SCRIPT_IDS = sorted(
    {
        found
        for match in re.finditer(
            r"""getElementById\(["']([^"']+)["']\)|querySelector(?:All)?\(["']#([^"'\s]+)["']\)""",
            SCRIPT_SOURCE,
        )
        for found in match.groups()
        if found
    }
)

# The ids the shell actually renders, read the same way.
SHELL_IDS = sorted(set(re.findall(r'(?:^|\s)id="([^"]+)"', shell(VM))))


def test_the_script_looks_its_elements_up_the_way_this_file_reads_them() -> None:
    # The other direction of the parametrized test below, and the reason a lookup spelled some
    # way the pattern above cannot read is not a silent hole: such a lookup drops out of
    # SCRIPT_IDS, the parametrized test stops asking about that id, and this one is left with a
    # shell id nothing accounts for. An empty set of lookups fails here for the same reason.
    assert SCRIPT_IDS == SHELL_IDS


@pytest.mark.parametrize("element_id", SCRIPT_IDS)
def test_the_shell_carries_every_id_the_script_writes_into(element_id: str) -> None:
    assert f'id="{element_id}"' in shell(VM)


def test_the_shell_links_the_stylesheet_and_the_script() -> None:
    rendered = shell(VM)

    assert f'href="{STYLESHEET_URL}"' in rendered
    assert f'src="{SCRIPT_URL}"' in rendered


def test_the_script_is_deferred_so_it_runs_after_the_skeleton_exists() -> None:
    # It looks its elements up at top level, so running it during head parsing would find
    # nothing. `defer` is what makes placing it in the head safe.
    assert re.search(r"<script[^>]*\bdefer\b", shell(VM))


def test_the_empty_state_names_the_range_the_proxy_actually_forwards() -> None:
    # Derived from ROUTED_PORTS rather than typed twice, so widening the range cannot leave
    # the page telling the reader something the scanner no longer does.
    assert f"({ROUTED_PORTS.start}-{ROUTED_PORTS.stop - 1})" in shell(VM)


def test_the_footer_names_the_digits_the_script_actually_binds() -> None:
    # The script owns the range and the shell only names it, so the two are pinned here rather
    # than derived: widening HOTKEYS without touching the footer would have the page tell the
    # reader about fewer keys than it answers to.
    hotkeys = re.search(r'HOTKEYS = "([^"]+)"', SCRIPT_SOURCE)
    assert hotkeys
    digits = hotkeys.group(1)

    assert f"<kbd>{digits[0]}</kbd>\N{EN DASH}<kbd>{digits[-1]}</kbd>" in shell(VM)


def test_the_rows_and_the_empty_notice_start_hidden_or_empty() -> None:
    rendered = shell(VM)

    # Nothing is known until the first event arrives, so the shell must not assert either
    # "here are your ports" or "nothing is listening" before then.
    assert re.search(r'<ul id="ports"></ul>', rendered)
    assert re.search(r'<p id="empty" hidden>', rendered)


def test_the_workspaces_nav_starts_hidden_until_a_payload_says_there_is_a_link() -> None:
    assert re.search(r'<nav id="workspaces" hidden></nav>', shell(VM))


def test_the_stylesheet_lets_the_hidden_attribute_win_over_the_workspaces_nav() -> None:
    # Read out of the stylesheet because nothing here renders a page: the UA's
    # `[hidden] { display: none }` loses to an id selector on origin alone, so an
    # unconditional `#workspaces { display: flex }` would lay the nav out with `hidden` set,
    # which is every page served with the VS Code link turned off.
    assert re.search(r"#workspaces:not\(\[hidden\]\)\s*\{", STYLESHEET_SOURCE)


def test_the_tab_is_named_after_the_vm_before_any_event_arrives() -> None:
    # Reflection is read before the server binds anything, so the name is in the shell rather
    # than written by the first payload: a tab opened and left alone still says which box it
    # is, and so does one whose stream never connects.
    assert f"<title>{VM.name}</title>" in shell(VM)


def test_the_heading_is_the_vm_name_and_the_emoji_is_left_out_of_it() -> None:
    # The document's one `h1` is what a screen reader announces as the page's heading, and an
    # emoji there announces as its own Unicode name. The glyph is decoration beside it.
    rendered = shell(VM)

    assert f'<h1 id="vm">{VM.name}</h1>' in rendered
    assert f'<span id="emblem" aria-hidden="true">{VM.emoji}</span>' in rendered


def test_a_vm_with_no_emoji_renders_no_gap_where_one_would_have_been() -> None:
    # The emoji is optional in reflection's answer, so an empty emblem must not sit in the
    # header holding its own gap open.
    assert '<span id="emblem" aria-hidden="true" hidden></span>' in shell(Reflection(name="nimbus", emoji=""))


def test_the_document_declares_a_doctype_and_a_language() -> None:
    rendered = shell(VM)

    assert rendered.startswith("<!doctype html>")
    assert '<html lang="en">' in rendered


def test_the_favicon_starts_as_an_empty_data_uri_rather_than_a_fetch() -> None:
    # Replaced by the VM's emoji when the first event lands. A real href here would send
    # every visitor after a /favicon.ico that does not exist.
    assert '<link id="favicon" rel="icon" href="data:,">' in shell(VM)


class TestPageResponse:
    def test_the_response_is_html_and_successful(self) -> None:
        response = page_response(VM)

        assert response.status == 200
        assert headers.first(response.headers, b"content-type") == b"text/html; charset=utf-8"

    def test_the_shell_is_not_stored_by_caches(self) -> None:
        # It links two assets whose names carry no fingerprint and holds the ids the script
        # depends on, so a cached shell beside a fresh script is the combination that breaks
        # without any error to see.
        assert headers.first(page_response(VM).headers, b"cache-control") == b"no-store"

    def test_the_body_is_the_rendered_shell(self) -> None:
        assert page_response(VM).body == shell(VM).encode()

    def test_the_response_is_the_same_value_every_time_it_is_built(self) -> None:
        # Nothing per-request goes into it, which is what lets the app build it once at
        # startup and serve that value to everyone.
        assert page_response(VM) == page_response(VM)
