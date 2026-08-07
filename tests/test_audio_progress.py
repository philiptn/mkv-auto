"""Tests for the audio stage's batch time estimate.

The audio stage reuses EncodeEstimator with audio-shaped records: the 'pixels'
normaliser becomes channel count, 'is_4k' is always False, and there is no
sampler at all - the estimate comes purely from the observation path, fed by
ffmpeg's -progress output. These tests cover that reuse plus the three small
pieces built around it, all without media, ffmpeg or disk.
"""

import threading
import time

import pytest

from modules.audio import is_copy_only_preference
from modules.encode_estimator import ETA_MIN_FRACTION, EncodeEstimator
from modules.misc import RESET, make_batch_eta_line, parse_ffmpeg_out_time, remove_color_codes
from modules.mkv import AUDIO_DEFAULT_CHANNELS, AudioJobProgress, build_audio_jobs
from modules.models import AudioTrackCandidate, WantedAudioTracks

HOUR = 3600.0
STEREO = 2
SURROUND = 6


def make_audio_jobs(count, duration=HOUR, channels=SURROUND):
    """Audio-shaped estimator records, in the style of make_files() next door."""
    return [
        {
            'index': i,
            'name': f'movie.mkv#{i}:EAC3',
            'duration': duration,
            'width': channels,
            'height': 1,
            'is_4k': False,
            'threads': 1,
        }
        for i in range(count)
    ]


# --- the estimator's observation-only path ------------------------------------

def test_no_sampler_means_no_eta_until_a_job_reports():
    """The audio stage never samples, so nothing is knowable before work starts."""
    est = EncodeEstimator(make_audio_jobs(4), codec_cap=4)
    assert est.display_eta() is None


def test_one_running_job_prices_the_whole_batch():
    """The path the audio stage depends on: progress on one job, no samples."""
    est = EncodeEstimator(make_audio_jobs(4), codec_cap=1)
    est.set_slots(1)
    est.note_start(0)
    # 60s elapsed at 25% done -> 240s per job, 4 jobs on one slot minus what
    # job 0 has already burned.
    est._files[0]['started_at'] = time.time() - 60.0
    est.note_progress(0, 0.25)
    assert est.display_eta() == pytest.approx(180.0 + 3 * 240.0, rel=0.05)


def test_progress_below_the_trust_floor_is_ignored():
    est = EncodeEstimator(make_audio_jobs(2), codec_cap=1)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 5.0
    est.note_progress(0, ETA_MIN_FRACTION / 2)
    assert est.display_eta() is None


def test_a_finished_job_prices_the_pending_ones():
    """Even with no progress plumbing, a completed job seeds the rest."""
    est = EncodeEstimator(make_audio_jobs(3), codec_cap=1)
    est.set_slots(1)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 120.0
    est.note_complete(0)
    # Two jobs left at the measured 120s each, one slot.
    assert est.display_eta() == pytest.approx(240.0, rel=0.05)


def test_channels_normalise_cost_across_tracks():
    """A 5.1 track costs ~3x a stereo one of the same length.

    This is what makes width=channels the right analogue of the video path's
    width*height: one measured job prices differently-shaped pending ones.
    """
    jobs = make_audio_jobs(1, channels=STEREO) + make_audio_jobs(1, channels=SURROUND)
    jobs[1]['index'] = 1
    est = EncodeEstimator(jobs, codec_cap=1)
    est.set_slots(1)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 100.0
    est.note_complete(0)
    # The stereo job measured 100s; the 5.1 job has 3x the channels.
    assert est.display_eta() == pytest.approx(300.0, rel=0.05)


def test_slots_divide_the_remaining_work():
    est = EncodeEstimator(make_audio_jobs(8), codec_cap=4)
    est.set_slots(4)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 100.0
    est.note_complete(0)
    # 7 jobs x 100s over 4 slots, floored by the makespan of a single job.
    assert est.display_eta() == pytest.approx(700.0 / 4, rel=0.05)


def test_all_jobs_done_yields_zero():
    est = EncodeEstimator(make_audio_jobs(3), codec_cap=2)
    for i in range(3):
        est.note_start(i)
        est.note_complete(i)
    assert est.display_eta() == pytest.approx(0.0, abs=1.0)


def test_a_job_without_a_duration_is_skipped_not_fatal():
    """mkvmerge does not always report a duration. Such a job drops out of the
    estimate rather than breaking it, and the rest are still priced."""
    jobs = make_audio_jobs(3)
    jobs[1]['duration'] = None
    est = EncodeEstimator(jobs, codec_cap=1)
    est.set_slots(1)
    est.note_start(0)
    est._files[0]['started_at'] = time.time() - 60.0
    est.note_complete(0)
    # Job 2 is priced at the measured 60s; job 1 contributes nothing.
    assert est.display_eta() == pytest.approx(60.0, rel=0.05)


# --- ffmpeg -progress parsing -------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("out_time_ms=1500000\n", 1.5),
    ("out_time_ms=0\n", 0.0),
    ("out_time_ms=7200000000\n", 7200.0),
])
def test_out_time_is_read_in_microseconds(line, expected):
    """ffmpeg reports this key in microseconds despite the _ms name."""
    assert parse_ffmpeg_out_time(line) == pytest.approx(expected)


def test_not_available_yields_nothing():
    """What ffmpeg emits on its first tick, before a frame has been written."""
    assert parse_ffmpeg_out_time("out_time_ms=N/A\n") is None


def test_a_non_numeric_value_yields_nothing():
    assert parse_ffmpeg_out_time("out_time_ms=garbage\n") is None


@pytest.mark.parametrize("line", [
    "frame=124\n", "bitrate=  640.0kbits/s\n", "progress=continue\n",
    "out_time=00:00:01.500000\n", "\n",
])
def test_other_progress_keys_are_ignored(line):
    assert parse_ffmpeg_out_time(line) is None


# --- the rendered line --------------------------------------------------------

class _FixedEta:
    """Stands in for EncodeEstimator; only display_eta() is rendered."""

    def __init__(self, eta):
        self._eta = eta

    def display_eta(self):
        return self._eta


def _render(estimator, done=1, total=16):
    return make_batch_eta_line("AUDIO", "Process audio formats",
                               estimator, lambda: done, total)()


def test_the_eta_is_a_bracketed_chip():
    """It sits beside the other chips - "[AUDIO][21m]" - not as a bare word in
    the description."""
    assert remove_color_codes(_render(_FixedEta(21 * 60))) == \
        "[AUDIO][21m] Process audio formats (1/16) "


def test_the_eta_chip_is_inside_the_grey_stretch():
    """Everything up to RESET renders grey, so the chip must precede it."""
    line = _render(_FixedEta(21 * 60))
    assert line.index("[21m]") < line.index(RESET)


def test_no_chip_at_all_before_the_first_estimate():
    assert remove_color_codes(_render(_FixedEta(None))) == \
        "[AUDIO] Process audio formats (1/16) "


def test_no_estimator_renders_a_plain_line():
    assert remove_color_codes(_render(None)) == \
        "[AUDIO] Process audio formats (1/16) "


def test_the_counter_is_read_at_render_time():
    """done_fn is a callable because worker threads advance it while the
    spinner's render thread reads it."""
    done = [0]
    line = make_batch_eta_line("AUDIO", "Process audio formats",
                               None, lambda: done[0], 16)
    assert "(0/16)" in remove_color_codes(line())
    done[0] = 7
    assert "(7/16)" in remove_color_codes(line())


# --- the copy-vs-transcode predicate ------------------------------------------

@pytest.mark.parametrize("transformation,codec,expected", [
    (None, 'ORIG', True),      # plain passthrough
    (None, '', True),          # empty codec, whatever the transformation
    ('EOS', '', True),
    ('EOS', 'ORIG', False),    # a filter runs, so it is not a copy
    ('EOS+', 'ORIG', False),
    (None, 'AC3', False),
    ('EOS', 'EAC3', False),
])
def test_copy_only_predicate(transformation, codec, expected):
    """Locks the precedence of the expression this replaced:
    (codec == 'ORIG' and transformation is None) or codec == ''."""
    assert is_copy_only_preference(transformation, codec) is expected


# --- job list construction ----------------------------------------------------

def _probe(track_ids, duration=HOUR, channels=SURROUND, needs_processing=True):
    wanted = WantedAudioTracks(
        needs_processing=needs_processing,
        tracks_to_convert=[AudioTrackCandidate(track_id=t, language='eng') for t in track_ids],
    )
    return wanted, duration, {t: channels for t in track_ids}


def test_jobs_are_one_per_track_times_preference():
    probes = [_probe([1, 2])]
    prefs = [(None, 'EAC3', '5.1'), ('EOS', 'AC3', '5.1')]
    jobs, index_map, total = build_audio_jobs(['movie.mkv'], probes, prefs)
    assert total == 4
    assert len(jobs) == 4
    assert set(index_map) == {(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)}


def test_copies_count_in_the_total_but_are_not_priced():
    probes = [_probe([1])]
    prefs = [(None, 'EAC3', '5.1'), (None, 'ORIG', '')]
    jobs, index_map, total = build_audio_jobs(['movie.mkv'], probes, prefs)
    assert total == 2
    assert len(jobs) == 1
    assert index_map == {(0, 0, 0): 0}
    # The copy has no estimator index, so nothing prices it.
    assert (0, 0, 1) not in index_map


def test_files_needing_no_processing_contribute_nothing():
    probes = [_probe([1], needs_processing=False), _probe([1])]
    prefs = [(None, 'EAC3', '5.1')]
    jobs, index_map, total = build_audio_jobs(['a.mkv', 'b.mkv'], probes, prefs)
    assert total == 1
    assert list(index_map) == [(1, 0, 0)]


def test_missing_channel_counts_fall_back_rather_than_zero():
    wanted, duration, _ = _probe([1])
    jobs, _, _ = build_audio_jobs(['movie.mkv'], [(wanted, duration, {1: None})],
                                  [(None, 'EAC3', '5.1')])
    assert jobs[0]['width'] == AUDIO_DEFAULT_CHANNELS


def test_job_records_are_audio_shaped():
    jobs, _, _ = build_audio_jobs(['movie.mkv'], [_probe([1], channels=STEREO)],
                                  [(None, 'EAC3', 'Stereo')])
    assert jobs[0]['is_4k'] is False
    assert jobs[0]['threads'] == 1
    assert jobs[0]['height'] == 1
    assert jobs[0]['width'] == STEREO
    assert jobs[0]['duration'] == HOUR


def test_indices_are_contiguous_across_files():
    probes = [_probe([1]), _probe([1, 2])]
    prefs = [(None, 'EAC3', '5.1')]
    jobs, index_map, _ = build_audio_jobs(['a.mkv', 'b.mkv'], probes, prefs)
    assert [j['index'] for j in jobs] == [0, 1, 2]
    assert sorted(index_map.values()) == [0, 1, 2]


# --- the reporter -------------------------------------------------------------

def test_the_counter_survives_concurrent_finishes():
    """encode_single_preference runs on a nested pool, so several threads call
    finish() at once - a bare += would lose increments."""
    reporter = AudioJobProgress(None, {})
    threads = [threading.Thread(target=lambda: [reporter.finish(None) for _ in range(200)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert reporter.done() == 8 * 200


def test_a_copy_job_advances_the_counter_without_pricing():
    est = EncodeEstimator(make_audio_jobs(1), codec_cap=1)
    reporter = AudioJobProgress(est, {(0, 0, 0): 0})
    assert reporter.index_of(0, 0, 1) is None    # the copy
    reporter.finish(None)
    assert reporter.done() == 1
    assert est.display_eta() is None             # nothing was priced


def test_the_reporter_drives_the_estimator_for_priced_jobs():
    est = EncodeEstimator(make_audio_jobs(2), codec_cap=1)
    est.set_slots(1)
    reporter = AudioJobProgress(est, {(0, 0, 0): 0, (0, 0, 1): 1})

    job = reporter.index_of(0, 0, 0)
    reporter.start(job)
    est._files[job]['started_at'] = time.time() - 50.0
    reporter.advance(job, 0.5)
    # 50s for half of job 0, plus a full 100s for job 1.
    assert est.display_eta() == pytest.approx(150.0, rel=0.05)

    reporter.finish(job)
    assert reporter.done() == 1


def test_a_reporter_without_an_estimator_is_inert():
    """The all-copies batch still wants a counter, but has nothing to price."""
    reporter = AudioJobProgress(None, {})
    reporter.start(None)
    reporter.advance(None, 0.5)
    reporter.finish(None)
    assert reporter.done() == 1
