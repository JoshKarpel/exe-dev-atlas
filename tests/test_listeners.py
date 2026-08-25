from __future__ import annotations

import pytest

from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.listeners import parse_listeners
from exe_dev_atlas.listeners import ticks_from_stat

# One `ss --tcp --listening --numeric --processes --no-header` line, with the columns it
# actually emits: State, Recv-Q, Send-Q, Local, Peer, Process.
LISTEN = 'LISTEN 0      4096   {local}      0.0.0.0:*    users:(("{name}",pid={pid},fd=3))'


def line(local: str, name: str = "server", pid: int = 4711) -> str:
    return LISTEN.format(local=local, name=name, pid=pid)


def test_a_single_listener_becomes_one_row_carrying_its_address_and_pid() -> None:
    assert parse_listeners(line("127.0.0.1:3456", pid=8812)) == [
        Listener(port=3456, pid=8812, addresses=("127.0.0.1",))
    ]


def test_the_same_port_bound_on_ipv4_and_ipv6_collapses_to_one_row() -> None:
    output = "\n".join([line("0.0.0.0:4567", pid=9203), line("[::]:4567", pid=9203)])

    assert parse_listeners(output) == [Listener(port=4567, pid=9203, addresses=("0.0.0.0", "::"))]


@pytest.mark.parametrize(
    ("local", "why"),
    [
        pytest.param("127.0.0.1:22", "below the proxied range", id="ssh"),
        pytest.param("127.0.0.1:2999", "one below the first routed port", id="just-below"),
        pytest.param("127.0.0.1:10000", "one above the last routed port", id="just-above"),
        pytest.param("127.0.0.1:54321", "an ephemeral port well above the range", id="ephemeral"),
    ],
)
def test_a_port_outside_the_proxied_range_is_dropped(local: str, why: str) -> None:
    assert parse_listeners(line(local)) == [], why


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(3000, 9999, id="both-ends-of-the-range"),
        pytest.param(4712, 6823, id="two-in-the-middle"),
    ],
)
def test_rows_come_back_sorted_by_port_whatever_order_they_arrived_in(first: int, second: int) -> None:
    output = "\n".join([line(f"127.0.0.1:{second}", pid=101), line(f"127.0.0.1:{first}", pid=202)])

    assert [listener.port for listener in parse_listeners(output)] == [first, second]


def test_a_listener_whose_process_is_not_ours_to_see_still_becomes_a_row() -> None:
    # `ss` omits the users:(...) column entirely for a socket owned by another user.
    output = "LISTEN 0      4096   127.0.0.1:5432      0.0.0.0:*"

    assert parse_listeners(output) == [Listener(port=5432, pid=None, addresses=("127.0.0.1",))]


@pytest.mark.parametrize(
    "garbage",
    [
        pytest.param("", id="empty"),
        pytest.param("\n\n", id="blank-lines"),
        pytest.param("LISTEN 0 4096", id="too-few-columns"),
        pytest.param("LISTEN 0 4096 127.0.0.1:notaport 0.0.0.0:*", id="non-numeric-port"),
        pytest.param("Netid State Recv-Q Send-Q Local", id="a-header-that-slipped-through"),
    ],
)
def test_a_line_that_is_not_a_listener_contributes_nothing(garbage: str) -> None:
    assert parse_listeners(garbage) == []


def test_a_wildcard_bind_and_a_loopback_bind_on_one_port_are_one_row_with_both_addresses() -> None:
    output = "\n".join([line("127.0.0.1:7331", pid=55), line("192.168.1.9:7331", pid=55)])

    (listener,) = parse_listeners(output)
    assert listener.addresses == ("127.0.0.1", "192.168.1.9")


def test_the_first_pid_seen_for_a_port_is_the_one_kept() -> None:
    # A port can appear twice with different pids (a pre-forking server). One row means one
    # pid, and taking the first is what makes the choice deterministic rather than arbitrary.
    output = "\n".join([line("127.0.0.1:6060", pid=333), line("[::]:6060", pid=444)])

    (listener,) = parse_listeners(output)
    assert listener.pid == 333


class TestStartTimeParsing:
    def test_the_starttime_field_is_read_from_a_plain_stat_line(self) -> None:
        fields = " ".join(str(n) for n in range(3, 22))
        assert ticks_from_stat(f"7788 (server) S {fields}") == 21

    def test_a_command_name_containing_spaces_and_parens_does_not_shift_the_fields(self) -> None:
        # The reason this parses from the *last* closing paren rather than splitting the line:
        # `comm` is arbitrary bytes from the process, parentheses and all.
        fields = " ".join(str(n) for n in range(3, 22))
        assert ticks_from_stat(f"7788 (my (weird) name) S {fields}") == 21

    @pytest.mark.parametrize(
        "stat",
        [
            pytest.param("", id="empty"),
            pytest.param("7788 no-parens-here S 1 2 3", id="no-closing-paren"),
            pytest.param("7788 (server) S 1 2 3", id="too-few-fields"),
            pytest.param("7788 (server) S " + " ".join(["x"] * 25), id="non-numeric-fields"),
        ],
    )
    def test_a_stat_line_that_cannot_be_read_yields_nothing_rather_than_a_wrong_number(self, stat: str) -> None:
        assert ticks_from_stat(stat) is None
