from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

import pytest
from without_http import ConnectionPool
from without_http import ResponseBody
from without_http import ResponseTrailers

from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.probes import PROBE_MAX_ATTEMPTS
from exe_dev_atlas.probes import PROBE_MAX_BYTES
from exe_dev_atlas.probes import PROBE_REFRESH
from exe_dev_atlas.probes import PROBE_RETRY
from exe_dev_atlas.probes import Probe
from exe_dev_atlas.probes import describe_response
from exe_dev_atlas.probes import format_probe_title
from exe_dev_atlas.probes import probe_address
from exe_dev_atlas.probes import probe_interval
from exe_dev_atlas.probes import probe_port
from exe_dev_atlas.probes import probe_url
from exe_dev_atlas.probes import read_beginning

# A distinct, non-default timestamp, so a probe that lost track of when it ran shows up.
PROBED_AT = 1_787_000_123.5

type Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class Listen(Protocol):
    """Start a throwaway listener on an ephemeral port, and report the port it bound."""

    async def __call__(self, handle: Handler, host: str = ...) -> int: ...


def bound(port: int, *addresses: str, pid: int = 7788) -> Listener:
    """A listener holding `port` on the addresses given, as `parse_listeners` would report it."""
    return Listener(port=port, pid=pid, addresses=addresses or ("127.0.0.1",))


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


async def chunked(*items: bytes) -> AsyncGenerator[bytes | ResponseTrailers]:
    for item in items:
        yield item


class TestReadingTheBeginningOfABody:
    async def test_a_body_shorter_than_the_limit_arrives_whole(self) -> None:
        assert await read_beginning(ResponseBody(chunked(b"kib", b"ana")), 64) == b"kibana"

    async def test_a_body_longer_than_the_limit_is_cut_at_it(self) -> None:
        assert await read_beginning(ResponseBody(chunked(b"kib", b"ana")), 4) == b"kiba"

    async def test_the_chunks_past_the_limit_are_never_pulled(self) -> None:
        # The whole point: a `/` that streams never ends, so a read that keeps pulling costs
        # the probe timeout and everything the listener sent inside it.
        pulled = 0

        async def counting() -> AsyncGenerator[bytes | ResponseTrailers]:
            nonlocal pulled
            while True:
                pulled += 1
                yield b"x" * 16

        assert await read_beginning(ResponseBody(counting()), 32) == b"x" * 32
        assert pulled == 2


@pytest.fixture
async def listening() -> AsyncIterator[Listen]:
    """Start throwaway listeners on ephemeral ports, and take them all down afterwards."""
    servers: list[asyncio.Server] = []

    async def start(handle: Handler, host: str = "127.0.0.1") -> int:
        server = await asyncio.start_server(handle, host, 0)
        servers.append(server)
        port: int = server.sockets[0].getsockname()[1]
        return port

    yield start

    for server in servers:
        server.close()
        await server.wait_closed()


async def not_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A database socket's answer to `GET /`: its own protocol, then a hang-up."""
    await reader.read(1024)
    writer.write(b"-ERR unknown command 'GET'\r\n")
    await writer.drain()
    writer.close()


async def says_nothing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A port that accepts a connection and closes it without answering."""
    await reader.read(1024)
    writer.close()


async def serve_a_page(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """An ordinary web server, answering with a titled page."""
    await reader.read(1024)
    body = b"<html><head><title>Grafana</title></head></html>"
    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-length: %d\r\n\r\n" % len(body))
    writer.write(body)
    await writer.drain()
    writer.close()


async def streams_forever(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A page that is real HTTP and never ends: an event stream, a log tail, a video feed."""
    await reader.read(1024)
    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ntransfer-encoding: chunked\r\n\r\n")
    body = b"<title>Tailing</title>" + b"." * 8192
    # The probe stopping the read is what ends this, and to the server that is the client
    # hanging up mid-body, which is an ordinary way for a stream to end rather than a fault.
    with contextlib.suppress(ConnectionError):
        while True:
            writer.write(b"%x\r\n%s\r\n" % (len(body), body))
            await writer.drain()


class TestChoosingWhereToAsk:
    @pytest.mark.parametrize(
        ("addresses", "expected"),
        [
            pytest.param(("127.0.0.1",), "127.0.0.1", id="loopback-alone"),
            pytest.param(("0.0.0.0",), "127.0.0.1", id="ipv4-wildcard-is-reachable-on-loopback"),
            pytest.param(("::",), "::1", id="ipv6-wildcard-answers-on-its-own-loopback"),
            pytest.param(("192.168.1.5",), "192.168.1.5", id="a-lan-address-is-asked-where-it-is-bound"),
            pytest.param(("192.168.1.5", "127.0.0.1"), "127.0.0.1", id="loopback-wins-over-a-lan-address"),
            pytest.param(("0.0.0.0", "::"), "127.0.0.1", id="both-wildcards"),
            pytest.param(("10.0.0.4", "192.168.1.5"), "10.0.0.4", id="the-first-bound-address-with-no-loopback"),
            pytest.param((), "127.0.0.1", id="no-address-at-all"),
        ],
    )
    def test_the_address_asked_is_one_the_process_actually_holds(
        self, addresses: tuple[str, ...], expected: str
    ) -> None:
        assert probe_address(addresses) == expected

    @pytest.mark.parametrize(
        ("addresses", "expected"),
        [
            pytest.param(("127.0.0.1",), "http://127.0.0.1:4321/", id="ipv4"),
            pytest.param(("::",), "http://[::1]:4321/", id="ipv6-is-bracketed-as-a-host"),
            pytest.param(("192.168.1.5",), "http://192.168.1.5:4321/", id="lan"),
        ],
    )
    def test_the_url_names_the_chosen_address_and_the_listening_port(
        self, addresses: tuple[str, ...], expected: str
    ) -> None:
        assert probe_url(bound(4321, *addresses)) == expected


class TestWhenAPortIsWorthAskingAgain:
    def test_a_port_that_has_never_answered_is_asked_again_quickly(self) -> None:
        never = Probe(is_http=False, status=None, title="", server="", attempts=2, at=PROBED_AT)

        assert probe_interval(never) == PROBE_RETRY

    def test_a_port_that_answered_is_asked_again_on_the_slow_cadence(self) -> None:
        # Otherwise the title, status and server a process gave in its first second and a half
        # stand for as long as it runs, on a page that says it updates live.
        answered = Probe(is_http=True, status=200, title="Grafana", server="caddy", attempts=1, at=PROBED_AT)

        assert probe_interval(answered) == PROBE_REFRESH

    def test_a_port_that_ran_out_of_attempts_falls_back_to_the_slow_cadence(self) -> None:
        given_up = Probe(is_http=False, status=None, title="", server="", attempts=PROBE_MAX_ATTEMPTS, at=PROBED_AT)

        assert probe_interval(given_up) == PROBE_REFRESH

    def test_the_slow_cadence_is_slower_than_the_retry_ladder(self) -> None:
        assert PROBE_REFRESH > PROBE_RETRY > timedelta(0)


class TestProbingARealListener:
    @pytest.mark.parametrize(
        "handler",
        [
            pytest.param(not_http, id="answers-in-another-protocol"),
            pytest.param(says_nothing, id="accepts-then-hangs-up"),
        ],
    )
    async def test_a_listener_that_does_not_speak_http_is_reported_as_such(
        self, listening: Listen, pool: ConnectionPool, handler: Handler
    ) -> None:
        # This is the case the probe exists for, and the answer has to come back as a value:
        # an exception here is never recorded, so the port is re-probed on every scan
        # forever and the page keeps offering a link to a database.
        port = await listening(handler)

        probe = await probe_port(pool, bound(port))

        assert probe.is_http is False
        assert probe.status is None

    async def test_nothing_is_listening_on_a_closed_port(self, pool: ConnectionPool) -> None:
        closed = await asyncio.start_server(says_nothing, "127.0.0.1", 0)
        port: int = closed.sockets[0].getsockname()[1]
        closed.close()
        await closed.wait_closed()

        probe = await probe_port(pool, bound(port))

        assert probe.is_http is False

    async def test_a_page_whose_body_never_ends_is_still_a_web_server_with_a_title(
        self, listening: Listen, pool: ConnectionPool
    ) -> None:
        # Read to the end, this costs the whole probe timeout and then throws away the head
        # it had already parsed, reporting a live web server as not answering HTTP.
        port = await listening(streams_forever)

        probe = await probe_port(pool, bound(port))

        assert probe.is_http is True
        assert probe.status == 200
        assert probe.title == "Tailing"

    async def test_an_ordinary_page_carries_its_title_and_status_back(
        self, listening: Listen, pool: ConnectionPool
    ) -> None:
        port = await listening(serve_a_page)

        probe = await probe_port(pool, bound(port))

        assert probe.is_http is True
        assert probe.status == 200
        assert probe.title == "Grafana"

    async def test_a_listener_bound_away_from_loopback_is_asked_where_it_is_bound(
        self, listening: Listen, pool: ConnectionPool
    ) -> None:
        # Nothing holds this port on 127.0.0.1, so a probe that assumes loopback reports a
        # running web server as not answering HTTP and the page strips its link.
        port = await listening(serve_a_page, "127.0.0.2")

        probe = await probe_port(pool, bound(port, "127.0.0.2"))

        assert probe.is_http is True
        assert probe.title == "Grafana"

    async def test_a_body_larger_than_the_bound_does_not_stall_the_probe(
        self, listening: Listen, pool: ConnectionPool
    ) -> None:
        async def serve_a_huge_page(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)
            body = b"<title>Huge</title>" + b"." * (PROBE_MAX_BYTES * 4)
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-length: %d\r\n\r\n" % len(body))
            writer.write(body)
            await writer.drain()
            writer.close()

        port = await listening(serve_a_huge_page)

        probe = await probe_port(pool, bound(port))

        assert probe.title == "Huge"
