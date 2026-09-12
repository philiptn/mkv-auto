"""Regression tests for episode numbers of three or more digits.

Guards the bug where every parser used a fixed two-digit episode capture, so
"Show - S01E100" silently parsed as episode 10: the truncated number was rebuilt
into the lookup string, TVMaze returned the metadata for the *real* episode 10,
and a whole season's worth of episodes above 99 collapsed onto E10-E12 and
overwrote each other. Seasons with 100+ episodes are routine (soaps, long-running
kids' shows, anime with absolute numbering).

The fix removed the ceiling rather than raising it - the captures are open-ended
and the padding widens only as far as a number needs - so four-digit absolute
numbering is pinned here alongside three, to keep a later edit from
reintroducing a fixed width one digit further out.

Network-free: every TVMaze entry point is stubbed.
"""

import os

import pytest

import modules.file_operations as fo
import modules.misc as misc
from modules.file_operations import resolve_output_target
from modules.logger import setup_quiet_logger
from modules.misc import (
    compact_episode_list,
    extract_season_episode,
    get_tv_episode_metadata,
)

LOGGER = setup_quiet_logger("mkv-auto.tests")


# --- the parser itself --------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    # The reported bug: the third digit was dropped, not rejected.
    ("Animaniacs (1993) - S01E100 - White Gloves.mkv", (1, [100])),
    ("Animaniacs (1993) - S01E122 - When Mice Ruled the Earth.mkv", (1, [122])),
    # Control: two-digit episodes were always fine and must stay fine.
    ("Animaniacs (1993) - S01E99 - Cookies for Einstein.mkv", (1, [99])),
    ("Some.Show.S01E02.1080p.mkv", (1, [2])),
    # Absolute numbering runs past three digits, and past four.
    ("Show - S01E1088 - Title.mkv", (1, [1088])),
    ("Show - S01E1000-E1002 - Multi.mkv", (1, [1000, 1001, 1002])),
    # Four digits do not turn a resolution suffix into a range either.
    ("Show.S01E1000-1080p.WEB.mkv", (1, [1000])),
    # Multi-episode forms, with and without the repeated E.
    ("Show - S01E01-E02 - Two.mkv", (1, [1, 2])),
    ("Show - S01E01-02 - Two.mkv", (1, [1, 2])),
    ("Show - S01E100-E102 - Multi.mkv", (1, [100, 101, 102])),
    # A resolution suffix is not the end of an episode range. Widening the
    # capture would otherwise have read this as episodes 1 through 1080.
    ("Show.S01E01-1080p.WEB.mkv", (1, [1])),
])
def test_extract_season_episode(filename, expected):
    season, episodes = extract_season_episode(filename)
    assert (season, list(episodes)) == expected


def test_extras_sentinel_is_not_an_episode():
    """S000E### is the internal marker for extras, not a season 0 episode.

    The two-digit season group is what keeps it out; assert that directly so a
    later widening of the season cannot silently reclassify every extra.
    """
    assert extract_season_episode("Show - S000E001 - Featurette.mkv") == (None, None)


def test_unnumbered_file_is_still_unmatched():
    assert extract_season_episode("Some Movie (2020).mkv") == (None, None)


# --- the formatting half (already correct; pinned so the fix stays whole) ------

@pytest.mark.parametrize("episodes,expected", [
    ([100], "E100"),
    ([99], "E99"),
    ([100, 101, 102], "E100-E102"),
    # Mixed width across a range is what the current code produces. Documented,
    # not changed - the padding rule is "3 digits only when it needs 3".
    ([99, 100], "E99-E100"),
    # The rule widens only as far as the number needs: no fixed ceiling above.
    ([1000], "E1000"),
    ([999, 1000], "E999-E1000"),
])
def test_compact_episode_list_pads_past_99(episodes, expected):
    assert compact_episode_list(episodes, True, True) == expected


# --- the lookup string parser -------------------------------------------------

def test_metadata_lookup_accepts_a_three_digit_episode(monkeypatch):
    """The lookup parser is `$`-anchored, so a two-digit capture did not merely
    truncate here - it failed to match at all and returned {}.

    Stub the network so the function returns as soon as the parse succeeds: a
    parse failure returns {}, a stubbed request failure returns None. The
    difference is what distinguishes "could not read S01E100" from "read it".
    """
    def no_network(*args, **kwargs):
        raise misc.requests.RequestException("stubbed")

    monkeypatch.setattr(misc.requests, "get", no_network)

    assert get_tv_episode_metadata(LOGGER, False, "Animaniacs (1993) - S01E100") is None
    assert get_tv_episode_metadata(LOGGER, False, "Animaniacs (1993) - S01E100-E102") is None
    assert get_tv_episode_metadata(LOGGER, False, "Some Show - S01E1088") is None
    # Control: something with no S..E.. at all still fails the parse.
    assert get_tv_episode_metadata(LOGGER, False, "Animaniacs (1993)") == {}


# --- end to end through the namer --------------------------------------------

@pytest.fixture
def naming_config():
    """Deterministic full-normalization naming, with the show cache cleared."""
    saved = {k: fo.config["general"][k] for k in (
        "normalize_filenames", "keep_original_file_structure", "make_season_folders",
        "tv_shows_folder",
    )}
    general = fo.config["general"]
    general["normalize_filenames"] = "full"
    general["keep_original_file_structure"] = "fallback"
    general["make_season_folders"] = True
    general["tv_shows_folder"] = "TV Shows"
    misc.tv_metadata_cache.clear()
    try:
        yield
    finally:
        fo.config["general"].update(saved)
        misc.tv_metadata_cache.clear()


@pytest.fixture
def recorded_lookups(monkeypatch):
    """Stub every TVMaze entry point, recording the strings it was asked about.

    resolve_output_target looks the episode up through file_operations, while
    reformat_filename does its own show lookup through misc; both have to be
    stubbed or the test hits the network.
    """
    calls = []

    def stub(logger, debug, input_str):
        calls.append(input_str)
        return {
            "show_name": "Animaniacs",
            "show_year": "1993",
            "episode_title": "White Gloves",
        }

    monkeypatch.setattr(fo, "get_tv_episode_metadata", stub)
    monkeypatch.setattr(misc, "get_tv_episode_metadata", stub)
    return calls


def _resolve(original_name):
    return resolve_output_target(
        LOGGER, False, f"/in/{original_name}", "/out", "", original_name,
    )


def test_episode_100_is_looked_up_as_100(naming_config, recorded_lookups):
    """The exact reported regression: S01E100 was looked up - and named - as E10."""
    result = _resolve("Animaniacs (1993) - S01E100 - White Gloves.mkv")

    assert "Animaniacs (1993) - S01E100" in recorded_lookups
    assert "Animaniacs (1993) - S01E10" not in recorded_lookups
    assert result["restored_filename"] == "Animaniacs (1993) - S01E100 - White Gloves.mkv"
    assert result["output_folder"] == os.path.join(
        "TV Shows", "Animaniacs (1993)", "Season 1")


def test_episode_99_still_resolves(naming_config, recorded_lookups):
    """Control: the digit count below 100 was never the problem."""
    result = _resolve("Animaniacs (1993) - S01E99 - Cookies for Einstein.mkv")

    assert "Animaniacs (1993) - S01E99" in recorded_lookups
    assert result["restored_filename"] == "Animaniacs (1993) - S01E99 - White Gloves.mkv"


def test_episodes_past_99_do_not_collide(naming_config, recorded_lookups):
    """E100-E122 all landed on E10/E11/E12 and overwrote one another."""
    paths = {
        _resolve(f"Animaniacs (1993) - S01E{n} - Title.mkv")["output_path"]
        for n in range(100, 123)
    }
    assert len(paths) == 23


def test_episodes_across_the_1000_boundary_do_not_collide(naming_config, recorded_lookups):
    """The same guarantee one digit further out.

    A run spanning 999 -> 1000 is where a reintroduced fixed width would show
    up first: three-digit padding would still render 999 correctly while
    truncating its successors onto paths that already exist.
    """
    paths = {
        _resolve(f"Some Show (2010) - S01E{n} - Title.mkv")["output_path"]
        for n in range(995, 1006)
    }
    assert len(paths) == 11

    result = _resolve("Some Show (2010) - S01E1000 - Title.mkv")
    assert "Some Show (2010) - S01E1000" in recorded_lookups
    assert "Some Show (2010) - S01E100" not in recorded_lookups
    assert "S01E1000" in result["restored_filename"]
