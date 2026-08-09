"""Tests for the per-file MediaFile records that replaced the index-parallel lists.

The stages used to communicate through one list per fact, all kept parallel to
the list of filenames and stitched back together by position. Nothing enforced
that alignment: a stage returning results in a different order would attach
episode 2's subtitles to episode 1, and the run would still report success.

These tests pin the property that replaced it - a result can only ever be filed
against the record it was computed from - by making the workers finish in an
order deliberately unrelated to submission order. They also cover the OCR retry
pass, whose write rules the end-to-end harness cannot reach because it needs a
genuine OCR failure.

Every worker here is a stub; no media, no mkvmerge, no OCR, no disk.
"""

import random
import threading
import time

import pytest

from modules import mkv
from modules.models import MediaFile, SubtitleTrack


def track(path, extension="srt", language="eng"):
    return SubtitleTrack(path=path, track_id=1, language=language, extension=extension)


def records(*names):
    return [MediaFile(name=name) for name in names]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Silence the progress lines and debug logging the stages emit."""
    monkeypatch.setattr(mkv, "print_with_progress", lambda *a, **k: None)
    monkeypatch.setattr(mkv, "custom_print", lambda *a, **k: None)
    monkeypatch.setattr(mkv, "custom_print_no_newline", lambda *a, **k: None)
    monkeypatch.setattr(mkv, "print_no_timestamp", lambda *a, **k: None)
    monkeypatch.setattr(mkv, "log_debug", lambda *a, **k: None)


@pytest.fixture
def ocr_threads(monkeypatch):
    monkeypatch.setattr(mkv, "get_max_ocr_threads", lambda: (4, 1024, 4096))
    monkeypatch.setattr(mkv, "get_worker_thread_count", lambda: 4)


# --- subs_langs_satisfied -----------------------------------------------------

@pytest.mark.parametrize("langs, satisfied", [
    (["none"], True),      # nothing was missing to begin with
    ([""], True),          # a stage that reports "nothing" as a blank
    ([], True),            # a stage that reports "nothing" as an empty list
    (["eng"], False),
    (["none", "eng"], False),
    (["", "eng"], False),
])
def test_subs_langs_satisfied_covers_all_three_spellings_of_nothing(langs, satisfied):
    """The stages spell "nothing missing" three ways; all must read alike."""
    assert MediaFile(name="a.mkv", missing_subs_langs=langs).subs_langs_satisfied is satisfied


# --- convert_to_srt_process ---------------------------------------------------

def test_conversion_results_land_on_the_file_they_came_from(monkeypatch, ocr_threads):
    """Workers finishing in reverse order must not shuffle results between files.

    This is the exact failure the index-parallel lists allowed: rc=0, no error,
    wrong subtitles on the wrong episode.

    Every value the stub returns is derived from the *tracks it was handed*, not
    from the filename it was told to work on. That makes the assertions fail for
    either half of a mis-pairing - a result filed against the wrong record, or
    the right record handed another file's subtitles.
    """
    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.subtitle_files = [track(f"e{index:02d}.eng.srt")]

    order = {"e01.mkv": 0.06, "e02.mkv": 0.03, "e03.mkv": 0.0}

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        time.sleep(order[input_file])
        stem = subtitle_files[0].path.split(".")[0]
        return ({stem: "repack"}, [track(f"{stem}.out.srt")], [track(f"{stem}.all.srt")],
                [], [], [f"{stem}-lang"])

    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    mkv.convert_to_srt_process(None, False, media, "/tmp", False)

    for index, item in enumerate(media, 1):
        stem = f"e{index:02d}"
        assert item.subtitle_tracks_to_merge == {stem: "repack"}
        assert item.subs_to_process[0].path == f"{stem}.out.srt"
        assert item.subs_all[0].path == f"{stem}.all.srt"
        assert item.missing_subs_langs == [f"{stem}-lang"]


def test_each_file_is_converted_with_its_own_subtitles(monkeypatch, ocr_threads):
    """The filename a worker is given and the tracks it is given must belong to
    the same file. Rotating one against the other is silent otherwise: the OCR
    output is written by track path, so it lands on another episode's subtitles
    while every record still looks populated."""
    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.subtitle_files = [track(f"e{index:02d}.eng.srt")]

    seen = []
    lock = threading.Lock()

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        time.sleep(random.uniform(0, 0.02))
        with lock:
            seen.append((input_file, subtitle_files[0].path))
        return ({}, [], [], [], [], [])

    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    mkv.convert_to_srt_process(None, False, media, "/tmp", False)

    assert sorted(seen) == [
        ("e01.mkv", "e01.eng.srt"),
        ("e02.mkv", "e02.eng.srt"),
        ("e03.mkv", "e03.eng.srt"),
    ]


def test_the_ocr_retry_reads_the_failures_and_keeps_the_first_pass_findings(monkeypatch, ocr_threads):
    """The retry re-runs only the tracks that failed, so it must not overwrite
    subs_all / errored_ocr / subtitle_tracks_to_merge - the first pass's results
    for the tracks that did convert are still the ones that count."""
    item = MediaFile(name="movie.mkv")
    item.subtitle_files = [track("movie.eng.sup", extension="sup")]
    item.subs_all = [track("first-pass-all.srt")]
    item.errored_ocr = [track("movie.eng.sup", extension="sup")]
    item.subtitle_tracks_to_merge = {"first": "pass"}
    item.subs_to_process = [track("first-pass-process.srt")]

    seen = {}

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        seen["tracks"] = list(subtitle_files)
        return ({"retry": "pass"}, [track("retry.srt")], [track("retry-all.srt")],
                [], [], ["none"])

    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    mkv.convert_to_srt_process(None, False, [item], "/tmp", True)

    # The retry pass is fed the failures, not everything staged.
    assert [t.path for t in seen["tracks"]] == ["movie.eng.sup"]

    assert [t.path for t in item.retry_subs_to_process] == ["retry.srt"]
    assert item.missing_subs_langs == ["none"]

    assert [t.path for t in item.subs_all] == ["first-pass-all.srt"]
    assert [t.path for t in item.subs_to_process] == ["first-pass-process.srt"]
    assert [t.path for t in item.errored_ocr] == ["movie.eng.sup"]
    assert item.subtitle_tracks_to_merge == {"first": "pass"}


def test_the_first_pass_reads_everything_staged(monkeypatch, ocr_threads):
    item = MediaFile(name="movie.mkv")
    item.subtitle_files = [track("a.srt"), track("b.sup", extension="sup")]
    item.errored_ocr = [track("should-not-be-read.sup", extension="sup")]

    seen = {}

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        seen["tracks"] = list(subtitle_files)
        return ({}, [], [], [], [], [])

    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    mkv.convert_to_srt_process(None, False, [item], "/tmp", False)

    assert [t.path for t in seen["tracks"]] == ["a.srt", "b.sup"]


def test_tracks_the_converter_cannot_read_are_filtered_out(monkeypatch, ocr_threads):
    item = MediaFile(name="movie.mkv")
    item.subtitle_files = [track("keep.srt"), track("drop.idx", extension="idx"), None]

    seen = {}

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        seen["tracks"] = list(subtitle_files)
        return ({}, [], [], [], [], [])

    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    mkv.convert_to_srt_process(None, False, [item], "/tmp", False)

    assert [t.path for t in seen["tracks"]] == ["keep.srt"]


def test_a_worker_returning_none_leaves_the_record_alone(monkeypatch, ocr_threads):
    """None means "I learnt nothing", not "wipe what the earlier stages found"."""
    item = MediaFile(name="movie.mkv", missing_subs_langs=["eng"])
    item.subtitle_files = [track("a.srt")]
    item.subs_all = [track("existing.srt")]

    monkeypatch.setattr(
        mkv, "convert_to_srt_process_worker",
        lambda *a, **k: (None, None, None, None, None, None))
    mkv.convert_to_srt_process(None, False, [item], "/tmp", False)

    assert [t.path for t in item.subs_all] == ["existing.srt"]
    assert item.missing_subs_langs == ["eng"]


def test_a_failing_worker_names_the_file_it_was_given(monkeypatch, ocr_threads):
    """The error report has to identify the right file, which is only true if
    the record travels with the future."""
    media = records("e01.mkv", "e02.mkv")
    for item in media:
        item.subtitle_files = [track(f"{item.name}.srt")]

    def worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
        if input_file == "e02.mkv":
            raise RuntimeError("boom")
        return ({}, [], [], [], [], [])

    reported = []
    monkeypatch.setattr(mkv, "convert_to_srt_process_worker", worker)
    monkeypatch.setattr(mkv, "print_no_timestamp", lambda logger, msg: reported.append(msg))

    with pytest.raises(RuntimeError):
        mkv.convert_to_srt_process(None, False, media, "/tmp", False)

    assert any("e02.mkv" in msg for msg in reported)


# --- get_subtitle_tracks_metadata_for_repack ----------------------------------

def test_repack_metadata_is_rebuilt_per_file(monkeypatch, ocr_threads):
    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.subtitle_files = [track(f"e{index:02d}.srt")]
        item.subtitle_tracks_to_merge = {"stale": True}

    monkeypatch.setattr(
        mkv, "return_subtitle_metadata_worker",
        lambda tracks, threads: {"from": tracks[0].path})

    mkv.get_subtitle_tracks_metadata_for_repack(None, media)

    for index, item in enumerate(media, 1):
        assert item.subtitle_tracks_to_merge == {"from": f"e{index:02d}.srt"}


# --- remove_sdh_process -------------------------------------------------------

def test_sdh_removal_gets_each_file_its_own_subtitles(monkeypatch, ocr_threads):
    monkeypatch.setattr(mkv, "check_config", lambda *a, **k: True)

    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.subs_to_process = [track(f"e{index:02d}.srt")]

    seen = []
    lock = threading.Lock()

    def worker(logger, debug, input_subtitles, internal_threads, memory_per_thread):
        time.sleep(random.uniform(0, 0.02))
        with lock:
            seen.append(input_subtitles[0].path)
        return ["one-replacement"]

    monkeypatch.setattr(mkv, "remove_sdh_process_worker", worker)
    count = mkv.remove_sdh_process(None, False, media)

    assert sorted(seen) == ["e01.srt", "e02.srt", "e03.srt"]
    assert count == 3


# --- repack_mkv_tracks_process ------------------------------------------------

def test_repack_muxes_each_file_with_its_own_tracks(monkeypatch, ocr_threads):
    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.audio_tracks_to_merge = {"audio": index}
        item.subtitle_tracks_to_merge = {"subs": index}

    seen = []
    lock = threading.Lock()

    def worker(debug, input_file, dirpath, audio_tracks, subtitle_tracks,
               progress=None, expected_bytes=0):
        time.sleep(random.uniform(0, 0.02))
        with lock:
            seen.append((input_file, audio_tracks, subtitle_tracks))

    monkeypatch.setattr(mkv, "repack_mkv_tracks_process_worker", worker)
    mkv.repack_mkv_tracks_process(None, False, media, "/tmp")

    assert sorted(seen) == [
        ("e01.mkv", {"audio": 1}, {"subs": 1}),
        ("e02.mkv", {"audio": 2}, {"subs": 2}),
        ("e03.mkv", {"audio": 3}, {"subs": 3}),
    ]


# --- fetch_missing_subtitles_process ------------------------------------------

def test_downloads_land_on_the_file_that_asked_for_them(monkeypatch, tmp_path, ocr_threads):
    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    for index, item in enumerate(media, 1):
        item.missing_subs_langs = [f"lang{index}"]

    monkeypatch.setattr(mkv, "to_alpha2", lambda lang: lang)

    seen = []
    lock = threading.Lock()

    # Named after the language it was asked for, so a record that gets another
    # file's language - or another file's download - fails the assertion.
    def worker(debug, input_file, dirpath, missing_subs_langs, internal_threads, logger):
        time.sleep(random.uniform(0, 0.02))
        with lock:
            seen.append((input_file, list(missing_subs_langs)))
        return [track(f"{missing_subs_langs[0]}.downloaded.srt")], [], [input_file], []

    monkeypatch.setattr(mkv, "fetch_missing_subtitles_process_worker", worker)
    mkv.fetch_missing_subtitles_process(None, False, media, str(tmp_path))

    assert sorted(seen) == [("e01.mkv", ["lang1"]), ("e02.mkv", ["lang2"]), ("e03.mkv", ["lang3"])]
    for index, item in enumerate(media, 1):
        assert [t.path for t in item.downloaded_subs] == [f"lang{index}.downloaded.srt"]


def test_a_language_already_covered_externally_is_not_downloaded(monkeypatch, tmp_path, ocr_threads):
    item = MediaFile(name="movie.mkv", missing_subs_langs=["eng", "jpn"])
    item.external_subs = [track("movie.eng.srt", language="eng")]

    monkeypatch.setattr(mkv, "to_alpha2", lambda lang: lang)

    asked = {}

    def worker(debug, input_file, dirpath, missing_subs_langs, internal_threads, logger):
        asked["langs"] = list(missing_subs_langs)
        return [], [], [], []

    monkeypatch.setattr(mkv, "fetch_missing_subtitles_process_worker", worker)
    mkv.fetch_missing_subtitles_process(None, False, [item], str(tmp_path))

    assert asked["langs"] == ["jpn"]


@pytest.mark.parametrize("lang", ["none", "und", "UND", ""])
def test_placeholder_languages_are_never_requested(monkeypatch, tmp_path, ocr_threads, lang):
    item = MediaFile(name="movie.mkv", missing_subs_langs=[lang, "jpn"])

    monkeypatch.setattr(mkv, "to_alpha2", lambda value: value)

    asked = {}

    def worker(debug, input_file, dirpath, missing_subs_langs, internal_threads, logger):
        asked["langs"] = list(missing_subs_langs)
        return [], [], [], []

    monkeypatch.setattr(mkv, "fetch_missing_subtitles_process_worker", worker)
    mkv.fetch_missing_subtitles_process(None, False, [item], str(tmp_path))

    assert asked["langs"] == ["jpn"]


# --- resync_sub_process -------------------------------------------------------

def test_resync_pairs_each_file_with_its_own_tracks(monkeypatch, ocr_threads):
    monkeypatch.setattr(mkv, "check_config", lambda *a, **k: True)

    media = records("e01.mkv", "e02.mkv", "e03.mkv")
    jobs = [(item, [track(f"{item.name}.srt")]) for item in media]

    seen = []
    lock = threading.Lock()

    def worker(debug, input_file, dirpath, subtitle_files_to_process, internal_threads):
        time.sleep(random.uniform(0, 0.02))
        with lock:
            seen.append((input_file, subtitle_files_to_process[0].path))

    monkeypatch.setattr(mkv, "resync_subs_process_worker", worker)
    mkv.resync_sub_process(None, False, "/tmp", jobs)

    assert sorted(seen) == [
        ("e01.mkv", "e01.mkv.srt"),
        ("e02.mkv", "e02.mkv.srt"),
        ("e03.mkv", "e03.mkv.srt"),
    ]
