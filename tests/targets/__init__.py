"""Registry of differential-test targets.

Each target is a callable ``run_case(case) -> dict`` that runs one function over
a single case dict and returns a JSON-serializable canonical result. Targets are
version-agnostic: the same code runs against the working tree and a `main`
worktree, so each must normalize whatever shape its branch returns.

To cover a new function, add a module here that calls ``@register("<name>")`` on
its run_case, then import it below so it self-registers.
"""

REGISTRY = {}


def register(name):
    """Decorator: register a run_case callable under ``name``."""
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


# Import target modules so they populate REGISTRY on package import.
from . import get_wanted_audio_tracks  # noqa: E402,F401
