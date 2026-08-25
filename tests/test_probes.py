from __future__ import annotations

import pytest

from exe_dev_atlas.probes import describe_response
from exe_dev_atlas.probes import format_probe_title

# A distinct, non-default timestamp, so a probe that lost track of when it ran shows up.
PROBED_AT = 1_787_000_123.5


class TestTitleExtraction:
    def test_a_plain_title_is_taken_verbatim(self) -> None:
        assert format_probe_title("<html><head><title>Grafana</title></head></html>") == "Grafana"

    def test_entities_in_a_title_are_unescaped(self) -> None:
        assert format_probe_title("<title>Jenkins &amp; friends</title>") == "Jenkins & friends"

    def test_whitespace_and_newlines_inside_a_title_collapse_to_single_spaces(self) -> None:
        assert format_probe_title("<title>\n  My   Long\n  Dashboard\n</title>") == "My Long Dashboard"

    def test_a_title_tag_carrying_attributes_is_still_matched(self) -> None:
        assert format_probe_title('<title data-turbo="false">Rails</title>') == "Rails"

    def test_the_match_is_case_insensitive(self) -> None:
        assert format_probe_title("<TITLE>Legacy</TITLE>") == "Legacy"

    def test_the_first_title_wins_when_a_page_carries_more_than_one(self) -> None:
        assert format_probe_title("<title>First</title><title>Second</title>") == "First"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("", id="empty"),
            pytest.param("<html><body>no title here</body></html>", id="no-title-tag"),
            pytest.param('{"status": "ok"}', id="json"),
            pytest.param("<title>unclosed", id="unclosed-tag"),
        ],
    )
    def test_a_body_with_no_readable_title_yields_an_empty_string(self, body: str) -> None:
        assert format_probe_title(body) == ""


class TestDescribeResponse:
    def test_an_html_response_carries_its_title_through(self) -> None:
        probe = describe_response(200, "text/html; charset=utf-8", "nginx/1.25", b"<title>Kibana</title>", PROBED_AT)

        assert probe.is_http is True
        assert probe.status == 200
        assert probe.title == "Kibana"
        assert probe.server == "nginx/1.25"
        assert probe.at == PROBED_AT

    @pytest.mark.parametrize(
        "content_type",
        [
            pytest.param("application/json", id="json"),
            pytest.param("text/plain", id="plain-text"),
            pytest.param("", id="none-declared"),
            pytest.param("application/octet-stream", id="binary"),
        ],
    )
    def test_a_non_html_response_is_not_scanned_for_a_title(self, content_type: str) -> None:
        # The bytes deliberately *do* contain a title, so this fails if the content type is
        # ignored and the regex is run over everything.
        probe = describe_response(204, content_type, "uvicorn", b"<title>Not Mine</title>", PROBED_AT)

        assert probe.title == ""
        assert probe.is_http is True

    def test_a_content_type_is_matched_case_insensitively(self) -> None:
        probe = describe_response(200, "TEXT/HTML", "", b"<title>Shouty</title>", PROBED_AT)

        assert probe.title == "Shouty"

    @pytest.mark.parametrize("status", [401, 403, 404, 500, 502])
    def test_an_error_status_still_counts_as_a_web_server(self, status: int) -> None:
        # A 404 or a 401 is still something worth offering a link to; only a socket that
        # never answers is not HTTP.
        probe = describe_response(status, "text/html", "caddy", b"", PROBED_AT)

        assert probe.is_http is True
        assert probe.status == status

    def test_a_fresh_probe_records_one_attempt(self) -> None:
        assert describe_response(200, "text/html", "", b"", PROBED_AT).attempts == 1
