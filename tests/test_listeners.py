from __future__ import annotations

import getpass
import os
import random
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from exe_dev_atlas.listeners import NO_PROCESS
from exe_dev_atlas.listeners import ROUTED_PORTS
from exe_dev_atlas.listeners import Binding
from exe_dev_atlas.listeners import Listener
from exe_dev_atlas.listeners import group_listeners
from exe_dev_atlas.listeners import read_environ
from exe_dev_atlas.listeners import read_listeners
from exe_dev_atlas.listeners import read_process

# High enough that nothing holds it, so every read about it comes back empty.
ABSENT_PID = 4_194_303


def bound(port: int, address: str = "127.0.0.1", pid: int | None = 4711) -> Binding:
    """One listening socket, as the kernel reports it."""
    return Binding(port=port, pid=pid, address=address)


def test_a_single_listener_becomes_one_row_carrying_its_address_and_pid() -> None:
    assert group_listeners([bound(3456, "127.0.0.1", 8812)]) == [
        Listener(port=3456, pid=8812, addresses=("127.0.0.1",))
    ]


def test_the_same_port_bound_on_ipv4_and_ipv6_collapses_to_one_row() -> None:
    bindings = [bound(4567, "0.0.0.0", 9203), bound(4567, "::", 9203)]

    assert group_listeners(bindings) == [Listener(port=4567, pid=9203, addresses=("0.0.0.0", "::"))]


@pytest.mark.parametrize(
    "port",
    [
        pytest.param(22, id="ssh-below-the-range"),
        pytest.param(2999, id="one-below-the-first-routed-port"),
        pytest.param(10000, id="one-above-the-last-routed-port"),
        pytest.param(54321, id="ephemeral-well-above-the-range"),
    ],
)
def test_a_port_outside_the_proxied_range_is_dropped(port: int) -> None:
    assert group_listeners([bound(port)]) == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(3000, 9999, id="both-ends-of-the-range"),
        pytest.param(4712, 6823, id="two-in-the-middle"),
    ],
)
def test_rows_come_back_sorted_by_port_whatever_order_they_arrived_in(first: int, second: int) -> None:
    bindings = [bound(second, pid=101), bound(first, pid=202)]

    assert [listener.port for listener in group_listeners(bindings)] == [first, second]


def test_a_listener_whose_process_is_not_ours_to_see_still_becomes_a_row() -> None:
    # A socket owned by another user arrives with no pid, because its `/proc/<pid>/fd` is not
    # ours to read. The port is still real and still worth a row.
    assert group_listeners([bound(5432, pid=None)]) == [Listener(port=5432, pid=None, addresses=("127.0.0.1",))]


def test_nothing_listening_is_an_empty_listing_rather_than_an_error() -> None:
    assert group_listeners([]) == []


def test_one_process_bound_on_two_addresses_is_one_row_carrying_both() -> None:
    bindings = [bound(7331, "127.0.0.1", 55), bound(7331, "192.168.1.9", 55)]

    (listener,) = group_listeners(bindings)
    assert listener.addresses == ("127.0.0.1", "192.168.1.9")


def test_two_processes_sharing_a_port_number_stay_two_rows() -> None:
    # Binding one port number on two addresses from two processes is legal and ordinary.
    # Merged on the port alone, the surviving row shows one process's address beside the
    # other's command line, working directory and user, and a session lookup runs against
    # the wrong pid.
    bindings = [bound(3000, "127.0.0.1", 111), bound(3000, "192.168.1.5", 222)]

    assert group_listeners(bindings) == [
        Listener(port=3000, pid=111, addresses=("127.0.0.1",)),
        Listener(port=3000, pid=222, addresses=("192.168.1.5",)),
    ]


def test_rows_sharing_a_port_number_come_back_in_pid_order() -> None:
    bindings = [bound(6060, "127.0.0.1", 444), bound(6060, "192.168.1.5", 333)]

    assert [listener.pid for listener in group_listeners(bindings)] == [333, 444]


def test_a_row_with_no_pid_sorts_ahead_of_the_named_processes_on_its_port() -> None:
    # A pid of None cannot be compared against a number, so this is the case that decides
    # whether the ordering is total at all.
    bindings = [bound(7070, "127.0.0.1", None), bound(7070, "192.168.1.5", 99)]

    assert [listener.pid for listener in group_listeners(bindings)] == [None, 99]


@contextmanager
def listening_on_a_routed_port() -> Iterator[int]:
    """A real listening socket on a port the proxy forwards, taken down with the test."""
    with socket.socket() as held:
        for port in random.sample(ROUTED_PORTS, 200):
            try:
                held.bind(("127.0.0.1", port))
            except OSError:
                continue
            held.listen(1)
            yield port
            return
    raise RuntimeError("nothing in the routed range was free to bind")


class TestReadingThisMachine:
    """
    What the kernel says about a socket and a process this test itself is holding.

    Nothing else pins the field names psutil is asked for. The grouping above is pure and
    tested against values written by hand, so a psutil release that renamed `laddr` or
    stopped answering `create_time` would leave every one of those tests green while the
    page rendered blank rows.
    """

    def test_a_socket_this_process_holds_is_found_with_this_process_named(self) -> None:
        with listening_on_a_routed_port() as port:
            found = [listener for listener in read_listeners() if listener.port == port]

        assert found == [Listener(port=port, pid=os.getpid(), addresses=("127.0.0.1",))]

    def test_a_socket_outside_the_routed_range_is_not_listed(self) -> None:
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            ephemeral: int = held.getsockname()[1]

            assert ephemeral not in ROUTED_PORTS
            assert [listener for listener in read_listeners() if listener.port == ephemeral] == []

    def test_this_process_describes_itself(self) -> None:
        process = read_process(os.getpid())

        assert process.command_name
        assert process.command_line
        assert process.user == getpass.getuser()
        assert process.directory == str(Path.cwd())
        assert Path(process.executable).exists()

    def test_a_start_time_is_a_past_second_that_does_not_move_between_scans(self) -> None:
        # The whole reason it is derived from the kernel's own boot second: a value that
        # wanders re-publishes the entire payload and re-renders every client once a second.
        first = read_process(os.getpid()).started_at
        time.sleep(0.05)
        second = read_process(os.getpid()).started_at

        assert first == second
        assert first is not None
        assert 0 <= time.time() - first < 3600

    def test_this_process_reports_the_environment_it_was_started_with(self) -> None:
        assert "PATH" in read_environ(os.getpid())

    @pytest.mark.parametrize(
        "pid",
        [
            pytest.param(None, id="a-socket-with-no-process-to-ask-about"),
            pytest.param(ABSENT_PID, id="a-pid-nothing-holds"),
        ],
    )
    def test_a_process_that_cannot_be_read_costs_blanks_rather_than_the_row(self, pid: int | None) -> None:
        assert read_process(pid) == NO_PROCESS

    def test_an_environment_that_cannot_be_read_is_empty_rather_than_an_error(self) -> None:
        assert read_environ(ABSENT_PID) == {}
