"""Tests for the mkvpropedit metadata cleanup in the clutter-removal stage.

Stripping tags, track names and the segment title is cosmetic, but a single
non-zero exit from mkvpropedit used to raise out of the worker, out of
remove_clutter_process and into the top-level handler, which moved *every*
remaining file in the batch to the destination unprocessed and exited 1. It also
reported the failure by printing `result.stderr` - and mkvtoolnix writes its
errors to stdout, so the [ERROR] line was blank and the crash was undiagnosable.

These tests pin the replacement behaviour: retry a failure a few times in case
it was transient, fall back to applying the edits one at a time, and report what
mkvpropedit actually said instead of taking the run down.

No mkvpropedit, no media, no disk - subprocess.run is stubbed throughout.
"""

import os
import subprocess

import pytest

from modules import mkv


class FakeRun:
    """Stands in for subprocess.run, replaying a scripted list of exit codes."""

    def __init__(self, codes, stdout="Error: something went wrong", stderr=""):
        self.codes = list(codes)
        self.stdout = stdout
        self.stderr = stderr
        self.commands = []

    def __call__(self, command, *args, **kwargs):
        self.commands.append(command)
        code = self.codes.pop(0) if self.codes else 0
        return subprocess.CompletedProcess(
            command, code,
            stdout=self.stdout if code else "",
            stderr=self.stderr if code else "",
        )


@pytest.fixture
def no_sleep(monkeypatch):
    """Retries must not actually wait during the tests."""
    monkeypatch.setattr(mkv.time, "sleep", lambda seconds: None)


@pytest.fixture
def runner(monkeypatch, no_sleep):
    def install(codes, **kwargs):
        fake = FakeRun(codes, **kwargs)
        monkeypatch.setattr(mkv.subprocess, "run", fake)
        return fake

    return install


def test_a_clean_run_reports_no_warning(runner):
    fake = runner([0])
    assert mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv") is None
    assert len(fake.commands) == 1


def test_warnings_are_not_failures(runner):
    """mkvpropedit exits 1 for warnings only. check_returncode() treated that as
    fatal, so a file that was edited fine could still abort the batch."""
    fake = runner([1])
    assert mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv") is None
    assert len(fake.commands) == 1


def test_a_transient_failure_is_retried(runner):
    """A momentary disk hiccup should cost a retry, not the file's metadata."""
    fake = runner([2, 0])
    assert mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv") is None
    assert len(fake.commands) == 2


def test_a_permanent_failure_warns_instead_of_raising(runner, capsys):
    """The reported crash: exit 2 on every attempt must not raise."""
    fake = runner([2] * 20, stdout="Error: no track corresponding to track:v1")

    warning = mkv.remove_all_mkv_track_tags(False, "/mkv-auto/files/tmp/file.mkv")

    assert warning is not None
    assert "no track corresponding to track:v1" in warning
    assert "file.mkv" in warning
    # 3 attempts at the combined command, then 2 each for the three single edits
    assert len(fake.commands) == 3 + 3 * 2


def test_the_message_comes_from_stdout(runner):
    """mkvtoolnix writes errors to stdout; reading stderr reported nothing."""
    runner([2] * 20, stdout="Error: the actual reason", stderr="")
    warning = mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv")
    assert "the actual reason" in warning


def test_stderr_is_used_when_stdout_is_empty(runner):
    runner([2] * 20, stdout="", stderr="mkvpropedit: command not found")
    warning = mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv")
    assert "command not found" in warning


def test_the_edits_that_work_are_still_applied(runner):
    """One rejected edit should not cost the other two. Combined command fails
    three times, then only the track:v1 edit keeps failing."""
    fake = runner([2, 2, 2,           # combined
                   0,                 # --tags all:
                   2, 2,              # --edit track:v1 (both attempts)
                   0])                # --edit info

    warning = mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv")

    assert warning is not None
    assert "track:v1" in warning
    assert "--edit info" not in warning
    applied = [c for c, code in zip(fake.commands[3:], [0, 2, 2, 0]) if code == 0]
    assert [c[2] for c in applied] == ["--tags", "--edit"]


def test_tags_are_cleared_with_an_empty_tags_xml(runner):
    """`--tags all:` with no file leaves some tags behind, so the tags are
    replaced with an empty <Tags> set instead."""
    fake = runner([0])
    mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv")
    command = fake.commands[0]
    xml_path = command[command.index("--tags") + 1].removeprefix("all:")
    assert xml_path.endswith(".xml")


def test_the_temporary_xml_is_cleaned_up(runner, monkeypatch):
    """It used to leak on any raising run: os.remove() sat after subprocess.run
    rather than in a finally."""
    written = []
    real_remove = mkv.os.remove
    monkeypatch.setattr(mkv.os, "remove", lambda path: (written.append(path), real_remove(path)))

    def explode(*args, **kwargs):
        raise OSError("mkvpropedit is missing")

    monkeypatch.setattr(mkv.subprocess, "run", explode)

    with pytest.raises(OSError):
        mkv.remove_all_mkv_track_tags(False, "/tmp/file.mkv")

    assert written and written[0].endswith(".xml")
    assert not os.path.exists(written[0])


# --- the stage around it ------------------------------------------------------

class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)

    debug = info
    color = info


def test_a_failed_cleanup_does_not_stop_the_batch(monkeypatch, capsys):
    """remove_clutter_process keeps every filename and finishes the batch, and
    the warning reaches the log rather than the top-level error handler."""
    monkeypatch.setattr(mkv, "get_worker_thread_count", lambda: 2)
    monkeypatch.setattr(mkv, "has_closed_captions", lambda path: False)
    monkeypatch.setattr(
        mkv, "remove_clutter_process_worker",
        lambda debug, input_file, dirpath:
            (input_file, "Could not clean metadata in 'b.mkv': boom")
            if input_file == "b.mkv" else (input_file, None),
    )
    logger = FakeLogger()

    result = mkv.remove_clutter_process(logger, False, ["a.mkv", "b.mkv", "c.mkv"], "/tmp")

    assert result == ["a.mkv", "b.mkv", "c.mkv"]
    assert sum("boom" in message for message in logger.messages) >= 1
