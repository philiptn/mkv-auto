"""Unit tests for the live CPU/disk chips on the status line.

The samplers take injectable counter sources, so everything here runs on
synthetic readings — no disk activity, no timing dependence, no psutil.

Both samplers need two readings before they say anything, and the sampling gate
means a reading only lands once SAMPLE_INTERVAL has passed, so most tests here
drive sample() with explicit timestamps.
"""

import os
from collections import namedtuple

from modules import sysmetrics
from tests.diff_harness import REPO_ROOT
from modules.sysmetrics import (
    EMA_ALPHA,
    SAMPLE_INTERVAL,
    STALE_AFTER,
    CpuSampler,
    DiskSampler,
    _resolve_devices,
    format_rate,
    peak_rate,
)

MB = 1024 ** 2
GB = 1024 ** 3

# The fields psutil.cpu_times() reports on Linux, in order.
CpuTimes = namedtuple('CpuTimes', 'user nice system idle iowait irq softirq steal guest guest_nice')


def cpu_times(busy=0.0, idle=0.0, iowait=0.0):
    return CpuTimes(busy, 0.0, 0.0, idle, iowait, 0.0, 0.0, 0.0, 0.0, 0.0)


def feeder(readings):
    """Counter source that hands out `readings` one call at a time."""
    it = iter(readings)
    return lambda: next(it)


# --------------------------------------------------------------------------
# format_rate
# --------------------------------------------------------------------------

def test_format_rate_units():
    assert format_rate(0) == "0B/s"
    assert format_rate(999) == "999B/s"
    assert format_rate(1024) == "1KB/s"
    assert format_rate(MB - 1) == "1024KB/s"
    assert format_rate(512 * MB) == "512MB/s"
    assert format_rate(GB) == "1.0GB/s"
    assert format_rate(5.5 * GB) == "5.5GB/s"


def test_format_rate_drops_decimal_above_ten_gigabytes():
    assert format_rate(12.4 * GB) == "12GB/s"


def test_format_rate_handles_missing_and_negative():
    assert format_rate(None) == "0B/s"
    assert format_rate(-1) == "0B/s"


# --------------------------------------------------------------------------
# peak_rate — only the busier direction is rendered
# --------------------------------------------------------------------------

def test_peak_rate_shows_the_larger_direction():
    assert peak_rate(1.2 * GB, 840 * MB) == "↓1.2GB/s"
    assert peak_rate(84 * MB, 512 * MB) == "↑512MB/s"


def test_peak_rate_flips_with_the_balance():
    # Same stage, one sample later: the chip follows the load, it does not
    # latch onto whichever direction happened to lead first.
    assert peak_rate(400 * MB, 10 * MB) == "↓400MB/s"
    assert peak_rate(10 * MB, 400 * MB) == "↑400MB/s"


def test_peak_rate_idle_reads_as_zero():
    assert peak_rate(0, 0) == "↓0B/s"


# --------------------------------------------------------------------------
# cold start and gating
# --------------------------------------------------------------------------

def test_first_sample_yields_nothing():
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (MB, MB)]))
    assert sampler.sample(now=100.0) is None


def test_second_sample_yields_the_rate():
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (200 * MB, 100 * MB)]))
    sampler.sample(now=100.0)
    read, write = sampler.sample(now=102.0)
    assert read == 100 * MB
    assert write == 50 * MB


def test_reading_is_held_between_sample_intervals():
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (200 * MB, 0)]))
    sampler.sample(now=100.0)
    first = sampler.sample(now=102.0)
    # No source reading is left; asking again inside the gate must not consume
    # one, and must return the value already computed.
    assert sampler.sample(now=102.0 + SAMPLE_INTERVAL / 2) == first


# --------------------------------------------------------------------------
# smoothing and staleness
# --------------------------------------------------------------------------

def test_subsequent_readings_are_eased_toward():
    sampler = DiskSampler(
        ('sda',),
        source=feeder([(0, 0), (100 * MB, 0), (300 * MB, 0)]),
    )
    sampler.sample(now=100.0)
    sampler.sample(now=101.0)   # 100 MB/s, displayed as-is
    read, _ = sampler.sample(now=102.0)  # raw 200 MB/s
    assert read == 100 * MB + (200 * MB - 100 * MB) * EMA_ALPHA


def test_gap_longer_than_stale_after_reprimes_instead_of_averaging():
    sampler = DiskSampler(
        ('sda',),
        source=feeder([(0, 0), (100 * GB, 0), (100 * GB + 50 * MB, 0)]),
    )
    sampler.sample(now=100.0)
    # 100 GB accumulated while nothing was watching: not a rate this stage saw.
    assert sampler.sample(now=100.0 + STALE_AFTER + 1) is None
    read, _ = sampler.sample(now=100.0 + STALE_AFTER + 2)
    assert read == 50 * MB


def test_current_reads_back_without_taking_a_reading():
    # The render thread reads through current(); only the sampler thread calls
    # sample(). A second current() must not consume the source's next reading.
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (100 * MB, 0)]))
    assert sampler.current() is None
    sampler.sample(now=100.0)
    assert sampler.current() is None
    sampler.sample(now=101.0)
    assert sampler.current() == sampler.current() == (100 * MB, 0.0)


def test_counter_going_backwards_reads_as_zero():
    sampler = DiskSampler(('sda',), source=feeder([(500 * MB, 0), (0, 0)]))
    sampler.sample(now=100.0)
    read, write = sampler.sample(now=101.0)
    assert read == 0
    assert write == 0


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

def test_cpu_percent_from_busy_and_idle_delta():
    sampler = CpuSampler(source=feeder([
        cpu_times(busy=0.0, idle=0.0),
        cpu_times(busy=7.5, idle=2.5),
    ]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) == (75.0,)


def test_cpu_counts_iowait_as_idle():
    sampler = CpuSampler(source=feeder([
        cpu_times(busy=0.0, idle=0.0, iowait=0.0),
        cpu_times(busy=5.0, idle=0.0, iowait=5.0),
    ]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) == (50.0,)


def test_cpu_percent_is_clamped():
    sampler = CpuSampler(source=feeder([
        cpu_times(busy=0.0, idle=10.0),
        cpu_times(busy=0.0, idle=5.0),   # idle fell: nonsense, but must not go negative
    ]))
    sampler.sample(now=100.0)
    pct, = sampler.sample(now=101.0)
    assert 0.0 <= pct <= 100.0


def test_cpu_idle_tick_reads_as_zero_not_a_crash():
    sampler = CpuSampler(source=feeder([cpu_times(), cpu_times()]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) == (0.0,)


# --------------------------------------------------------------------------
# device resolution
# --------------------------------------------------------------------------

def test_unresolvable_path_falls_back_to_all_disks(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("no diskstats here")

    monkeypatch.setattr(sysmetrics.psutil, 'disk_io_counters', unavailable)
    assert _resolve_devices(('/nonexistent-mount-point-xyz/tmp',)) is None


def test_path_that_does_not_exist_yet_resolves_via_its_parent():
    # Stage directories are created as the pipeline goes; the device is the
    # nearest existing ancestor's, and every ancestor is on the same one.
    parent = _resolve_devices((REPO_ROOT,))
    child = _resolve_devices((os.path.join(REPO_ROOT, 'not-created-yet', 'deeper'),))
    assert child == parent


def test_resolution_is_cached_per_path_set():
    key = ('/nonexistent-mount-point-abc/tmp',)
    first = _resolve_devices(key)
    assert _resolve_devices(key) is first
