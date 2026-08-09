"""Tests for the byte-based time estimate used by the I/O-bound stages.

Copying into TEMP, filtering audio tracks and moving to the destination are not
CPU-bound the way encoding is - they are bytes through a disk. ThroughputEstimator
divides what is left by the observed rate; ByteProgress supplies the "done so
far" figure, counting partially written destination files so a single huge file
is not a blind spot.

Time is injected and byte counts come from a holder, so nothing here sleeps and
nothing needs real media.
"""

import threading

import pytest

from modules.file_operations import total_file_size
from modules.misc import ByteProgress, ThroughputEstimator, eta_chip

MB = 1 << 20
GB = 1 << 30


class _Bytes:
    """A mutable done-bytes source, standing in for ByteProgress.done_bytes."""

    def __init__(self, value=0):
        self.value = value

    def __call__(self):
        return self.value


def feed(est, done, points):
    """Replay (seconds, bytes-done) points; return the ETA after the last."""
    out = None
    for when, value in points:
        done.value = value
        out = est.display_eta(now=when)
    return out


# --- ThroughputEstimator ------------------------------------------------------

def test_one_reading_is_a_position_not_a_rate():
    done = _Bytes()
    est = ThroughputEstimator(100 * MB, done)
    assert est.display_eta(now=0.0) is None


def test_a_stalled_transfer_reads_as_unknown_not_as_finished():
    done = _Bytes()
    est = ThroughputEstimator(100 * MB, done)
    assert feed(est, done, [(0.0, 0), (5.0, 0), (10.0, 0)]) is None


def test_the_rate_gives_the_remaining_time():
    # 10MB/s with 900MB still to go.
    done = _Bytes()
    est = ThroughputEstimator(1000 * MB, done)
    assert feed(est, done, [(0.0, 0), (10.0, 100 * MB)]) == pytest.approx(90.0, rel=0.01)


def test_a_faster_disk_yields_a_shorter_estimate():
    slow_done, fast_done = _Bytes(), _Bytes()
    slow = ThroughputEstimator(1000 * MB, slow_done)
    fast = ThroughputEstimator(1000 * MB, fast_done)
    slow_eta = feed(slow, slow_done, [(0.0, 0), (10.0, 50 * MB)])
    fast_eta = feed(fast, fast_done, [(0.0, 0), (10.0, 200 * MB)])
    assert fast_eta < slow_eta


def test_an_empty_stage_has_nothing_to_estimate():
    done = _Bytes()
    est = ThroughputEstimator(0, done)
    assert est.display_eta(now=0.0) is None
    assert feed(est, done, [(0.0, 0), (10.0, 0)]) is None


def test_completion_reads_as_zero():
    done = _Bytes()
    est = ThroughputEstimator(100 * MB, done)
    assert feed(est, done, [(0.0, 0), (10.0, 100 * MB)]) == 0.0


def test_done_bytes_beyond_the_total_do_not_go_negative():
    """A partial-size read can race a finish() and over-report."""
    done = _Bytes()
    est = ThroughputEstimator(100 * MB, done)
    assert feed(est, done, [(0.0, 0), (10.0, 500 * MB)]) == 0.0


def test_the_estimate_falls_quickly_and_rises_slowly():
    """Same easing rationale as the encoder's: a countdown that jumps upward
    costs more trust than one converging from below."""
    down_done, up_done = _Bytes(), _Bytes()
    down = ThroughputEstimator(1000 * MB, down_done)
    up = ThroughputEstimator(1000 * MB, up_done)

    # Both settle at the same figure, then the disk changes speed.
    first_down = feed(down, down_done, [(0.0, 0), (10.0, 100 * MB)])
    first_up = feed(up, up_done, [(0.0, 0), (10.0, 100 * MB)])
    assert first_down == pytest.approx(first_up)

    speeding_up = feed(down, down_done, [(11.0, 400 * MB)])
    slowing_down = feed(up, up_done, [(11.0, 101 * MB)])

    # The drop moves further from the old value than the rise does.
    assert first_down - speeding_up > slowing_down - first_up


def test_the_rate_tracks_a_window_not_the_whole_run():
    """A stage that starts on cache speed and settles to disk speed must stop
    quoting the cache figure."""
    done = _Bytes()
    est = ThroughputEstimator(10 * GB, done)
    # A fast first second, then a long slow stretch past the window.
    points = [(0.0, 0), (1.0, 1 * GB)]
    points += [(float(t), 1 * GB + t * 10 * MB) for t in range(2, 40)]
    eta = feed(est, done, points)
    # At ~10MB/s with several GB left the answer is hundreds of seconds, not the
    # tens the opening burst would have implied.
    assert eta > 300


# --- ByteProgress -------------------------------------------------------------

def test_finished_units_accumulate():
    p = ByteProgress(300)
    p.finish('a', 100)
    p.finish('b', 50)
    assert p.done_bytes() == 150


def test_an_in_flight_file_counts_what_is_on_disk(tmp_path):
    """The point of the whole thing: a 40GB copy is not a blind spot."""
    dest = tmp_path / "big.mkv"
    p = ByteProgress(1000)
    p.start('big', str(dest))
    assert p.done_bytes() == 0

    dest.write_bytes(b'x' * 400)
    assert p.done_bytes() == 400

    dest.write_bytes(b'x' * 900)
    assert p.done_bytes() == 900


def test_a_destination_that_does_not_exist_yet_counts_nothing(tmp_path):
    p = ByteProgress(1000)
    p.start('pending', str(tmp_path / "not-created-yet.mkv"))
    assert p.done_bytes() == 0


def test_finishing_replaces_the_partial_with_the_real_size(tmp_path):
    dest = tmp_path / "f.mkv"
    dest.write_bytes(b'x' * 400)
    p = ByteProgress(1000)
    p.start('f', str(dest))
    p.finish('f', 500)
    # Counted once at its true size, not 400 + 500.
    assert p.done_bytes() == 500


def test_done_never_exceeds_the_total(tmp_path):
    """A partial read racing a finish() must not push the figure past 100%."""
    dest = tmp_path / "f.mkv"
    dest.write_bytes(b'x' * 900)
    p = ByteProgress(1000)
    p.start('f', str(dest))
    p.finish('other', 800)
    assert p.done_bytes() == 1000


def test_a_total_can_be_built_up_as_the_work_is_discovered():
    """The encoder's post-encode move delivers encoded files, whose sizes are
    only known as each encode finishes."""
    p = ByteProgress()
    assert p.total_bytes() == 0
    p.add_total(400)
    p.add_total(600)
    assert p.total_bytes() == 1000
    p.finish('a', 400)
    assert p.done_bytes() == 400


def test_an_unsized_stage_still_reports_finished_bytes():
    """With no total there is nothing to cap against, but the counter is real."""
    p = ByteProgress()
    p.finish('a', 700)
    assert p.done_bytes() == 700


def test_concurrent_finishes_are_not_lost():
    p = ByteProgress(8 * 200)

    def work():
        for i in range(200):
            p.finish(f"{threading.get_ident()}-{i}", 1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert p.done_bytes() == 8 * 200


# --- total_file_size ----------------------------------------------------------

def test_total_file_size_sums_only_files(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b'x' * 100)
    (tmp_path / "b.mkv").write_bytes(b'x' * 250)
    (tmp_path / "sub").mkdir()
    assert total_file_size(str(tmp_path), ["a.mkv", "b.mkv", "sub"]) == 350


def test_total_file_size_ignores_missing_entries(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b'x' * 100)
    assert total_file_size(str(tmp_path), ["a.mkv", "gone.mkv"]) == 100


def test_total_file_size_of_nothing_is_zero(tmp_path):
    assert total_file_size(str(tmp_path), []) == 0


# --- the shared chip ----------------------------------------------------------

def test_the_chip_is_shared_with_the_other_stages():
    done = _Bytes()
    est = ThroughputEstimator(1000 * MB, done)
    feed(est, done, [(0.0, 0), (10.0, 100 * MB)])
    assert eta_chip(est) in ("[1m]", "[90s]")


def test_no_chip_without_an_estimate():
    done = _Bytes()
    assert eta_chip(ThroughputEstimator(1000 * MB, done)) == ""
    assert eta_chip(None) == ""


# --- uncompressed_size --------------------------------------------------------

def test_an_archive_is_weighed_by_what_it_expands_to(tmp_path):
    """Sizing an extraction by bytes on disk would mis-weight it badly: a
    compressed archive writes far more than it occupies."""
    import zipfile
    from modules.file_operations import uncompressed_size

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("big.bin", b"0" * 500_000)
        handle.writestr("sub/small.txt", b"x" * 10)

    assert uncompressed_size(str(archive)) == 500_010
    assert archive.stat().st_size < 10_000        # the point of the exercise


def test_an_unreadable_archive_falls_back_to_its_size_on_disk(tmp_path):
    """A wrong weighting still lets the stage show an estimate; a zero would
    leave the whole batch without one."""
    from modules.file_operations import uncompressed_size

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not really a zip file")
    assert uncompressed_size(str(broken)) == len(b"not really a zip file")


def test_a_missing_archive_weighs_nothing(tmp_path):
    from modules.file_operations import uncompressed_size
    assert uncompressed_size(str(tmp_path / "gone.zip")) == 0


# --- repack_output_size -------------------------------------------------------

def test_repack_is_weighed_by_source_plus_the_tracks_merged_in(tmp_path):
    """The mux writes the source plus every track being added, which with
    several audio preferences is a good deal more than the source alone."""
    from modules.mkv import repack_output_size
    from modules.models import MediaFile

    (tmp_path / "ep.mkv").write_bytes(b"m" * 1000)
    (tmp_path / "a1.eac3").write_bytes(b"a" * 300)
    (tmp_path / "s1.srt").write_bytes(b"s" * 50)

    item = MediaFile(name="ep.mkv")
    item.audio_tracks_to_merge = {"audio_paths": [str(tmp_path / "a1.eac3")]}
    item.subtitle_tracks_to_merge = {"sub_paths": [str(tmp_path / "s1.srt")]}

    assert repack_output_size(item, str(tmp_path)) == 1350


def test_repack_size_survives_empty_and_missing_track_lists(tmp_path):
    from modules.mkv import repack_output_size
    from modules.models import MediaFile

    (tmp_path / "ep.mkv").write_bytes(b"m" * 1000)
    item = MediaFile(name="ep.mkv")
    assert repack_output_size(item, str(tmp_path)) == 1000

    item.audio_tracks_to_merge = {"audio_paths": None}
    item.subtitle_tracks_to_merge = {"sub_paths": [str(tmp_path / "gone.srt")]}
    assert repack_output_size(item, str(tmp_path)) == 1000
