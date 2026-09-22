"""Regression tests for file_operations.resolve_output_target movie naming.

Guards the bug where a movie whose filename carries a dynamic-range / quality
suffix (e.g. "Movie name (2020) - DV HDR.mkv") was mis-detected as a movie
*extra*, so the title was stripped and the file became just "DV HDR.mkv".

Movie extras are strictly defined by extras_definitions; only text containing one
of those keywords (behindthescenes/featurette/trailer/...) is an extra. These
tests drive the pure naming logic - no ffmpeg, no network (movies in 'simple'
mode never do a metadata lookup).
"""

import os

import pytest

import modules.file_operations as fo
from modules.file_operations import resolve_output_target


@pytest.fixture(autouse=True)
def naming_config():
    """Force deterministic, network-free naming and restore afterwards."""
    general = fo.config["general"]
    saved = {k: general[k] for k in ("normalize_filenames", "keep_original_file_structure")}
    general["normalize_filenames"] = "simple"          # sep is ' - ', no TVMaze lookup
    general["keep_original_file_structure"] = "fallback"
    try:
        yield
    finally:
        general.update(saved)


def _resolve(original_name):
    return resolve_output_target(
        None, False, f"/in/{original_name}", "/out", "", original_name,
    )


# --- regression: dynamic-range / quality suffix must NOT be treated as an extra

@pytest.mark.parametrize("original_name,expected", [
    ("Movie name (2020) - DV HDR.mkv", "Movie name (2020) - DV HDR.mkv"),
    ("Movie name (2020) - DV.mkv", "Movie name (2020) - DV.mkv"),
    ("Movie name (2020) - HDR.mkv", "Movie name (2020) - HDR.mkv"),
    ("Movie name (2020) - 4K.mkv", "Movie name (2020) - 4K.mkv"),
])
def test_dynamic_range_suffix_keeps_title(original_name, expected):
    result = _resolve(original_name)
    assert result["restored_filename"] == expected
    # The title-bearing folder was always correct; assert it stays so.
    assert result["output_folder"].endswith("Movie name (2020)")
    # The specific bug symptom must not reappear.
    assert result["restored_filename"] != "DV HDR.mkv"


def test_dotted_scene_name_gets_dr_suffix():
    # Already worked (dotted base never matched the extra pattern) - guard it.
    result = _resolve("Movie.Name.2020.2160p.DV.HDR.mkv")
    assert result["restored_filename"] == "Movie Name (2020) - DV HDR.mkv"
    assert result["output_folder"].endswith("Movie Name (2020)")


# --- genuine extras (defined keywords) are still handled as extras -------------

@pytest.mark.parametrize("original_name,expected", [
    ("Movie name (2020) - Featurette.mkv", "Featurette.mkv"),
    ("Movie name (2020)-behindthescenes.mkv", "behindthescenes.mkv"),
    ("Movie name (2020) - Deleted Scene.mkv", "Deleted Scene.mkv"),
])
def test_real_extras_still_stripped_to_extra_name(original_name, expected):
    result = _resolve(original_name)
    assert result["restored_filename"] == expected


# --- variant_paths: the other names this same file could have resolved to ------
#
# The dynamic range is read off the filename, which only stands in for a stream
# probe. A pre-copy (the encoder placeholder, or the qBittorrent live copy) writes
# its guess to the destination before anything has looked at the file, so the
# finished output has to know which other names to clean up.

def _variant_names(result):
    return sorted(os.path.basename(p) for p in result["variant_paths"])


def test_movie_variants_cover_the_other_suffixes():
    result = _resolve("Movie name (2020) - DV.mkv")

    assert _variant_names(result) == [
        "Movie name (2020) - 4K.mkv",
        "Movie name (2020) - DV HDR.mkv",
        "Movie name (2020) - HDR.mkv",
        "Movie name (2020).mkv",
    ]
    # Never its own destination.
    assert result["output_path"] not in result["variant_paths"]


def test_dv_hdr_variants_include_the_plain_dv_name():
    """The reported duplicate: pre-copy wrote '- DV HDR', output is '- DV'."""
    result = _resolve("Movie name (2020) - DV HDR.mkv")

    assert "Movie name (2020) - DV.mkv" in _variant_names(result)
    assert "Movie name (2020) - DV HDR.mkv" not in _variant_names(result)


def test_variants_share_the_destination_folder():
    result = _resolve("Movie name (2020) - DV.mkv")

    folders = {os.path.dirname(p) for p in result["variant_paths"]}
    assert folders == {os.path.dirname(result["output_path"])}


def test_tv_episode_variants_keep_the_episode():
    result = _resolve("Show.Name.S01E02.2160p.DV.mkv")

    assert result["restored_filename"] == "Show Name - S01E02 - DV.mkv"
    assert _variant_names(result) == [
        "Show Name - S01E02 - 4K.mkv",
        "Show Name - S01E02 - DV HDR.mkv",
        "Show Name - S01E02 - HDR.mkv",
        "Show Name - S01E02.mkv",
    ]


def test_original_structure_variants_use_the_release_name(monkeypatch):
    """With the original structure kept, the suffix never appears - the DV token
    sits inside the release name instead, so the rewrites are what must match."""
    monkeypatch.setitem(fo.config["general"], "keep_original_file_structure", "true")

    result = _resolve("Movie.Name.2020.2160p.DV.x265-GRP.mkv")

    assert result["restored_filename"] == "Movie.Name.2020.2160p.DV.x265-GRP.mkv"
    # Exactly the rewrite, and nothing else: the suffix form is unreachable here,
    # so those names must not widen what the move is willing to delete.
    assert _variant_names(result) == ["Movie.Name.2020.2160p.DV.HDR.x265-GRP.mkv"]


# --- move_resolved_to_output removes a mispredicted pre-copy -------------------

class StubLogger:
    """move_resolved_to_output() logs unconditionally."""

    def debug(self, message):
        pass


def _target(tmp_path, restored_filename, variants):
    return {
        "output_folder": "Movies",
        "restored_filename": restored_filename,
        "output_path": str(tmp_path / restored_filename),
        "media_name": "Movie name (2020)",
        "variant_paths": [str(tmp_path / name) for name in variants],
    }


def test_move_removes_the_stale_placeholder(tmp_path):
    placeholder = tmp_path / "Movie name (2020) - DV HDR.mkv"
    placeholder.write_bytes(b"placeholder")
    source = tmp_path / "source.mkv"
    source.write_bytes(b"real output")

    target = _target(tmp_path, "Movie name (2020) - DV.mkv",
                     ["Movie name (2020) - DV HDR.mkv"])
    fo.move_resolved_to_output(StubLogger(), str(source), target)

    assert not placeholder.exists()
    assert (tmp_path / "Movie name (2020) - DV.mkv").read_bytes() == b"real output"


def test_copy_never_removes_anything(tmp_path):
    """A pre-copy is itself a placeholder - it must not delete the real output."""
    existing = tmp_path / "Movie name (2020) - DV HDR.mkv"
    existing.write_bytes(b"real output")
    source = tmp_path / "source.mkv"
    source.write_bytes(b"placeholder")

    target = _target(tmp_path, "Movie name (2020) - DV.mkv",
                     ["Movie name (2020) - DV HDR.mkv"])
    fo.move_resolved_to_output(StubLogger(), str(source), target, copy=True)

    assert existing.read_bytes() == b"real output"


def test_move_leaves_unrelated_files_alone(tmp_path):
    unrelated = tmp_path / "Other movie (2021) - DV HDR.mkv"
    unrelated.write_bytes(b"other")
    source = tmp_path / "source.mkv"
    source.write_bytes(b"real output")

    target = _target(tmp_path, "Movie name (2020) - DV.mkv",
                     ["Movie name (2020) - DV HDR.mkv"])
    fo.move_resolved_to_output(StubLogger(), str(source), target)

    assert unrelated.exists()
