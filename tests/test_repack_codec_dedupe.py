"""Tests for the codec normalisation behind repack's duplicate-track dedupe.

repack keeps an original track and drops any converted track that would land on
the same (codec, language, channels) - converting DTS-HD to DTS and keeping both
just stores the same audio twice. Deciding "same codec" is unify_codec's job.

It used to read `if acodec.endswith("ac3"): return "ac3"`, which quietly swept
E-AC-3 in with AC-3. Configure `ORIG, EAC3` against an AC-3 source and the
E-AC-3 track you asked for looked like a duplicate of the original and was
dropped - no error, no warning, just a missing track.
"""

import pytest

from modules.mkv import unify_codec


@pytest.mark.parametrize("codec", ["dts", "dts-hd", "dtshd", "dts_hd_ma"])
def test_the_dts_family_collapses(codec):
    """Converting DTS-HD to DTS really would duplicate the original."""
    assert unify_codec(codec) == "dts"


def test_eac3_is_not_ac3():
    """The bug: 'eac3'.endswith('ac3') is True, so E-AC-3 was folded into AC-3
    and any requested EAC3 track collided with the kept ORIG."""
    assert unify_codec("eac3") == "eac3"
    assert unify_codec("ac3") == "ac3"
    assert unify_codec("eac3") != unify_codec("ac3")


@pytest.mark.parametrize("codec", ["opus", "aac", "flac", "truehd", "pcm_s16le"])
def test_every_other_codec_is_left_alone(codec):
    assert unify_codec(codec) == codec


def test_an_eac3_conversion_is_not_a_duplicate_of_an_ac3_original():
    """The dedupe decision itself, as repack makes it: key a kept ORIG by
    (codec, lang, channels) and check a converted track against it."""
    def key(codec, lang, channels):
        return (unify_codec(codec), lang, channels)

    has_orig = {key("ac3", "eng", 6)}

    assert key("eac3", "eng", 6) not in has_orig    # different codec: keep it
    assert key("ac3", "eng", 6) in has_orig         # same codec: drop it
    assert key("ac3", "jpn", 6) not in has_orig     # different language
    assert key("ac3", "eng", 2) not in has_orig     # different channel count


def test_a_dts_conversion_of_a_dts_original_is_still_a_duplicate():
    """The fix must not disable the dedupe it exists for."""
    def key(codec, lang, channels):
        return (unify_codec(codec), lang, channels)

    has_orig = {key("dts-hd", "eng", 6)}
    assert key("dts", "eng", 6) in has_orig
