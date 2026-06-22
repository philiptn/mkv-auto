#!/usr/bin/env bash
# Run the mkv-auto regression test suite.
#
# Diffs get_wanted_audio_tracks (and future refactored functions) against the
# `main` branch as ground truth. Pass-through args go to pytest, e.g.:
#   ./run-tests.sh -k commentary
set -euo pipefail

cd "$(dirname "$0")"

# Prefer the project virtualenv if present, otherwise fall back to `python`.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python"
fi

if ! "$PY" -m pytest --version >/dev/null 2>&1; then
    echo "pytest is not installed in '$PY'."
    echo "Create a venv and install the test dependencies, e.g.:"
    echo "  python -m venv .venv"
    echo "  .venv/bin/python -m pip install pytest pycountry psutil requests tqdm configparser"
    exit 1
fi

exec "$PY" -m pytest tests/ -v "$@"
