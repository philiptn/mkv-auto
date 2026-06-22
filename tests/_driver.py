"""Generic cross-branch driver for the regression test suite.

Reads a JSON payload from stdin:

    {"target": "<name>", "cases": [ {"name": ...}, ... ]}

looks the target up in the tests.targets registry, runs every case through it,
and writes {case_name: canonical_dict} as JSON to stdout.

The same file is executed both in the working tree and inside a temporary git
worktree of `main`, so the per-target `run_case` functions (in tests/targets/)
must understand both the old and new return shapes and normalize them to the
same comparable dict. The driver itself knows nothing function-specific - add a
new module under tests/targets/ to cover a new function.

Never touches real media: every input is an in-memory dict supplied via stdin.
"""

import sys
import os
import json

# Run with the tree root as cwd so `import modules.*` / `import tests.*` resolve
# to whichever branch's checkout we were pointed at.
sys.path.insert(0, os.getcwd())

from tests.targets import REGISTRY  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    target = payload["target"]
    cases = payload["cases"]
    if target not in REGISTRY:
        raise SystemExit(f"unknown target {target!r}; known: {sorted(REGISTRY)}")
    run_case = REGISTRY[target]
    out = {case["name"]: run_case(case) for case in cases}
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
