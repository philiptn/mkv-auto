"""Synthetic in-memory fixtures for get_wanted_audio_tracks regression tests.

Everything here is plain dicts shaped like the `file_info` mkvmerge JSON the
real pipeline builds - NO real media files are read. Each case also carries the
arguments and the single config flag the function consults.

`DIFF_CASES` are compared bit-for-bit against the `main` branch (ground truth).
`FIX_CASES` are intentional divergences from `main` (the commentary/codec-removal
fix); they are checked against an explicit expected value, not against main.
"""


def track(track_id, language, codec="A_AC3", name=""):
    """Build one audio track entry as mkvmerge would report it."""
    return {
        "type": "audio",
        "id": track_id,
        "properties": {
            "track_name": name,
            "codec_id": codec,
            "language": language,
        },
    }


def video_track(track_id=0):
    return {"type": "video", "id": track_id, "properties": {"codec_id": "V_MPEGH/ISO/HEVC"}}


def file_info(*tracks, file_name="movie.mkv"):
    return {"file_name": file_name, "tracks": [video_track()] + list(tracks)}


def case(name, fi, pref_audio_langs, pref_audio_formats,
         remove_commentary=False, only_keep_one=False):
    return {
        "name": name,
        "file_info": fi,
        "pref_audio_langs": pref_audio_langs,
        "pref_audio_formats": pref_audio_formats,
        "remove_commentary": remove_commentary,
        "only_keep_one_matching_audio_track": only_keep_one,
    }


DIFF_CASES = [
    case("matched_in_order",
         file_info(track(1, "eng"), track(2, "jpn")),
         ["eng", "jpn"], "AC3"),

    case("matched_out_of_order",
         file_info(track(1, "jpn"), track(2, "eng")),
         ["eng", "jpn"], "AC3"),

    case("und_becomes_eng",
         file_info(track(1, "und")),
         ["eng"], "AC3"),

    case("nob_nno_to_nor",
         file_info(track(1, "nob"), track(2, "nno")),
         ["nor"], "AC3"),

    case("original_suffix",
         file_info(track(1, "eng"), track(2, "eng", name="Score (Original)")),
         ["eng"], "AC3"),

    case("original_named_tuple",
         file_info(track(1, "eng"), track(2, "eng", name="Original (5.1)")),
         ["eng"], "AC3"),

    case("compatibility_matched",
         file_info(track(1, "eng", name="Compatibility track"), track(2, "eng")),
         ["eng"], "AC3"),

    case("compatibility_unmatched",
         file_info(track(1, "spa", name="Compatibility track")),
         ["eng"], "AC3"),

    case("commentary_remove_on_matched",
         file_info(track(1, "eng"), track(2, "eng", name="Director Commentary")),
         ["eng"], "AC3", remove_commentary=True),

    case("commentary_remove_off_matched",
         file_info(track(1, "eng"), track(2, "eng", name="Director Commentary")),
         ["eng"], "AC3", remove_commentary=False),

    case("only_keep_one_on",
         file_info(track(1, "eng"), track(2, "eng")),
         ["eng"], "AC3", only_keep_one=True),

    case("only_keep_one_off",
         file_info(track(1, "eng"), track(2, "eng")),
         ["eng"], "AC3", only_keep_one=False),

    case("copy_only",
         file_info(track(1, "eng"), track(2, "jpn")),
         ["eng", "jpn"], "COPY"),

    case("orig_only",
         file_info(track(1, "eng")),
         ["eng"], "ORIG"),

    case("no_audio",
         {"file_name": "movie.mkv", "tracks": [video_track()]},
         ["eng"], "AC3"),

    case("unmatched_added",
         file_info(track(1, "spa"), track(2, "fre")),
         ["eng"], "AC3", only_keep_one=False),

    case("first_track_fallback",
         file_info(track(1, "spa"), track(2, "fre")),
         ["eng"], "AC3", only_keep_one=True),
]


# Intentional divergence from main: an unmatched-language commentary track with a
# lower-case codec id. On main, the parallel-list removal does
# `.remove(codec.upper())` against a list that stored the raw (lower-case) codec
# and raises ValueError. The refactor removes the exact candidate object, so it
# behaves correctly: the track is added then dropped as a commentary track.
FIX_CASES = [
    (
        case("commentary_unmatched_lowercase_codec",
             file_info(track(1, "spa", codec="a_eac3", name="Spanish Commentary")),
             ["eng"], "AC3", remove_commentary=True, only_keep_one=False),
        {
            "wanted_track_ids": [1],
            "default_track_id": 1,
            "needs_processing": True,
            "pref_formats_found": False,
            "tracks_to_convert": [
                {"track_id": 1, "language": "spa", "name": "Spanish Commentary"},
            ],
        },
    ),
]
