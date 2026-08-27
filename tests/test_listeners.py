from __future__ import annotations

import pytest
from conftest import line

from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.listeners import parse_boot_epoch
from exe_dev_atlas.listeners import parse_listeners
from exe_dev_atlas.listeners import ticks_from_stat


def test_a_single_listener_becomes_one_row_carrying_its_address_and_pid() -> None:
    assert parse_listeners(line("127.0.0.1:3456", pid=8812)) == [
        Listener(port=3456, pid=8812, addresses=("127.0.0.1",))
    ]


def test_the_same_port_bound_on_ipv4_and_ipv6_collapses_to_one_row() -> None:
    output = "\n".join([line("0.0.0.0:4567", pid=9203), line("[::]:4567", pid=9203)])

    assert parse_listeners(output) == [Listener(port=4567, pid=9203, addresses=("0.0.0.0", "::"))]


@pytest.mark.parametrize(
    "local",
    [
        pytest.param("127.0.0.1:22", id="ssh-below-the-range"),
        pytest.param("127.0.0.1:2999", id="one-below-the-first-routed-port"),
        pytest.param("127.0.0.1:10000", id="one-above-the-last-routed-port"),
        pytest.param("127.0.0.1:54321", id="ephemeral-well-above-the-range"),
    ],
)
def test_a_port_outside_the_proxied_range_is_dropped(local: str) -> None:
    assert parse_listeners(line(local)) == []


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


def test_one_process_bound_on_two_addresses_is_one_row_carrying_both() -> None:
    output = "\n".join([line("127.0.0.1:7331", pid=55), line("192.168.1.9:7331", pid=55)])

    (listener,) = parse_listeners(output)
    assert listener.addresses == ("127.0.0.1", "192.168.1.9")


def test_two_processes_sharing_a_port_number_stay_two_rows() -> None:
    # Binding one port number on two addresses from two processes is legal and ordinary.
    # Merged on the port alone, the surviving row shows one process's address beside the
    # other's command line, working directory and user, and a session lookup runs against
    # the wrong pid.
    output = "\n".join([line("127.0.0.1:3000", name="nodeapp", pid=111), line("192.168.1.5:3000", "otherapp", 222)])

    assert parse_listeners(output) == [
        Listener(port=3000, pid=111, addresses=("127.0.0.1",)),
        Listener(port=3000, pid=222, addresses=("192.168.1.5",)),
    ]


def test_rows_sharing_a_port_number_come_back_in_pid_order() -> None:
    output = "\n".join([line("127.0.0.1:6060", pid=444), line("192.168.1.5:6060", pid=333)])

    assert [listener.pid for listener in parse_listeners(output)] == [333, 444]


def test_a_row_with_no_pid_sorts_ahead_of_the_named_processes_on_its_port() -> None:
    # A pid of None cannot be compared against a number, so this is the case that decides
    # whether the ordering is total at all.
    output = "\n".join(["LISTEN 0 4096 127.0.0.1:7070 0.0.0.0:*", line("192.168.1.5:7070", pid=99)])

    assert [listener.pid for listener in parse_listeners(output)] == [None, 99]


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


class TestBootEpochParsing:
    def test_the_btime_field_is_read_from_among_the_others(self) -> None:
        stat = "cpu  199 0 87 4212\nintr 90210\nbtime 1787145398\nprocesses 3401\n"

        assert parse_boot_epoch(stat) == 1787145398

    @pytest.mark.parametrize(
        "stat",
        [
            pytest.param("", id="empty"),
            pytest.param("cpu  199 0 87 4212\nintr 90210\n", id="no-btime-line"),
            pytest.param("btime\n", id="btime-with-no-value"),
            pytest.param("btime notanumber\n", id="btime-that-is-not-a-number"),
        ],
    )
    def test_a_stat_file_carrying_no_readable_btime_yields_nothing(self, stat: str) -> None:
        # Rather than a wrong epoch: every uptime on the page is derived from this, so a
        # number guessed here is a wrong "up 3h" on every row.
        assert parse_boot_epoch(stat) is None
