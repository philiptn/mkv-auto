"""Tests for the strict episode ordering of post-encode destination moves.

Encodes finish in runtime order, not episode order - a short episode submitted
second routinely overtakes a long one submitted first. Two pieces guarantee the
destination still sees ep1, ep2, ep3 ... in order:

  * OrderedRelease buffers an early finisher until every lower index is out,
  * a single-worker ThreadPoolExecutor makes submission order the execution order.

Both are exercised here with fakes only - no media, no ffmpeg, no disk.
"""

import concurrent.futures
import random
import threading
import time

import pytest

from modules.media_encoder import OrderedRelease
from modules.misc import natural_sort_key
from modules.mkv import print_arr_summary


# --- OrderedRelease -----------------------------------------------------------

def test_in_order_arrivals_release_immediately():
    release = OrderedRelease()
    for index in range(4):
        release.add(index, f"ep{index}")
        assert list(release.ready()) == [f"ep{index}"]
    assert release.outstanding() == []


def test_an_early_finisher_is_held_back():
    """ep3 finishing before ep2 must not reach the destination first."""
    release = OrderedRelease()
    release.add(0, "ep1")
    assert list(release.ready()) == ["ep1"]

    release.add(2, "ep3")
    assert list(release.ready()) == []
    assert release.outstanding() == [2]

    release.add(1, "ep2")
    assert list(release.ready()) == ["ep2", "ep3"]
    assert release.outstanding() == []


def test_nothing_is_released_while_the_head_is_missing():
    release = OrderedRelease()
    for index in (1, 2, 3, 4):
        release.add(index, f"ep{index + 1}")
        assert list(release.ready()) == []
    assert release.outstanding() == [1, 2, 3, 4]


def test_a_fully_reversed_batch_still_comes_out_in_order():
    release = OrderedRelease()
    payloads = [f"ep{i}" for i in range(6)]
    released = []
    for index in reversed(range(6)):
        release.add(index, payloads[index])
        # Everything stays buffered until index 0, the last to arrive, unblocks
        # the whole run at once.
        assert released == []
        released.extend(release.ready())
    assert released == payloads


def test_ready_is_idempotent_and_never_repeats_an_index():
    release = OrderedRelease()
    release.add(0, "ep1")
    assert list(release.ready()) == ["ep1"]
    assert list(release.ready()) == []
    assert list(release.ready()) == []


@pytest.mark.parametrize("seed", range(20))
def test_any_arrival_order_yields_index_order(seed):
    """The ordering property holds for every permutation, not just the ones above."""
    rng = random.Random(seed)
    order = list(range(8))
    rng.shuffle(order)

    release = OrderedRelease()
    released = []
    for index in order:
        release.add(index, index)
        released.extend(release.ready())

    assert released == list(range(8))


# --- OrderedRelease paired with the single-worker mover -----------------------

def _drive(completion_order, total=None):
    """Replay a completion order through the release buffer + one mover thread.

    Mirrors the pairing in encode_media_files(): completions feed the buffer,
    whatever the buffer unblocks is submitted to a single-worker pool, and the
    pool records the order in which the moves actually ran.
    """
    total = total if total is not None else len(completion_order)
    release = OrderedRelease()
    moved = []
    lock = threading.Lock()

    def move(index):
        # Uneven work, so a pool with more than one worker would visibly
        # reorder these and the test would catch it.
        time.sleep(0.002 * ((index * 7) % 5))
        with lock:
            moved.append(index)

    futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as mover:
        for index in completion_order:
            release.add(index, index)
            for ready_index in release.ready():
                futures.append(mover.submit(move, ready_index))

    for future in futures:
        future.result()
    return moved


def test_moves_run_in_index_order_when_encodes_finish_in_order():
    assert _drive([0, 1, 2, 3, 4]) == [0, 1, 2, 3, 4]


def test_moves_run_in_index_order_when_encodes_finish_scrambled():
    assert _drive([3, 1, 0, 4, 2]) == [0, 1, 2, 3, 4]


def test_moves_run_in_index_order_when_encodes_finish_reversed():
    assert _drive([4, 3, 2, 1, 0]) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("seed", range(10))
def test_delivery_order_is_index_order_for_any_completion_order(seed):
    rng = random.Random(seed)
    completion_order = list(range(6))
    rng.shuffle(completion_order)
    assert _drive(completion_order) == list(range(6))


def test_a_stalled_head_delivers_nothing_early():
    """While ep1 is still encoding, no later episode may reach the destination."""
    assert _drive([1, 2, 3, 4]) == []


# --- natural_sort_key ---------------------------------------------------------

def test_zero_padded_episodes_sort_by_episode_number():
    names = ["Show.S01E10.mkv", "Show.S01E02.mkv", "Show.S01E01.mkv"]
    assert sorted(names, key=natural_sort_key) == [
        "Show.S01E01.mkv", "Show.S01E02.mkv", "Show.S01E10.mkv"]


def test_unpadded_episodes_sort_by_value_not_by_character():
    """Plain lexicographic sorting puts 'Episode 10' before 'Episode 2'."""
    names = ["Episode 10.mkv", "Episode 2.mkv", "Episode 1.mkv"]
    assert sorted(names, key=natural_sort_key) == [
        "Episode 1.mkv", "Episode 2.mkv", "Episode 10.mkv"]


def test_seasons_outrank_episodes():
    names = ["Show.S02E01.mkv", "Show.S01E09.mkv", "Show.S10E01.mkv"]
    assert sorted(names, key=natural_sort_key) == [
        "Show.S01E09.mkv", "Show.S02E01.mkv", "Show.S10E01.mkv"]


def test_case_does_not_split_otherwise_equal_names():
    names = ["show.s01e02.mkv", "Show.S01E01.mkv"]
    assert sorted(names, key=natural_sort_key) == [
        "Show.S01E01.mkv", "show.s01e02.mkv"]


def test_arr_summary_counts_only_real_paths(capsys):
    """Shared by the batch move stage and the encoder's mover, so the counting
    has to survive the empty strings a worker returns when no API key is set,
    and the None a slot carries when its move never produced a result."""
    print_arr_summary(_FakeLogger(), [
        ("", "/tv/Show/S01E01.mkv"),
        ("", "/tv/Show/S01E02.mkv"),
        ("", ""),
        (None, None),
        ("/movies/Film (2019)/Film.mkv", ""),
    ])
    out = capsys.readouterr().out
    assert "Updated 1 movie folder in Radarr." in out
    assert "Updated 2 TV folders in Sonarr." in out


def test_arr_summary_prints_zero_counts_for_no_updates(capsys):
    print_arr_summary(_FakeLogger(), [("", ""), ("", "")])
    # Nothing was updated, so neither integration gets a line at all.
    assert capsys.readouterr().out.strip() == ""


class _FakeLogger:
    """Just the three sinks custom_print()/custom_print_no_newline() write to."""

    def info(self, message):
        pass

    def debug(self, message):
        pass

    def color(self, message):
        pass


def test_names_without_digits_sort_alphabetically():
    names = ["cover.jpg", "Backdrop.png", "poster.jpg"]
    assert sorted(names, key=natural_sort_key) == [
        "Backdrop.png", "cover.jpg", "poster.jpg"]


def test_multi_episode_files_group_with_their_first_episode():
    """A pack like S01E01-E02 sorts against E01, i.e. ahead of E03.

    Whether it lands just before or just after a bare E01 is not something the
    pipeline depends on - what matters is that it does not drift past a later
    episode.
    """
    names = ["Show.S01E03.mkv", "Show.S01E01-E02.mkv", "Show.S01E01.mkv"]
    ordered = sorted(names, key=natural_sort_key)
    assert ordered[-1] == "Show.S01E03.mkv"
    assert set(ordered[:2]) == {"Show.S01E01.mkv", "Show.S01E01-E02.mkv"}
