"""Integration tests for audio.encode_single_preference.

Exercises the single-pass encode (decode + filters + codec in one ffmpeg run)
end to end across the full codec x channel x transformation matrix from
defaults.ini's PREFERRED_AUDIO_FORMATS:

  codecs       : AAC, FLAC, OPUS, WAV (8ch); AC3, DTS, EAC3 (6ch); ORIG (copy)
  transforms   : none, EOS, EOS+
  downmix      : none (keep), 7.1, 5.1, 2.0, 1.0

For every combination it asserts the resulting codec and channel count match
what the clamping rules in encode_single_preference promise, and that the
single-pass path leaves no intermediate *.temp.wav behind. A few cases also pin
the human-readable track name.

Skipped automatically when ffmpeg/ffprobe are not installed.
"""

import os
import glob
import json
import shutil
import subprocess

import pytest

from modules.audio import encode_single_preference, detect_source_channels_and_layout
from modules.models import AudioTrack

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# --- matrix definition (mirrors defaults.ini PREFERRED_AUDIO_FORMATS) ----------

ENCODE_CODECS = ["AAC", "FLAC", "OPUS", "WAV", "AC3", "DTS", "EAC3"]
TRANSFORMS = [None, "EOS", "EOS+"]
CHANNELS = [None, "7.1", "5.1", "2.0", "1.0"]

# Expected ffprobe codec_name per pref codec.
CODEC_NAME = {
    "AAC": "aac", "FLAC": "flac", "OPUS": "opus", "WAV": "pcm_s16le",
    "AC3": "ac3", "DTS": "dts", "EAC3": "eac3",
}
# These codecs are capped at 5.1 (6 channels) by encode_single_preference.
SIX_CHANNEL_CODECS = {"AC3", "DTS", "EAC3"}
CH_TO_INT = {None: None, "7.1": 8, "5.1": 6, "2.0": 2, "1.0": 1}


def expected_channels(codec, ch_str, source_channels):
    """Replicate the channel clamping in encode_single_preference."""
    target = CH_TO_INT[ch_str]
    if target is None:
        target = source_channels
    target = min(source_channels, target)
    if codec in SIX_CHANNEL_CODECS:
        target = min(6, target)
    return target


# --- helpers ------------------------------------------------------------------

def _make_source(path, channel_layout, codec="flac"):
    """Create a 1s silent source with the requested channel layout."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"anullsrc=channel_layout={channel_layout}:sample_rate=48000",
         "-t", "1", "-c:a", codec, path],
        check=True, capture_output=True, text=True,
    )


def _probe(path):
    """Return (codec_name, channels) of the first audio stream."""
    out = subprocess.run(
        ["ffprobe", "-i", path, "-show_streams", "-select_streams", "a",
         "-print_format", "json"],
        check=True, capture_output=True, text=True,
    ).stdout
    stream = json.loads(out)["streams"][0]
    return stream["codec_name"], int(stream["channels"])


def _encode(tmp_path, source_layout, transformation, codec, ch_str,
            source_codec="flac"):
    src = os.path.join(str(tmp_path), "source.mka")
    _make_source(src, source_layout, codec=source_codec)
    track = AudioTrack(path=src, track_id=1, language="eng", name="", extension="mka")
    source_channels, layout = detect_source_channels_and_layout(False, src)
    result = encode_single_preference(
        track, False, transformation, codec, ch_str, [],
        source_channels, layout,
    )
    return result


def _assert_no_temp_wav(tmp_path):
    assert glob.glob(os.path.join(str(tmp_path), "*.temp.wav")) == []


# --- the matrix ---------------------------------------------------------------

_MATRIX = [
    (codec, transform, ch_str)
    for codec in ENCODE_CODECS
    for transform in TRANSFORMS
    for ch_str in CHANNELS
]


@pytest.mark.parametrize(
    "codec,transform,ch_str", _MATRIX,
    ids=[f"{c}-{t or 'plain'}-{ch or 'keep'}" for c, t, ch in _MATRIX],
)
def test_codec_channel_matrix(tmp_path, codec, transform, ch_str):
    # 7.1 source so every downmix target (and the 6ch cap) is reachable.
    result = _encode(tmp_path, "7.1", transform, codec, ch_str)
    codec_name, channels = _probe(result.path)
    assert codec_name == CODEC_NAME[codec], (
        f"{codec}/{transform}/{ch_str}: got codec {codec_name}")
    assert channels == expected_channels(codec, ch_str, 8), (
        f"{codec}/{transform}/{ch_str}: got {channels}ch")
    _assert_no_temp_wav(tmp_path)


# Clamping when the source has fewer channels than requested: a stereo source
# asked for surround must clamp down to stereo, never up.
@pytest.mark.parametrize("codec", ENCODE_CODECS)
@pytest.mark.parametrize("ch_str", [None, "7.1", "5.1", "2.0", "1.0"])
def test_stereo_source_clamps_down(tmp_path, codec, ch_str):
    result = _encode(tmp_path, "stereo", None, codec, ch_str)
    codec_name, channels = _probe(result.path)
    assert codec_name == CODEC_NAME[codec]
    expected = 1 if ch_str == "1.0" else 2  # never exceeds the 2ch source
    assert channels == expected
    _assert_no_temp_wav(tmp_path)


# --- ORIG copy path (only ever produced with transformation=None) -------------

def test_orig_copy_preserves_source(tmp_path):
    result = _encode(tmp_path, "5.1", None, "ORIG", None)
    codec_name, channels = _probe(result.path)
    # ORIG is a stream copy: codec and channel count are untouched.
    assert codec_name == "flac"
    assert channels == 6
    _assert_no_temp_wav(tmp_path)


# --- targeted track-name / behaviour assertions -------------------------------

def test_plain_downmix_name(tmp_path):
    result = _encode(tmp_path, "5.1", None, "AC3", "2.0")
    assert result.name == "Dolby Digital Stereo"


def test_channel_preserving_name(tmp_path):
    result = _encode(tmp_path, "5.1", None, "AAC", None)
    assert result.name == "AAC 5.1"


def test_eos_name(tmp_path):
    result = _encode(tmp_path, "5.1", "EOS", "AC3", None)
    assert result.name == "Even-Out-Sound 5.1"


def test_eos_from_stereo_applies_volume_prefix(tmp_path):
    # Stereo source + EOS -> the old decode-time volume=0.8 must be merged into
    # the single-pass filter chain; output must still be valid stereo.
    result = _encode(tmp_path, "stereo", "EOS", "AAC", "2.0")
    codec_name, channels = _probe(result.path)
    assert codec_name == "aac"
    assert channels == 2
    assert result.name == "Even-Out-Sound Stereo"
    _assert_no_temp_wav(tmp_path)


def test_auto_probe_fallback(tmp_path):
    # Calling without source_channels/layout must still work (it probes itself).
    src = os.path.join(str(tmp_path), "source.mka")
    _make_source(src, "5.1")
    track = AudioTrack(path=src, track_id=1, language="eng", name="", extension="mka")
    result = encode_single_preference(track, False, None, "AAC", None, [])
    codec_name, channels = _probe(result.path)
    assert codec_name == "aac"
    assert channels == 6
    _assert_no_temp_wav(tmp_path)
