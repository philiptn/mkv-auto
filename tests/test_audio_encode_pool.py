"""Tests for the audio stage's shared encode pool.

The stage splits the available threads evenly across files, which integer-divides
to one thread per file as soon as there are at least as many files as threads. A
private pool per file then ran that file's (track x preference) jobs one at a
time, so a 20-episode batch used 12.7 of 32 cores while 19 slots were nominally
available. Sharing a single pool across all files lets every file's jobs compete
for the whole budget.

No ffmpeg here - encode_single_preference is stubbed. What is pinned is which
pool the jobs are submitted to, that the pool survives the call, and that results
still come back in preference order.
"""

import concurrent.futures
import threading
import time

import pytest

from modules import audio
from modules.models import AudioTrack


def tracks(*names):
    return [AudioTrack(path=f"/tmp/{n}.ac3", track_id=1, language="eng",
                       name="", extension="ac3") for n in names]


@pytest.fixture(autouse=True)
def no_probe(monkeypatch):
    monkeypatch.setattr(audio, "detect_source_channels_and_layout", lambda debug, path: (6, "5.1"))


def stub_encode(monkeypatch, record=None, delay=0.0):
    def encode(audio_track, debug, transformation, codec, ch_str, custom_opts,
               source_channels=None, source_layout=None, duration=None,
               job=None, reporter=None):
        if delay:
            time.sleep(delay)
        if record is not None:
            record.append(threading.current_thread().name)
        return AudioTrack(path=f"{audio_track.path}.{codec}", track_id=1,
                          language="eng", name=codec, extension=codec.lower())
    monkeypatch.setattr(audio, "encode_single_preference", encode)


def test_jobs_run_on_the_supplied_pool(monkeypatch):
    """Every job must land on the shared pool's threads, not a private one."""
    seen = []
    stub_encode(monkeypatch, record=seen)

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="sharedpool") as pool:
        audio.encode_audio_tracks(1, False, tracks("a"), "DTS, AC3, EAC3",
                                  encode_pool=pool)

    assert seen and all(name.startswith("sharedpool") for name in seen)


def test_the_shared_pool_is_left_open_for_the_next_file(monkeypatch):
    """encode_audio_tracks runs once per file; shutting the shared pool down at
    the end of the first file would break every file after it."""
    stub_encode(monkeypatch)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        first = audio.encode_audio_tracks(1, False, tracks("a"), "DTS", encode_pool=pool)
        second = audio.encode_audio_tracks(1, False, tracks("b"), "DTS", encode_pool=pool)

    assert len(first) == 1 and len(second) == 1


def test_the_shared_pool_survives_a_failing_job(monkeypatch):
    """A file whose encode raises must not take the pool down with it."""
    def boom(*a, **k):
        raise RuntimeError("encode failed")

    monkeypatch.setattr(audio, "encode_single_preference", boom)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        with pytest.raises(RuntimeError):
            audio.encode_audio_tracks(1, True, tracks("a"), "DTS", encode_pool=pool)

        stub_encode(monkeypatch)
        assert len(audio.encode_audio_tracks(1, False, tracks("b"), "DTS", encode_pool=pool)) == 1


def test_without_a_shared_pool_a_private_one_is_used_and_closed(monkeypatch):
    """The standalone path (and every existing caller) keeps working."""
    seen = []
    stub_encode(monkeypatch, record=seen)

    result = audio.encode_audio_tracks(2, False, tracks("a"), "DTS, AC3")

    assert len(result) == 2
    assert seen and not any(name.startswith("sharedpool") for name in seen)


def test_results_stay_in_preference_order_on_a_shared_pool(monkeypatch):
    """Jobs from several files interleave on the shared pool, so completion
    order says nothing about preference order - the caller still needs the
    preferences back in the order it asked for them."""
    def encode(audio_track, debug, transformation, codec, ch_str, custom_opts,
               source_channels=None, source_layout=None, duration=None,
               job=None, reporter=None):
        # Make the last preference finish first.
        time.sleep({"DTS": 0.05, "AC3": 0.02, "EAC3": 0.0}[codec])
        return AudioTrack(path=f"{audio_track.path}.{codec}", track_id=1,
                          language="eng", name=codec, extension=codec.lower())

    monkeypatch.setattr(audio, "encode_single_preference", encode)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        result = audio.encode_audio_tracks(1, False, tracks("a"), "DTS, AC3, EAC3",
                                           encode_pool=pool)

    assert [t.name for t in result] == ["DTS", "AC3", "EAC3"]


def test_several_files_share_the_budget(monkeypatch):
    """The point of the change: with more files than the per-file split allows,
    concurrency comes from the pool size, not from files x 1."""
    running = []
    peak = [0]
    lock = threading.Lock()

    def encode(audio_track, debug, transformation, codec, ch_str, custom_opts,
               source_channels=None, source_layout=None, duration=None,
               job=None, reporter=None):
        with lock:
            running.append(1)
            peak[0] = max(peak[0], len(running))
        time.sleep(0.05)
        with lock:
            running.pop()
        return AudioTrack(path=audio_track.path, track_id=1, language="eng",
                          name=codec, extension="dts")

    monkeypatch.setattr(audio, "encode_single_preference", encode)

    # Four files, each allotted internal_threads=1 by the even split, three
    # preferences apiece - twelve jobs, and a budget of twelve to run them in.
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        def one_file(index):
            return audio.encode_audio_tracks(1, False, tracks(f"f{index}"),
                                             "DTS, AC3, EAC3", encode_pool=pool)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as files:
            list(files.map(one_file, range(4)))

    # A private pool of internal_threads=1 per file would cap this at 4.
    assert peak[0] > 4
