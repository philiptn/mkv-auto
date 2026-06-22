"""Shared pytest fixtures for the differential regression suite.

Test modules typically only need `collect_results`:

    from tests.fixtures import DIFF_CASES, FIX_CASES

    @pytest.fixture(scope="session")
    def results(collect_results):
        return collect_results("get_wanted_audio_tracks", DIFF_CASES, FIX_CASES)
"""

import pytest

from tests.diff_harness import (
    REPO_ROOT,
    run_driver,
    create_main_worktree,
    remove_main_worktree,
)


@pytest.fixture(scope="session")
def main_worktree(tmp_path_factory):
    """A detached git worktree of `main`, with the current tests/ copied in."""
    path = str(tmp_path_factory.mktemp("main-worktree"))
    create_main_worktree(path)
    try:
        yield path
    finally:
        remove_main_worktree(path)


@pytest.fixture(scope="session")
def collect_results(main_worktree):
    """Return a helper that diffs a target against main.

    `helper(target, diff_cases, fix_cases=())` returns `(ground_truth, actual)`:
    ground_truth from main over diff_cases, actual from the working tree over
    diff_cases + fix_cases (fix cases are dev-only intentional divergences).
    """
    def helper(target, diff_cases, fix_cases=()):
        ground_truth = run_driver(main_worktree, target, diff_cases)
        actual = run_driver(
            REPO_ROOT, target, list(diff_cases) + [c for c, _ in fix_cases]
        )
        return ground_truth, actual

    return helper
