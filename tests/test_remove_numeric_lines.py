"""Unit tests for the digit-only line filter in the SDH pass.

A lone number on its own line is a leftover - a sign translation, an OCR
artifact, a stray index - and gets dropped. The test is strict on purpose:
anything but digits on the line makes it real dialogue, so "154:", "458!",
"1,000" and "- 325" all stay.

Runs on synthetic SRT files in tmp_path, no media and no SubtitleEdit.
"""

import pytest

# The minimal test venv documented in run-tests.sh does not carry the full
# subtitle toolchain that modules/subs.py imports, so skip rather than fail.
subs_module = pytest.importorskip("modules.subs")
pysrt = pytest.importorskip("pysrt")

remove_numeric_only_lines = subs_module.remove_numeric_only_lines


def build_srt(tmp_path, texts, name="sub.srt"):
    """Write an SRT whose entries carry `texts`, one second apart."""
    path = tmp_path / name
    blocks = []
    for index, text in enumerate(texts, start=1):
        start = f"00:00:{index:02d},000"
        end = f"00:00:{index:02d},500"
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


def texts_of(path):
    return [sub.text for sub in pysrt.open(path)]


def test_digit_only_entry_is_dropped(tmp_path):
    path = build_srt(tmp_path, ["Hello there.", "325", "Goodbye."])
    remove_numeric_only_lines(path)
    assert texts_of(path) == ["Hello there.", "Goodbye."]


@pytest.mark.parametrize("text", [
    "154:",
    "458!",
    "1,000",
    "3.5",
    "- 325",
    "<i>325</i>",
    "Room 325",
    "325 dollars",
])
def test_numbers_with_anything_else_are_kept(tmp_path, text):
    path = build_srt(tmp_path, [text])
    remove_numeric_only_lines(path)
    assert texts_of(path) == [text]


def test_only_the_numeric_line_of_an_entry_goes(tmp_path):
    path = build_srt(tmp_path, ["Room 325\n325"])
    remove_numeric_only_lines(path)
    assert texts_of(path) == ["Room 325"]


def test_surrounding_whitespace_still_counts_as_digit_only(tmp_path):
    path = build_srt(tmp_path, ["Keep me.", " 325 "])
    remove_numeric_only_lines(path)
    assert texts_of(path) == ["Keep me."]


def test_already_empty_entries_are_dropped(tmp_path):
    path = build_srt(tmp_path, ["Hello there.", "", "Goodbye."])
    remove_numeric_only_lines(path)
    assert texts_of(path) == ["Hello there.", "Goodbye."]


def test_file_without_numeric_lines_is_unchanged(tmp_path):
    path = build_srt(tmp_path, ["Hello there.", "Room 325", "- 458!"])
    before = texts_of(path)
    remove_numeric_only_lines(path)
    assert texts_of(path) == before


def test_timings_survive_the_pass(tmp_path):
    path = build_srt(tmp_path, ["First.", "42", "Second."])
    remove_numeric_only_lines(path)
    subs = pysrt.open(path)
    assert [str(sub.start) for sub in subs] == ["00:00:01,000", "00:00:03,000"]
    assert [str(sub.end) for sub in subs] == ["00:00:01,500", "00:00:03,500"]
