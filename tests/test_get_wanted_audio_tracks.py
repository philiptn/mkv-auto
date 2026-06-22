"""Regression test for audio.get_wanted_audio_tracks.

Diffs the working tree against the `main` branch (ground truth) over the shared
fixtures; the machinery lives in tests/conftest.py + tests/diff_harness.py and
the normalization in tests/targets/get_wanted_audio_tracks.py. The only
deliberate divergences (the commentary/codec-removal fix) live in FIX_CASES and
are asserted against explicit expected values instead.
"""

import pytest

from tests.fixtures import DIFF_CASES, FIX_CASES

TARGET = "get_wanted_audio_tracks"


@pytest.fixture(scope="session")
def results(collect_results):
    return collect_results(TARGET, DIFF_CASES, FIX_CASES)


@pytest.mark.parametrize("name", [c["name"] for c in DIFF_CASES])
def test_matches_main(results, name):
    ground_truth, actual = results
    assert actual[name] == ground_truth[name], (
        f"{name}: dev diverged from main\n"
        f"main: {ground_truth[name]}\ndev:  {actual[name]}"
    )


@pytest.mark.parametrize("case,expected", FIX_CASES, ids=[c["name"] for c, _ in FIX_CASES])
def test_intended_fixes(results, case, expected):
    _, actual = results
    assert actual[case["name"]] == expected
