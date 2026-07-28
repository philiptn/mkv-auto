"""Unit tests for the encoder's batch time-remaining estimate.

Covers EncodeEstimator's two data sources (timed sample encodes and the real
encodes' own progress), the calibration factor that reconciles them, the 4K
serialization rule, and the display smoothing.

display_eta() eases the value it returns, but the first call on a fresh
estimator has nothing to ease from and returns the raw figure — so most tests
here call it exactly once.
"""

import time

import pytest

from modules.encode_estimator import (
    ETA_ALPHA_DOWN,
    ETA_ALPHA_UP,
    ETA_CALIB_CLAMP,
    EncodeEstimator,
)
from modules.misc import format_duration_short, format_time

HD = (1920, 1080)
UHD = (3840, 2160)
HOUR = 3600.0


def make_files(count, duration=HOUR, dims=HD, is_4k=False, threads=8):
    return [
        {
            'index': i,
            'name': f'file{i}.mkv',
            'duration': duration,
            'width': dims[0],
            'height': dims[1],
            'is_4k': is_4k,
            'threads': threads,
        }
        for i in range(count)
    ]


# --------------------------------------------------------------------------
# no data
# --------------------------------------------------------------------------

def test_no_data_yields_no_eta():
    est = EncodeEstimator(make_files(3), codec_cap=2)
    assert est.display_eta() is None


def test_all_files_done_yields_zero():
    est = EncodeEstimator(make_files(2), codec_cap=2)
    for i in range(2):
        est.note_start(i)
        est.note_complete(i)
    assert est.display_eta() == pytest.approx(0.0, abs=1.0)


def test_missing_duration_is_excluded_not_fatal():
    files = make_files(2)
    files[1]['duration'] = None
    est = EncodeEstimator(files, codec_cap=1)
    # 10 wall seconds per video second -> 10h for the one file we can price.
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)
    assert est.display_eta() == pytest.approx(10 * HOUR, rel=0.01)


def test_zero_duration_does_not_divide_by_zero():
    files = make_files(1, duration=0)
    est = EncodeEstimator(files, codec_cap=1)
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)
    assert est.display_eta() is None


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def test_one_sample_prices_the_whole_batch():
    # The point of the sampler: an ETA for every file off a single measurement.
    est = EncodeEstimator(make_files(4), codec_cap=4)
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)  # 10x realtime

    # 4 files x 10h of work, spread over 4 slots.
    assert est.display_eta() == pytest.approx(10 * HOUR, rel=0.01)


def test_sample_generalizes_across_resolution_and_threads():
    # One HD sample must price a 4K file that has half the threads: the cost
    # scales with pixels and inversely with threads.
    files = make_files(1, dims=HD, threads=8)
    files += [{
        'index': 1, 'name': 'big.mkv', 'duration': HOUR,
        'width': UHD[0], 'height': UHD[1], 'is_4k': False, 'threads': 4,
    }]
    est = EncodeEstimator(files, codec_cap=1)   # one slot: costs simply add
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)

    hd_cost = 10 * HOUR
    uhd_cost = hd_cost * (UHD[0] * UHD[1]) / (HD[0] * HD[1]) * (8 / 4)
    assert est.display_eta() == pytest.approx(hd_cost + uhd_cost, rel=0.01)


def test_per_file_sample_overrides_the_pool():
    est = EncodeEstimator(make_files(2), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)    # 1x realtime
    est.record_sample(1, wall_seconds=30.0, video_seconds=6.0)   # 5x realtime

    assert est.display_eta() == pytest.approx(HOUR + 5 * HOUR, rel=0.01)


def test_repeated_samples_of_a_file_pool_together():
    est = EncodeEstimator(make_files(1), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)

    # Pooled over both windows: 66 wall seconds for 12 video seconds.
    assert est.display_eta() == pytest.approx((66 / 12) * HOUR, rel=0.01)


def test_unsampleable_file_still_priced_from_the_pool():
    est = EncodeEstimator(make_files(2), codec_cap=1)
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)
    est.mark_unsampleable(1)

    assert est.wants_sample(1) is False
    assert est.display_eta() == pytest.approx(2 * 10 * HOUR, rel=0.01)


def test_wants_sample_tracks_state_and_cap():
    est = EncodeEstimator(make_files(1), codec_cap=1)
    assert est.wants_sample(0) is True
    est.note_start(0)
    assert est.wants_sample(0) is False   # running: real progress is better data


# --------------------------------------------------------------------------
# parallelism and the 4K rule
# --------------------------------------------------------------------------

def test_non_4k_work_is_shared_across_slots():
    est = EncodeEstimator(make_files(4), codec_cap=4)
    for i in range(4):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)   # 1h each

    assert est.display_eta() == pytest.approx(HOUR, rel=0.01)


def test_4k_work_is_serialized():
    # Same work as above, but 4K files run one at a time -> four times as long.
    est = EncodeEstimator(make_files(4, dims=UHD, is_4k=True), codec_cap=4)
    for i in range(4):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)

    assert est.display_eta() == pytest.approx(4 * HOUR, rel=0.01)


def test_slots_are_capped_by_remaining_file_count():
    # Two files across four slots must not be divided by four.
    est = EncodeEstimator(make_files(2), codec_cap=4)
    for i in range(2):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)

    assert est.display_eta() == pytest.approx(HOUR, rel=0.01)


def test_reserved_sampler_slot_raises_the_eta():
    est = EncodeEstimator(make_files(4), codec_cap=4)
    for i in range(4):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)
    with_all_slots = est.display_eta()

    est.set_sampling(True)          # sampler holds one of the four slots
    est._displayed = None           # read the raw value, not an eased one
    with_reserved_slot = est.display_eta()

    assert with_reserved_slot == pytest.approx(4 * HOUR / 3, rel=0.01)
    assert with_reserved_slot > with_all_slots


def test_makespan_floor():
    # One 3h file plus nine tiny ones over four slots: the batch cannot finish
    # before its longest single job, however well the rest parallelize.
    files = make_files(1, duration=3 * HOUR) + [
        {'index': i, 'name': f'small{i}.mkv', 'duration': 60.0,
         'width': HD[0], 'height': HD[1], 'is_4k': False, 'threads': 8}
        for i in range(1, 10)
    ]
    est = EncodeEstimator(files, codec_cap=4)
    for i in range(10):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)

    assert est.display_eta() == pytest.approx(3 * HOUR, rel=0.01)


# --------------------------------------------------------------------------
# real progress and calibration
# --------------------------------------------------------------------------

def test_in_flight_file_is_priced_from_its_own_progress():
    # Running at half the sampled speed: at 50% done the file's own
    # measurement is trusted completely and the sample is discarded.
    est = EncodeEstimator(make_files(1), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)     # predicts 1h
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - HOUR              # 1h elapsed
    est.note_progress(0, 0.5)

    # Projected total 2h, half of it left.
    assert est.display_eta() == pytest.approx(HOUR, rel=0.02)


def test_early_progress_is_ignored():
    # Below ETA_MIN_FRACTION the self-measurement is noise, so the sampled
    # prediction stands.
    est = EncodeEstimator(make_files(1), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 1.0
    est.note_progress(0, 0.001)

    assert est.display_eta() == pytest.approx(HOUR, rel=0.02)


def test_observation_prices_unsampled_files():
    # No samples at all — the all-4K and single-file paths rely on this.
    est = EncodeEstimator(make_files(3), codec_cap=1)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - HOUR
    est.note_progress(0, 0.5)

    # File 0 projects to 2h total (1h left); files 1 and 2 inherit 2h each.
    assert est.display_eta() == pytest.approx(HOUR + 2 * (2 * HOUR), rel=0.02)


def test_calibration_lifts_estimates_when_reality_is_slower():
    est = EncodeEstimator(make_files(4), codec_cap=1)
    for i in range(4):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)
    baseline = est.display_eta()

    # File 0 actually runs at half the sampled speed.
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - HOUR
    est.note_progress(0, 0.5)

    for _ in range(50):
        est.display_eta()

    assert est._calibration == pytest.approx(2.0, rel=0.05)
    est._displayed = None
    # File 0 is priced from itself (1h left); the other three are now 2h each.
    assert est.display_eta() == pytest.approx(HOUR + 3 * 2 * HOUR, rel=0.05)
    assert est.display_eta() > baseline


def test_calibration_is_clamped_both_ways():
    low, high = ETA_CALIB_CLAMP

    slow = EncodeEstimator(make_files(2), codec_cap=1)
    slow.record_sample(0, wall_seconds=6.0, video_seconds=6.0)
    slow.note_start(0)
    slow._files[0]['started_at'] = time.time() - 100 * HOUR
    slow.note_progress(0, 0.5)
    for _ in range(200):
        slow.display_eta()
    assert slow._calibration <= high + 1e-9

    fast = EncodeEstimator(make_files(2), codec_cap=1)
    fast.record_sample(0, wall_seconds=600.0, video_seconds=6.0)
    fast.note_start(0)
    fast._files[0]['started_at'] = time.time() - 1.0
    fast.note_progress(0, 0.9)
    for _ in range(200):
        fast.display_eta()
    assert fast._calibration >= low - 1e-9


def test_completed_file_drops_out_of_the_estimate():
    est = EncodeEstimator(make_files(2), codec_cap=1)
    for i in range(2):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)
    both = est.display_eta()

    est.note_start(0)
    est.note_complete(0)
    est._displayed = None
    assert est.display_eta() < both


# --------------------------------------------------------------------------
# display smoothing
# --------------------------------------------------------------------------

def test_eta_eases_down_when_a_file_completes():
    est = EncodeEstimator(make_files(2), codec_cap=1)
    for i in range(2):
        est.record_sample(i, wall_seconds=6.0, video_seconds=6.0)

    start = est.display_eta()
    assert start == pytest.approx(2 * HOUR, rel=0.01)

    # Completing in exactly the sampled time keeps the calibration at 1.0, so
    # the raw estimate simply halves.
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - HOUR
    est.note_complete(0)

    fallen = est.display_eta()
    assert fallen == pytest.approx(start + (HOUR - start) * ETA_ALPHA_DOWN, rel=0.01)


def test_eta_eases_up_more_slowly_than_down():
    est = EncodeEstimator(make_files(1), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)

    start = est.display_eta()
    assert start == pytest.approx(HOUR, rel=0.01)

    # A second, much slower window on the same file raises its pooled cost.
    est.record_sample(0, wall_seconds=60.0, video_seconds=6.0)
    raw_after = ((6.0 + 60.0) / (6.0 + 6.0)) * HOUR

    risen = est.display_eta()
    assert risen == pytest.approx(start + (raw_after - start) * ETA_ALPHA_UP, rel=0.01)

    # The rise closed a far smaller share of its gap than a fall would have.
    assert ETA_ALPHA_UP < ETA_ALPHA_DOWN


def test_eta_never_goes_backwards_to_none():
    est = EncodeEstimator(make_files(1), codec_cap=1)
    est.record_sample(0, wall_seconds=6.0, video_seconds=6.0)
    assert est.display_eta() is not None
    est.note_start(0)
    # Running but with no usable progress yet: the previous value must hold
    # rather than the segment blinking out of the status line.
    assert est.display_eta() is not None


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (1, "1s"),
    (59, "59s"),
    (60, "1m"),
    (3599, "59m"),            # minutes right up to the hour boundary
    (3600, "1h"),
    (3660, "1h"),             # a single unit only: no trailing minutes
    (7830, "2h"),
    (86399, "23h"),
    (86400, "24h"),           # hours accumulate rather than rolling into days
    (90000, "25h"),
    (180000, "50h"),
])
def test_format_duration_short(seconds, expected):
    assert format_duration_short(seconds) == expected


def test_format_duration_short_handles_none_and_negatives():
    assert format_duration_short(None) == ""
    assert format_duration_short(-5) == "0s"


@pytest.mark.parametrize("seconds,with_and,with_commas", [
    (0, "0 seconds", "0 seconds"),
    (1, "1 second", "1 second"),
    (3600, "1 hour", "1 hour"),
    (61, "1 minute and 1 second", "1 minute, 1 second"),
    (252, "4 minutes and 12 seconds", "4 minutes, 12 seconds"),
    (144133, "1 day, 16 hours, 2 minutes and 13 seconds",
             "1 day, 16 hours, 2 minutes, 13 seconds"),
])
def test_format_time_join_styles(seconds, with_and, with_commas):
    # The encoder summary wants a plain list; "Processing took ..." keeps the
    # sentence, so the default must stay the conjunction form.
    assert format_time(seconds) == with_and
    assert format_time(seconds, conjunction=True) == with_and
    assert format_time(seconds, conjunction=False) == with_commas
