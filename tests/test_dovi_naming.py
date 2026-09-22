"""Tests for the DV -> DV HDR rename in mkv.convert_dovi_files.

Guards the bug where the rename was a side effect of a *completed conversion*:
files already at Profile 8.1 were excluded from the job list entirely, so they
kept their plain "DV" name while the pre-copy - which predicts the name from the
filename alone, and cannot know the profile - had already written "DV HDR" to the
destination. The two names are two paths, so the placeholder was never
overwritten and every non-Profile-7 DV release landed in the library twice.

The rename now follows what the file *is*: anything that ends up at Profile 8.1
gets "DV HDR", whether this run converted it or found it that way. Profile 5 has
no HDR10 base layer and a failed conversion is still Profile 7, so neither is
renamed.

dovi_convert is never invoked - both the classifier and the per-file worker are
stubbed, so these run against plain files on disk.
"""

import os

import pytest

import modules.mkv as mkv
from modules.mkv import convert_dovi_files


class StubLogger:
    """Swallows the pipeline's logging calls - the module logs unconditionally."""

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def color(self, message):
        pass


@pytest.fixture
def dovi(tmp_path, monkeypatch):
    """Drive convert_dovi_files with a stubbed classifier and worker.

    Returns a helper taking {filename: (classification, outcome)} and giving back
    the returned name list plus what actually exists on disk afterwards.
    """
    def run(spec):
        names = list(spec)
        for name in names:
            (tmp_path / name).write_bytes(b"stub")

        by_name = {name: spec[name] for name in names}

        def fake_classify(full_path):
            return by_name[os.path.basename(full_path)][0]

        def fake_worker(logger, debug, input_file, dirpath, progress, worker_id):
            progress.start_worker(worker_id)
            progress.finish_worker(worker_id)
            outcome = by_name[input_file][1]
            size = os.path.getsize(os.path.join(dirpath, input_file))
            return input_file, {"initial_file_size": size, "resulting_file_size": size}, outcome

        monkeypatch.setattr(mkv, "classify_dolby_vision", fake_classify)
        monkeypatch.setattr(mkv, "dovi_scan_and_convert_single", fake_worker)

        returned = convert_dovi_files(StubLogger(), False, names, str(tmp_path))
        on_disk = sorted(p.name for p in tmp_path.iterdir())
        return returned, on_disk

    return run


# --- regression: already Profile 8.1 must still get the DV HDR name

def test_already_profile_8_is_renamed(dovi):
    returned, on_disk = dovi({"Movie.2020.2160p.DV.x265.mkv": ("skip", None)})

    assert returned == ["Movie.2020.2160p.DV.HDR.x265.mkv"]
    assert on_disk == ["Movie.2020.2160p.DV.HDR.x265.mkv"]


def test_batch_of_only_already_profile_8_still_renames(dovi):
    """The empty-job-list early return used to skip the renames entirely."""
    returned, on_disk = dovi({
        "Show.S01E01.DV.x265.mkv": ("skip", None),
        "Show.S01E02.DV.x265.mkv": ("skip", None),
    })

    assert returned == ["Show.S01E01.DV.HDR.x265.mkv", "Show.S01E02.DV.HDR.x265.mkv"]
    assert on_disk == ["Show.S01E01.DV.HDR.x265.mkv", "Show.S01E02.DV.HDR.x265.mkv"]


# --- the outcomes that must not be renamed

def test_profile_7_conversion_is_renamed(dovi):
    returned, on_disk = dovi({"Movie.2020.DV.x265.mkv": ("convert", "true")})

    assert returned == ["Movie.2020.DV.HDR.x265.mkv"]
    assert on_disk == ["Movie.2020.DV.HDR.x265.mkv"]


def test_failed_conversion_keeps_its_name(dovi):
    """A failed conversion is still Profile 7 - it has no HDR10 base layer."""
    returned, on_disk = dovi({"Movie.2020.DV.x265.mkv": ("convert", "fail")})

    assert returned == ["Movie.2020.DV.x265.mkv"]
    assert on_disk == ["Movie.2020.DV.x265.mkv"]


def test_profile_5_keeps_its_name(dovi):
    returned, on_disk = dovi({"Movie.2020.DV.x265.mkv": ("p5", "p5")})

    assert returned == ["Movie.2020.DV.x265.mkv"]
    assert on_disk == ["Movie.2020.DV.x265.mkv"]


def test_no_dolby_vision_keeps_its_name(dovi):
    returned, on_disk = dovi({"Movie.2020.HDR.x265.mkv": ("none", None)})

    assert returned == ["Movie.2020.HDR.x265.mkv"]
    assert on_disk == ["Movie.2020.HDR.x265.mkv"]


# --- rename guards

def test_existing_hdr_token_is_not_upgraded_twice(dovi):
    returned, on_disk = dovi({"Movie.2020.DV.HDR.x265.mkv": ("skip", None)})

    assert returned == ["Movie.2020.DV.HDR.x265.mkv"]
    assert on_disk == ["Movie.2020.DV.HDR.x265.mkv"]


def test_rename_is_skipped_when_target_exists(tmp_path, dovi):
    (tmp_path / "Movie.2020.DV.HDR.x265.mkv").write_bytes(b"occupied")

    returned, on_disk = dovi({"Movie.2020.DV.x265.mkv": ("skip", None)})

    assert returned == ["Movie.2020.DV.x265.mkv"]
    assert on_disk == ["Movie.2020.DV.HDR.x265.mkv", "Movie.2020.DV.x265.mkv"]
    assert (tmp_path / "Movie.2020.DV.HDR.x265.mkv").read_bytes() == b"occupied"


# --- mixed batch: the returned list must stay index-aligned for remap_origins()

def test_mixed_batch_keeps_positions(dovi):
    returned, _ = dovi({
        "A.DV.x265.mkv": ("skip", None),
        "B.DV.x265.mkv": ("p5", "p5"),
        "C.DV.x265.mkv": ("convert", "true"),
        "D.x265.mkv": ("none", None),
    })

    assert returned == [
        "A.DV.HDR.x265.mkv",
        "B.DV.x265.mkv",
        "C.DV.HDR.x265.mkv",
        "D.x265.mkv",
    ]
