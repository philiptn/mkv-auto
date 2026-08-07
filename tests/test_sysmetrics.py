"""Unit tests for the live CPU/disk chips on the status line.

The samplers take injectable counter sources, so everything here runs on
synthetic readings — no disk activity, no timing dependence, no psutil.

Both samplers need two readings before they say anything, and the sampling gate
means a reading only lands once SAMPLE_INTERVAL has passed, so most tests here
drive sample() with explicit timestamps.

The disk sampler additionally reports nothing while the disk is idle, and
discards the one reading whose window straddles the moment work started - see
DiskSampler.should_reanchor. So a disk test needs *three* readings before a rate
appears, and `warm()` below is the shorthand for getting there.
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


def warm(sampler, start=100.0):
    """Drive a disk sampler past the prime and the straddling first reading.

    Returns the timestamp of the last reading taken, so callers can carry on
    from there. Consumes two source readings.
    """
    sampler.sample(now=start)
    sampler.sample(now=start + SAMPLE_INTERVAL)
    return start + SAMPLE_INTERVAL


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


def test_the_first_full_window_of_work_yields_the_rate():
    # Reading 1 primes, reading 2 straddles the start of the work and is
    # discarded, reading 3 is a full window and is what gets reported.
    sampler = DiskSampler(('sda',), source=feeder([
        (0, 0), (50 * MB, 25 * MB), (150 * MB, 75 * MB)]))
    last = warm(sampler)
    read, write = sampler.sample(now=last + SAMPLE_INTERVAL)
    assert read == 100 * MB
    assert write == 50 * MB


def test_the_straddling_reading_is_not_reported():
    """Its window is partly the idle time before the stage began, so it
    understates the disk - the symptom this discard exists to remove."""
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (20 * MB, 0)]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) is None


def test_an_idle_disk_reports_nothing_rather_than_zero():
    sampler = DiskSampler(('sda',), source=feeder([(0, 0), (0, 0), (0, 0)]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) is None
    assert sampler.sample(now=102.0) is None


def test_the_first_reported_rate_is_exact_not_eased_from_idle():
    """Easing out of the idle baseline is what made a disk running flat out
    open at a fraction of its real rate and take seconds to converge."""
    sampler = DiskSampler(('sda',), source=feeder([
        (0, 0), (100 * MB, 0), (600 * MB, 0)]))
    last = warm(sampler)
    read, _ = sampler.sample(now=last + SAMPLE_INTERVAL)
    assert read == 500 * MB


def test_a_slow_stage_still_gets_a_chip():
    """Subtitle and remux work runs at hundreds of KB/s; the idle floor must sit
    well below that or those stages would show nothing at all."""
    slow = 400 * 1024
    sampler = DiskSampler(('sda',), source=feeder([
        (0, 0), (slow, 0), (2 * slow, 0)]))
    last = warm(sampler)
    read, _ = sampler.sample(now=last + SAMPLE_INTERVAL)
    assert read == slow


def test_going_idle_again_clears_the_chip():
    sampler = DiskSampler(('sda',), source=feeder([
        (0, 0), (100 * MB, 0), (200 * MB, 0), (200 * MB, 0)]))
    last = warm(sampler)
    assert sampler.sample(now=last + SAMPLE_INTERVAL) is not None
    assert sampler.sample(now=last + 2 * SAMPLE_INTERVAL) is None


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
    """Within a running stage the EMA still smooths jitter - the discard only
    covers the transition into work, not the work itself."""
    sampler = DiskSampler(
        ('sda',),
        source=feeder([(0, 0), (50 * MB, 0), (150 * MB, 0), (350 * MB, 0)]),
    )
    last = warm(sampler)
    sampler.sample(now=last + SAMPLE_INTERVAL)          # 100 MB/s, taken as-is
    read, _ = sampler.sample(now=last + 2 * SAMPLE_INTERVAL)  # raw 200 MB/s
    assert read == 100 * MB + (200 * MB - 100 * MB) * EMA_ALPHA


def test_gap_longer_than_stale_after_reprimes_instead_of_averaging():
    sampler = DiskSampler(
        ('sda',),
        source=feeder([(0, 0), (100 * GB, 0),
                       (100 * GB + 50 * MB, 0), (100 * GB + 100 * MB, 0)]),
    )
    sampler.sample(now=100.0)
    # 100 GB accumulated while nothing was watching: not a rate this stage saw.
    assert sampler.sample(now=100.0 + STALE_AFTER + 1) is None
    start = 100.0 + STALE_AFTER + 1
    sampler.sample(now=start + SAMPLE_INTERVAL)         # straddling, discarded
    read, _ = sampler.sample(now=start + 2 * SAMPLE_INTERVAL)
    assert read == 50 * MB


def test_current_reads_back_without_taking_a_reading():
    # The render thread reads through current(); only the sampler thread calls
    # sample(). A second current() must not consume the source's next reading.
    sampler = DiskSampler(('sda',), source=feeder([
        (0, 0), (50 * MB, 0), (150 * MB, 0)]))
    assert sampler.current() is None
    last = warm(sampler)
    assert sampler.current() is None
    sampler.sample(now=last + SAMPLE_INTERVAL)
    assert sampler.current() == sampler.current() == (100 * MB, 0.0)


def test_counter_going_backwards_shows_nothing():
    """A drop means a device left the sum or the counter wrapped. rates() floors
    it at zero, and zero is not a reading worth putting on the line."""
    sampler = DiskSampler(('sda',), source=feeder([(500 * MB, 0), (0, 0)]))
    sampler.sample(now=100.0)
    assert sampler.sample(now=101.0) is None


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
