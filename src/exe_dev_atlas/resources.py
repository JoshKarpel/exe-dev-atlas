# How much of the VM is in use right now: CPU, memory, and the root filesystem.
#
# Every figure is rounded, percentages to whole ones and sizes to `GRAIN`, because the payload
# is diffed to decide whether there is news: a figure carried at finer grain than the page
# shows would republish the payload every second over a change nobody can see.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

import psutil

# The VM's own disk. The tmpfs mounts beside it are memory under another name, already
# counted there.
DISK_PATH: Final = "/"

# What a size in use is rounded to: a tenth of a GiB, the finest the page draws. Memory in use
# moves by a few MiB every second on a box doing nothing much, so a MiB grain would publish
# every second over a change the page then rounds away.
GRAIN: Final = 2**30 // 10


@dataclass(frozen=True, slots=True)
class CpuTimes:
    """
    Seconds of CPU time spent since boot across every core, and how many of them were busy.

    A percentage needs two of these: the kernel only counts, so how busy the machine is *now*
    is the busy share of whatever was counted between two readings.
    """

    busy: float
    total: float


@dataclass(frozen=True, slots=True)
class Usage:
    """What the page says about the VM's resources, as the browser receives it."""

    # `None` where nothing was counted between the two readings it was taken from, which is
    # the first scan: it follows the reading it is measured against by microseconds.
    cpu_percent: int | None
    cpu_count: int | None
    memory_percent: int
    memory_used: int
    memory_total: int
    # Of what an unprivileged user can fill rather than of `disk_total`, which is how `df`
    # counts it, so `disk_used / disk_total` is a few points short of this on purpose.
    disk_percent: int
    disk_used: int
    disk_total: int

    def as_dict(self) -> dict[str, object]:
        """Every field, named, for the same reason `Row.as_dict` names its own."""
        return {
            "cpu_percent": self.cpu_percent,
            "cpu_count": self.cpu_count,
            "memory_percent": self.memory_percent,
            "memory_used": self.memory_used,
            "memory_total": self.memory_total,
            "disk_percent": self.disk_percent,
            "disk_used": self.disk_used,
            "disk_total": self.disk_total,
        }


def to_grain(size: int) -> int:
    """`size` in bytes, rounded to the nearest `GRAIN`."""
    return round(size / GRAIN) * GRAIN


def cpu_percent(before: CpuTimes, after: CpuTimes) -> int | None:
    """
    How busy the machine was between two readings, as a share of every core together.

    `None` rather than zero when no time was counted between them, since the honest answer to
    "how busy was it over no time at all" is that there is no answer yet.
    """
    elapsed = after.total - before.total
    if elapsed <= 0:
        return None
    return round(100 * (after.busy - before.busy) / elapsed)


def read_cpu_times() -> CpuTimes:
    """
    The kernel's running CPU counters, reduced to the two sums a percentage needs.

    Counted the way psutil's own `cpu_percent` and htop count them: guest time is already
    inside user and nice on Linux, so it is taken back out of the total, and iowait is a CPU
    waiting rather than working, so it is idle.
    """
    times = psutil.cpu_times()
    total = sum(times) - times.guest - times.guest_nice
    return CpuTimes(busy=total - times.idle - times.iowait, total=total)


def read_usage(since: CpuTimes) -> tuple[Usage, CpuTimes]:
    """
    The VM's resources now, with CPU measured from `since`.

    Also answers the CPU reading this one ended on, so the caller can measure the next one
    from it. Memory in use is what is not `available`, which counts reclaimable page cache as
    free: `used` alone would read a box that has only been reading files as nearly full.
    """
    now = read_cpu_times()
    memory = psutil.virtual_memory()
    memory_used = memory.total - memory.available
    disk = psutil.disk_usage(DISK_PATH)
    usage = Usage(
        cpu_percent=cpu_percent(since, now),
        cpu_count=os.cpu_count(),
        memory_percent=round(100 * memory_used / memory.total),
        memory_used=to_grain(memory_used),
        memory_total=memory.total,
        disk_percent=round(100 * disk.used / (disk.used + disk.free)),
        disk_used=to_grain(disk.used),
        disk_total=disk.total,
    )
    return usage, now
