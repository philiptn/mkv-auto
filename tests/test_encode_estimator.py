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
from modules.misc import (
    RESET,
    OversizeWarning,
    ProgressState,
    format_duration_short,
    format_time,
    make_progress_line,
    remove_color_codes,
)

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


@pytest.mark.parametrize("seconds,expected", [
    (0, "0 seconds"),
    (19, "19 seconds"),
    (59, "59 seconds"),          # under a minute keeps seconds: "0 minutes" says nothing
    (61, "1 minute"),
    (3599, "1 hour"),            # rounding carries up through the units
    (6619, "1 hour, 50 minutes"),
    (6645, "1 hour, 51 minutes"),
    (144133, "1 day, 16 hours, 2 minutes"),
])
def test_format_time_without_seconds(seconds, expected):
    # The encoder summary drops seconds; second-level precision on an hours-long
    # encode is noise.
    assert format_time(seconds, conjunction=False, include_seconds=False) == expected


def test_format_time_flags_compose():
    assert format_time(6619, include_seconds=False) == "1 hour and 50 minutes"


# ----------------------------------------------------------------------
# OversizeWarning — the live "this encode is growing" chip
#
# update() is driven with an explicit `now` so the once-a-second sampling gate
# is deterministic; each tick below is one second and one render.
# ----------------------------------------------------------------------

def feed(warning, raw, ticks, start=1000.0):
    """Render `ticks` successive seconds at a steady reading; return the last chip."""
    out = ""
    for i in range(ticks):
        out = warning.update(raw, now=start + i)
    return out


def test_shrinking_encode_shows_nothing():
    # The expected outcome, and the whole point of the chip: it stays silent.
    w = OversizeWarning()
    assert feed(w, 35.0, 30) == ""


def test_sustained_growth_warns():
    w = OversizeWarning()
    assert feed(w, -8.0, 30) == "~8% BIGGER "


def test_growth_below_the_threshold_stays_silent():
    # 3% over is within the noise an encoder wanders through; not worth a chip.
    w = OversizeWarning()
    assert feed(w, -3.0, 30) == ""


def test_warning_holds_through_the_hysteresis_band():
    w = OversizeWarning()
    assert feed(w, -8.0, 30) != ""
    # Recovering to 4.5% over is above the show threshold but not yet clear of
    # the hide threshold, so the chip must not blink off.
    assert feed(w, -4.5, 60, start=2000.0).endswith("BIGGER ")


def test_warning_clears_once_growth_recedes():
    w = OversizeWarning()
    assert feed(w, -8.0, 30) != ""
    assert feed(w, -2.0, 60, start=2000.0) == ""


def test_no_reading_renders_nothing_and_keeps_state():
    w = OversizeWarning()
    assert feed(w, -8.0, 30) != ""
    assert w.update(None) == ""
    # The gap left no mark: the next real reading picks up where it left off.
    assert w.update(-8.0, now=1100.0) == "~8% BIGGER "


def test_a_burst_of_renders_within_one_second_samples_once():
    # The spinner redraws far faster than once a second; a spike arriving in
    # that window must not be able to stuff the median window with itself.
    w = OversizeWarning()
    feed(w, -8.0, 30)
    for _ in range(100):
        chip = w.update(50.0, now=1029.0)
    assert chip.endswith("BIGGER ")


# --------------------------------------------------------------------------
# the encoder progress line
#
# get_cpu_temp_cached() reads real hardware, so these patch it out and assert
# on the colour-stripped line. The batch ETA is a chip inside the grey stretch
# beside the temperature; the per-worker chips further along keep their own
# colour and are deliberately not bracketed.
# --------------------------------------------------------------------------

class _FixedEta:
    def __init__(self, eta=None):
        self._eta = eta

    def display_eta(self):
        return self._eta


def render_encoder_line(monkeypatch, estimator, total=8, done=2, temp=62.0):
    import modules.misc as misc
    monkeypatch.setattr(misc, "get_cpu_temp_cached", lambda *a, **k: temp)
    progress = ProgressState(total, 2)
    for wid in range(2):
        progress.start_worker(wid)
    progress.completed_files = done
    return make_progress_line(progress, "ENCODER", "Encoding", time.time(),
                              estimator)()


def test_batch_eta_is_a_bracketed_chip(monkeypatch):
    line = render_encoder_line(monkeypatch, _FixedEta(eta=46 * 60))
    assert remove_color_codes(line).startswith("[ENCODER][CPU 62°C][46m] ")


def test_batch_eta_chip_sits_inside_the_grey_stretch(monkeypatch):
    line = render_encoder_line(monkeypatch, _FixedEta(eta=46 * 60))
    assert line.index("[46m]") < line.index(RESET)


def test_no_chip_before_the_first_estimate(monkeypatch):
    """Nothing at all while sampling is still working the figure out — no
    "Estimating..." placeholder taking up room on an already busy line."""
    line = render_encoder_line(monkeypatch, _FixedEta())
    assert remove_color_codes(line).startswith("[ENCODER][CPU 62°C] ")


def test_no_estimator_renders_no_chip(monkeypatch):
    line = render_encoder_line(monkeypatch, None)
    assert remove_color_codes(line).startswith("[ENCODER][CPU 62°C] ")


def test_the_last_file_in_flight_drops_the_batch_chip(monkeypatch):
    """One worker left on the final file: the batch figure would just repeat
    that worker's own chip."""
    import modules.misc as misc
    monkeypatch.setattr(misc, "get_cpu_temp_cached", lambda *a, **k: 62.0)
    progress = ProgressState(3, 1)
    progress.start_worker(0)
    progress.completed_files = 2
    line = make_progress_line(progress, "ENCODER", "Encoding", time.time(),
                              _FixedEta(eta=46 * 60))()
    assert "[46m]" not in line


def test_per_worker_chips_are_not_bracketed(monkeypatch):
    """The per-worker readouts keep the shape they always had."""
    line = remove_color_codes(render_encoder_line(monkeypatch, _FixedEta(eta=60)))
    assert line.endswith("(2/8) ")
    assert "┃0%" in line
