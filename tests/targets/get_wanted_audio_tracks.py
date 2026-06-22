"""Differential-test target for audio.get_wanted_audio_tracks.

Understands both return shapes so dev and main results are comparable:
  * main -> a 7-tuple
            (wanted_ids, default_id, needs_processing, pref_formats_found,
             convert_ids, convert_langs, convert_names)
  * dev  -> a WantedAudioTracks dataclass with a list of AudioTrackCandidate
"""

import modules.audio as audio

from . import register


def _normalize(ret):
    """Turn either return shape into the canonical comparison dict."""
    if isinstance(ret, tuple):
        (wanted_ids, default_id, needs_processing, pref_formats_found,
         convert_ids, convert_langs, convert_names) = ret
        # Pairing mirrors how extract_audio_tracks_in_mkv zips the three lists.
        convert = [
            {"track_id": tid, "language": lang, "name": name}
            for tid, lang, name in zip(convert_ids, convert_langs, convert_names)
        ]
        return {
            "wanted_track_ids": list(wanted_ids),
            "default_track_id": default_id,
            "needs_processing": needs_processing,
            "pref_formats_found": pref_formats_found,
            "tracks_to_convert": convert,
        }
    # dev dataclass
    return {
        "wanted_track_ids": list(ret.wanted_track_ids),
        "default_track_id": ret.default_track_id,
        "needs_processing": ret.needs_processing,
        "pref_formats_found": ret.pref_formats_found,
        "tracks_to_convert": [
            {"track_id": c.track_id, "language": c.language, "name": c.name}
            for c in ret.tracks_to_convert
        ],
    }


@register("get_wanted_audio_tracks")
def run_case(case):
    # The only config key get_wanted_audio_tracks reads is this one; override it
    # on the shared config dict so behavior is driven by the fixture, not the
    # branch's defaults.ini.
    audio.config["audio"]["only_keep_one_matching_audio_track"] = \
        case["only_keep_one_matching_audio_track"]

    ret = audio.get_wanted_audio_tracks(
        False,
        case["file_info"],
        case["pref_audio_langs"],
        case["remove_commentary"],
        case["pref_audio_formats"],
    )
    return _normalize(ret)
