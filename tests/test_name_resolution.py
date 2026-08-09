"""Static check that every module can resolve the names it uses.

A module that calls a helper it never imported raises NameError only when that
line runs. `total_file_size` sat unimported in media_encoder.py for a whole
release: it lives in file_operations, media_encoder imports two specific names
from there, and the call is inside encode_media_files - so it fired only for
users with ENABLE_MEDIA_ENCODER on, and no test or harness run touched it.

This walks each module's AST and reports names loaded at runtime that nothing
could have bound: not defined locally, not imported explicitly, not reachable
through a star-import, not a builtin. It is a cheap standing guard over the
branches the test suite does not execute.
"""

import ast
import builtins
import importlib
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODULES = [
    "mkv-auto.py",
    "modules/audio.py",
    "modules/encode_estimator.py",
    "modules/file_operations.py",
    "modules/integrations.py",
    "modules/logger.py",
    "modules/media_encoder.py",
    "modules/misc.py",
    "modules/mkv.py",
    "modules/models.py",
    "modules/preview.py",
    "modules/subs.py",
    "modules/sysmetrics.py",
]


def bound_names(tree):
    """Every name the module body could bind, however it binds it."""
    names = set()
    star_modules = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    star_modules.append(node.module)
                else:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)

    return names, star_modules


def unresolved(path):
    with open(os.path.join(REPO_ROOT, path)) as handle:
        tree = ast.parse(handle.read(), path)

    names, star_modules = bound_names(tree)
    available = set(dir(builtins)) | names

    for module in star_modules:
        imported = importlib.import_module(module)
        available.update(n for n in dir(imported) if not n.startswith("_"))

    missing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in available:
                missing.setdefault(node.id, []).append(node.lineno)
    return missing


@pytest.mark.parametrize("path", MODULES)
def test_every_name_a_module_uses_can_be_resolved(path):
    missing = unresolved(path)
    assert not missing, "unresolvable names in " + path + ": " + ", ".join(
        f"{name} (line{'s' if len(lines) > 1 else ''} "
        f"{', '.join(str(n) for n in lines)})"
        for name, lines in sorted(missing.items()))


def test_the_check_would_catch_a_missing_import(tmp_path):
    """Guard the guard: a module calling something it never imported must be
    reported, or this file is decoration."""
    sample = tmp_path / "sample.py"
    sample.write_text("import os\n\ndef f():\n    return never_imported(os.sep)\n")

    tree = ast.parse(sample.read_text())
    names, _ = bound_names(tree)
    available = set(dir(builtins)) | names

    loads = {n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "never_imported" in loads - available
