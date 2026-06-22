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
