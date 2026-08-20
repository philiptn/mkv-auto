"""Tests for the mkvpropedit metadata cleanup in the clutter-removal stage.

Stripping tags, track names and the segment title is cosmetic, but a single
non-zero exit from mkvpropedit used to raise out of the worker, out of
remove_clutter_process and into the top-level handler, which moved *every*
remaining file in the batch to the destination unprocessed and exited 1. It also
reported the failure by printing `result.stderr` - and mkvtoolnix writes its
errors to stdout, so the [ERROR] line was blank and the crash was undiagnosable.

These tests pin the replacement behaviour: retry a failure a few times in case
it was transient, fall back to applying the edits one at a time, rebuild the
container when mkvpropedit cannot navigate it, and record what mkvpropedit
actually said in the debug log instead of taking the run down or spamming the
console with it.

No mkvpropedit, no media - subprocess.run is stubbed throughout.
"""

import os
import subprocess

import pytest

from modules import mkv

# What mkvpropedit says about a container it cannot edit in place
UNNAVIGABLE = ("The file is being analyzed.\nError: Modification of properties in the section "
               "'Track headers' was requested, but no corresponding level 1 element was found "
               "in the file. The file has not been modified.")


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
    """The reported crash: exit 2 on every attempt must not raise, and must not
    print anything - the message is returned for the debug log."""
    fake = runner([2] * 20, stdout="Error: no track corresponding to track:v1")

    warning = mkv.remove_all_mkv_track_tags(False, "/mkv-auto/files/tmp/file.mkv")

    assert warning is not None
    assert "no track corresponding to track:v1" in warning
    assert "file.mkv" in warning
    # 3 attempts at the combined command, then 2 each for the three single edits
    assert len(fake.commands) == 3 + 3 * 2
    assert capsys.readouterr().out == ""


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


# --- the container mkvpropedit cannot navigate --------------------------------

@pytest.fixture
def rebuild(monkeypatch, tmp_path, no_sleep):
    """A file mkvpropedit rejects structurally, with mkvmerge stubbed out."""
    def install(mkvmerge_code=0, writes_output=True):
        source = tmp_path / "file.mkv"
        source.write_text("original")
        monkeypatch.setattr(mkv, "get_mkv_video_track_id", lambda path: 0)

        commands = []

        def fake_run(command, *args, **kwargs):
            commands.append(command)
            if command[0] == "mkvmerge":
                if writes_output:
                    output = command[command.index("--output") + 1]
                    with open(output, "w") as handle:
                        handle.write("rebuilt")
                return subprocess.CompletedProcess(command, mkvmerge_code, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 2, stdout=UNNAVIGABLE, stderr="")

        monkeypatch.setattr(mkv.subprocess, "run", fake_run)
        return source, commands

    return install


def test_an_unnavigable_container_is_not_retried(rebuild):
    """Retrying an in-place edit the container cannot support only wastes the
    backoff - each invocation gets exactly one attempt before the rebuild."""
    source, commands = rebuild()

    mkv.remove_all_mkv_track_tags(False, str(source))

    edits = [c for c in commands if c[0] == "mkvpropedit"]
    assert len(edits) == 1 + 3  # combined once, then each of the three edits once


def test_an_unnavigable_container_is_rebuilt(rebuild):
    """mkvmerge writes a container every tool can navigate, clearing the tags,
    the title and the video track name in the same pass."""
    source, commands = rebuild()

    note = mkv.remove_all_mkv_track_tags(False, str(source))

    assert note.startswith("Rebuilt the container of 'file.mkv'")
    assert source.read_text() == "rebuilt"
    assert list(source.parent.glob("*_clean.mkv")) == []

    merge = next(c for c in commands if c[0] == "mkvmerge")
    assert "--no-global-tags" in merge and "--no-track-tags" in merge
    assert merge[merge.index("--title") + 1] == ""
    assert merge[merge.index("--track-name") + 1] == "0:"
    assert merge[merge.index("--default-track-flag") + 1] == "0:yes"
    assert merge[-1] == str(source)


def test_a_failed_rebuild_leaves_the_source_alone(rebuild):
    """A half-written remux must never replace the file it came from."""
    source, _ = rebuild(mkvmerge_code=2)

    note = mkv.remove_all_mkv_track_tags(False, str(source))

    assert "could not rebuild the container" in note
    assert source.read_text() == "original"
    assert list(source.parent.glob("*_clean.mkv")) == []


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
    """The debug sink log_debug() writes to, kept apart from the console."""

    def __init__(self):
        self.debug_messages = []

    def debug(self, message):
        self.debug_messages.append(message)


def test_a_failed_cleanup_does_not_stop_the_batch(monkeypatch, capsys):
    """remove_clutter_process keeps every filename and finishes the batch, and
    what happened reaches the debug log rather than the console or the top-level
    error handler."""
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
    assert sum("boom" in message for message in logger.debug_messages) == 1
    assert "boom" not in capsys.readouterr().out
