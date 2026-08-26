from __future__ import annotations

import re

import pytest
from without_asgi import headers

from exe_dev_atlas.listeners import ROUTED_PORTS
from exe_dev_atlas.page import SCRIPT_URL
from exe_dev_atlas.page import STYLESHEET_URL
from exe_dev_atlas.page import page_response
from exe_dev_atlas.page import shell

# Every id the script looks up with getElementById. The shell's whole job is to be the
# skeleton those writes land in, so one missing id is a page that renders blank with no
# server-side error to notice.
REQUIRED_IDS = ("ports", "empty", "state", "workspaces", "favicon", "host")


@pytest.mark.parametrize("element_id", REQUIRED_IDS)
def test_the_shell_carries_every_id_the_script_writes_into(element_id: str) -> None:
    assert f'id="{element_id}"' in shell()


def test_the_shell_links_the_stylesheet_and_the_script() -> None:
    rendered = shell()

    assert f'href="{STYLESHEET_URL}"' in rendered
    assert f'src="{SCRIPT_URL}"' in rendered


def test_the_script_is_deferred_so_it_runs_after_the_skeleton_exists() -> None:
    # It looks its elements up at top level, so running it during head parsing would find
    # nothing. `defer` is what makes placing it in the head safe.
    assert re.search(r"<script[^>]*\bdefer\b", shell())


def test_the_empty_state_names_the_range_the_proxy_actually_forwards() -> None:
    # Derived from ROUTED_PORTS rather than typed twice, so widening the range cannot leave
    # the page telling the reader something the scanner no longer does.
    assert f"({ROUTED_PORTS.start}-{ROUTED_PORTS.stop - 1})" in shell()


def test_the_rows_and_the_empty_notice_start_hidden_or_empty() -> None:
    rendered = shell()

    # Nothing is known until the first event arrives, so the shell must not assert either
    # "here are your ports" or "nothing is listening" before then.
    assert re.search(r'<ul id="ports"></ul>', rendered)
    assert re.search(r'<p id="empty" hidden>', rendered)


def test_the_workspaces_nav_starts_hidden_since_the_vscode_link_is_owner_only() -> None:
    assert re.search(r'<nav id="workspaces" hidden></nav>', shell())


def test_the_document_declares_a_doctype_and_a_language() -> None:
    rendered = shell()

    assert rendered.startswith("<!doctype html>")
    assert '<html lang="en">' in rendered


def test_the_favicon_starts_as_an_empty_data_uri_rather_than_a_fetch() -> None:
    # Replaced by the VM's emoji when the first event lands. A real href here would send
    # every visitor after a /favicon.ico that does not exist.
    assert '<link id="favicon" rel="icon" href="data:,">' in shell()


class TestPageResponse:
    def test_the_response_is_html_and_successful(self) -> None:
        response = page_response()

        assert response.status == 200
        assert headers.first(response.headers, b"content-type") == b"text/html; charset=utf-8"

    def test_the_shell_is_not_stored_by_caches(self) -> None:
        # It links two assets whose names carry no fingerprint and holds the ids the script
        # depends on, so a cached shell beside a fresh script is the combination that breaks
        # without any error to see.
        assert headers.first(page_response().headers, b"cache-control") == b"no-store"

    def test_the_body_is_the_rendered_shell(self) -> None:
        assert page_response().body == shell().encode()

    def test_the_response_is_the_same_value_every_time_it_is_built(self) -> None:
        # Nothing per-request goes into it, which is what lets the app build it once at
        # startup and serve that value to everyone.
        assert page_response() == page_response()
