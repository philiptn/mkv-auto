"""Reusable machinery for diffing a function against the `main` branch.

`main` is treated as ground truth: the same generic driver (tests/_driver.py)
is run both in the working tree and in a throwaway git worktree of `main` over a
shared set of synthetic cases, and the canonical results are compared.

This module is import-safe without pytest so it can be reused by ad-hoc scripts;
the pytest fixtures that wrap it live in tests/conftest.py.
"""

import os
import sys
import json
import shutil
import subprocess

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
DRIVER_REL = os.path.join("tests", "_driver.py")


def run_driver(cwd, target, cases):
    """Run `target` over `cases` via the driver in `cwd`; return {name: result}."""
    payload = {"target": target, "cases": list(cases)}
    proc = subprocess.run(
        [sys.executable, DRIVER_REL],
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"driver failed in {cwd} for target {target!r}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def create_main_worktree(path):
    """Create a detached worktree of `main` at `path` with tests/ copied in.

    The whole tests/ tree (driver + targets registry) is copied so the SAME
    normalization code runs against main's modules. Returns `path`.
    """
    subprocess.run(
        ["git", "worktree", "add", "--force", "--detach", path, "main"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    shutil.copytree(
        TESTS_DIR, os.path.join(path, "tests"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    return path


def remove_main_worktree(path):
    subprocess.run(
        ["git", "worktree", "remove", "--force", path],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
