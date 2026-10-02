from __future__ import annotations

import time

import pytest

from exe_dev_atlas.resources import GRAIN
from exe_dev_atlas.resources import CpuTimes
from exe_dev_atlas.resources import cpu_percent
from exe_dev_atlas.resources import read_cpu_times
from exe_dev_atlas.resources import read_usage
from exe_dev_atlas.resources import to_grain


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(CpuTimes(busy=120.0, total=900.0), CpuTimes(busy=123.0, total=904.0), 75, id="busy-share"),
        pytest.param(CpuTimes(busy=120.0, total=900.0), CpuTimes(busy=120.0, total=902.0), 0, id="idle"),
        pytest.param(CpuTimes(busy=120.0, total=900.0), CpuTimes(busy=122.0, total=902.0), 100, id="saturated"),
    ],
)
def test_cpu_is_the_busy_share_of_the_time_between_two_readings(
    before: CpuTimes, after: CpuTimes, expected: int
) -> None:
    assert cpu_percent(before, after) == expected


def test_cpu_measured_over_no_time_at_all_has_no_answer_rather_than_zero() -> None:
    # The first scan, which follows the reading it is measured from by microseconds: inside
    # one clock tick the kernel's counters have not moved, and 0% would be a claim.
    reading = CpuTimes(busy=120.0, total=900.0)

    assert cpu_percent(reading, reading) is None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param(GRAIN * 37 + GRAIN // 3, GRAIN * 37, id="down"),
        pytest.param(GRAIN * 37 + GRAIN * 2 // 3, GRAIN * 38, id="up"),
        pytest.param(GRAIN * 41, GRAIN * 41, id="already-on-a-grain"),
    ],
)
def test_a_size_in_use_is_rounded_to_the_nearest_grain(size: int, expected: int) -> None:
    assert to_grain(size) == expected


def test_a_size_in_use_moves_only_by_a_tenth_of_a_gib() -> None:
    # The churn this exists to stop: memory in use moves by a few MiB a second on an idle box,
    # and every one of those would otherwise be a payload the page rounds back to the same text.
    assert to_grain(5 * 2**30 + 3 * 2**20) == to_grain(5 * 2**30 - 7 * 2**20)


class TestReadingThisMachine:
    """
    What psutil says about this box, which nothing else pins.

    `cpu_percent` above is tested over readings written by hand, so a psutil release that
    renamed `iowait` or `available` would leave it green while every scan raised.
    """

    def test_every_figure_is_a_percentage_of_something_real(self) -> None:
        since = read_cpu_times()
        # Long enough for the kernel's 10ms counters to move on every core.
        time.sleep(0.05)

        usage, _ = read_usage(since)

        assert usage.cpu_percent is not None
        for percent in (usage.cpu_percent, usage.memory_percent, usage.disk_percent):
            assert 0 <= percent <= 100
        assert 0 < usage.memory_used <= usage.memory_total
        assert 0 < usage.disk_used <= usage.disk_total

    def test_a_reading_ends_where_the_next_one_starts(self) -> None:
        since = read_cpu_times()

        _, ended = read_usage(since)

        assert ended.total >= since.total
        assert ended.busy >= since.busy
