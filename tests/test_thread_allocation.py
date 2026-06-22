"""Unit tests for misc.compute_thread_allocation.

Guards the fix for the dead `internal_threads = max_worker_threads // num_workers`
formula that always evaluated to 1 (because num_workers was set to
max_worker_threads). With fewer files than available threads each file must now
receive internal threads so otherwise-idle cores are used for per-track work.
"""

from modules.misc import compute_thread_allocation


def test_large_batch_one_thread_per_file():
    # files >= threads: one worker per thread, no internal parallelism (as before)
    assert compute_thread_allocation(32, 16) == (16, 1)


def test_exactly_saturated():
    assert compute_thread_allocation(16, 16) == (16, 1)


def test_single_file_uses_all_threads_internally():
    # the bug fix: one file, many threads -> internal threads > 1 (was always 1)
    assert compute_thread_allocation(1, 16) == (1, 16)


def test_few_files_split_threads():
    assert compute_thread_allocation(4, 16) == (4, 4)


def test_total_product_never_exceeds_budget():
    for files in range(0, 40):
        for threads in (1, 2, 8, 16, 31):
            num_workers, internal_threads = compute_thread_allocation(files, threads)
            assert num_workers >= 1 and internal_threads >= 1
            assert num_workers * internal_threads <= max(1, threads)


def test_zero_files_clamps_to_one_worker():
    assert compute_thread_allocation(0, 16) == (1, 16)


def test_single_thread_budget():
    assert compute_thread_allocation(8, 1) == (1, 1)
