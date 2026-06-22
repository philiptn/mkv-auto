import subprocess
import os
import json
import concurrent.futures
from tqdm import tqdm
import re
import uuid
from datetime import datetime

from modules.misc import *


# Track-name / language / codec markers used when classifying audio tracks.
# Names that mark a track as an "Original" mix (kept in preference to others).
ORIGINAL_TRACK_NAMES = ("Original", "Original (Stereo)", "Original (5.1)", "Original (7.1)")
ORIGINAL_SUFFIX = "(Original)"
# Substrings (matched case-insensitively) marking compatibility / commentary tracks.
COMPATIBILITY_MARKER = "compatibility"
COMMENTARY_MARKER = "commentary"
# Undefined language is treated as English.
UNDEFINED_LANG, DEFAULT_UND_LANG = "und", "eng"
# Norwegian bokmål / nynorsk both collapse to the generic Norwegian code.
NORWEGIAN_VARIANTS, NORWEGIAN = ("nob", "nno"), "nor"
# Pseudo-codecs in pref_audio_formats: copy every track as-is / keep only originals.
COPY_CODEC, ORIG_CODEC = "COPY", "ORIG"


def is_original_track(name):
    """True if the track name marks it as an 'Original' audio mix."""
    return name.endswith(ORIGINAL_SUFFIX) or name in ORIGINAL_TRACK_NAMES


def is_compatibility_track(name):
    """True if the track name marks it as a compatibility downmix."""
    return COMPATIBILITY_MARKER in name.lower()


def is_commentary_track(name):
    """True if the track name marks it as a commentary track."""
    return COMMENTARY_MARKER in name.lower()


# Function to extract a single audio track
def extract_audio_track(debug, filename, track, language, name):
    base, _, _ = filename.rpartition('.')
    # Plain working name; the track id only disambiguates files, it is never
    # parsed back out. All metadata travels on the returned AudioTrack.
    audio_filename = f"{base}.aud{track}.mka"

    command = [
        "ffmpeg",
        "-i", filename,
        "-map", f"0:{track}",
        "-c", "copy",
        audio_filename,
        "-y"
    ]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        # If copy fails, try decoding instead
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}Copy failed, retrying with decode{RESET}")
        command = [
            "ffmpeg",
            "-i", filename,
            "-map", f"0:{track}",
            audio_filename,
            "-y"
        ]
        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception("Error executing ffmpeg command: " + result.stderr)

    return AudioTrack(path=audio_filename, track_id=track, language=language, name=name, extension='mka')


def extract_audio_tracks_in_mkv(internal_threads, debug, filename, candidates):
    if not candidates:
        print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Error: No track numbers passed.")
        return []

    if debug:
        print()

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        # Map each future back to its input index so results stay ordered
        futures = {
            executor.submit(extract_audio_track, debug, filename, c.track_id, c.language, c.name): index
            for index, c in enumerate(candidates)
        }

        # Prepare a container for the AudioTrack results in the correct order
        ordered_results = [None] * len(candidates)

        for future in concurrent.futures.as_completed(futures):
            try:
                ordered_results[futures[future]] = future.result()
            except Exception as e:
                # Handle exceptions here, if necessary
                print(f"Error extracting audio track: {e}")
                raise

    return ordered_results


def parse_preferred_codecs(preferred_codec_string):
    preferences = []
    items = [p.strip() for p in preferred_codec_string.split(',')]
    for item in items:
        if '-' in item:
            transformation_part, codec_part = item.split('-', 1)
            transformation = transformation_part.strip().upper()
            if ':' in codec_part:
                c, ch = codec_part.split(':', 1)
                c = c.strip().upper()
                ch = ch.strip()
                preferences.append((transformation, c, ch))
            else:
                c = codec_part.strip().upper()
                preferences.append((transformation, c, None))
        else:
            if ':' in item:
                c, ch = item.split(':', 1)
                c = c.strip().upper()
                ch = ch.strip()
                if c == "EOS":
                    preferences.append(('EOS', 'AC3', ch))
                elif c == "EOS+":
                    preferences.append(('EOS+', 'AC3', ch))
                else:
                    preferences.append((None, c, ch))
            else:
                val = item.upper()
                if val == "EOS":
                    preferences.append(("EOS", "AC3", None))
                elif val == "EOS+":
                    preferences.append(("EOS+", "AC3", None))
                else:
                    preferences.append((None, val, None))
    return preferences


def channels_to_int(ch):
    if ch is None:
        return None
    ch = ch.strip().lower()
    if ch == '5.1':
        return 6
    elif ch == '7.1':
        return 8
    elif ch == '2.0':
        return 2
    elif ch == '1.0':
        return 1
    try:
        return int(ch)
    except ValueError:
        return None


def detect_source_channels_and_layout(debug, file):
    try:
        # Use ffprobe to extract audio stream data in JSON format
        command_probe = [
            'ffprobe', '-i', file, '-show_streams', '-select_streams', 'a', '-print_format', 'json'
        ]
        result = subprocess.run(command_probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        audio_info = json.loads(result.stdout)

        if 'streams' not in audio_info or not audio_info['streams']:
            return None, None  # No audio streams found

        # Assume the first audio stream is the relevant one
        audio_stream = audio_info['streams'][0]
        channel_layout = audio_stream.get('channel_layout', '')
        channels = audio_stream.get('channels', 0)

        # Map codec layout strings to the desired format
        channel_map = {
            '7.1': (8, '7.1'),
            '5.1(side)': (6, '5.1(side)'),
            '5.1': (6, '5.1'),
            'stereo': (2, 'stereo'),
            '2.0': (2, 'stereo'),
            'mono': (1, 'mono'),
            '1.0': (1, 'mono')
        }

        for layout, (num_channels, label) in channel_map.items():
            if layout in channel_layout:
                return num_channels, label

        return channels, None  # Default if no match found

    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"Error processing file: {e}")
        return None, None


def get_pan_filter_eos(source_channels, layout):
    if layout in ('5.1', '5.1(side)'):
        # Channels: FL, FR, FC, LFE, BL, BR
        # Similar logic as before: boost FC, mix some FC into FL/FR, reduce surrounds.
        return (
            'pan=5.1|'
            'FL=0.5*FL|'
            'FR=0.5*FR|'
            'FC=0.6*FC|'
            'LFE=0.3*LFE|'
            'BL=0.3*BL|'
            'BR=0.3*BR'
        )

    elif layout == '7.1':
        # Channels: FL, FR, FC, LFE, BL, BR, SL, SR
        # Similar approach: boost FC, mix some FC into FL/FR,
        # keep LFE as is, and reduce the volume of surrounds and sides.
        return (
            'pan=7.1|'
            'FL=0.5*FL|'
            'FR=0.5*FR|'
            'FC=0.6*FC|'
            'LFE=0.3*LFE|'
            'BL=0.3*BL|'
            'BR=0.3*BR|'
            'SL=0.3*SL|'
            'SR=0.3*SR'
        )

    elif layout == 'Stereo':
        # Input might be multi-channel. We want a stereo downmix that still
        # emphasizes FC and includes others at lower levels.
        # If original source had more channels, this mixes them into FL/FR.
        # For simplicity assume FL, FR, FC, BL, BR, SL, SR, LFE might exist and need mixing.
        # If the source has fewer channels, missing ones are treated as silence by ffmpeg.
        if source_channels > 2:
            return (
                'pan=stereo|'
                'FL=0.5*FL+0.6*FC+0.3*BL+0.3*SL+0.3*LFE|'
                'FR=0.5*FR+0.6*FC+0.3*BR+0.3*SR+0.3*LFE'
            )
        else:
            return (
                'pan=stereo|'
                'FL=0.7*FL|'
                'FR=0.7*FR'
            )

    elif layout == 'Mono':
        if source_channels > 2:
            return 'pan=mono|FC=0.5*FL+0.5*FR+0.6*FC'
        elif source_channels == 2:
            return 'pan=mono|FC=0.7*FL+0.7*FR'
        else:
            return 'pan=mono|FC=0.7*FC'

    else:
        return None


def get_pan_filter_eos_plus(source_channels, layout):
    if layout in ('5.1', '5.1(side)'):
        # Channels: FL, FR, FC, LFE, BL, BR
        # Similar logic as before: boost FC, mix some FC into FL/FR, reduce surrounds.
        return (
            'pan=5.1|'
            'FL=0.3*FL|'
            'FR=0.3*FR|'
            'FC=0.7*FC|'
            'LFE=0.1*LFE|'
            'BL=0.1*BL|'
            'BR=0.1*BR'
        )

    elif layout == '7.1':
        # Channels: FL, FR, FC, LFE, BL, BR, SL, SR
        # Similar approach: boost FC, mix some FC into FL/FR,
        # keep LFE as is, and reduce the volume of surrounds and sides.
        return (
            'pan=7.1|'
            'FL=0.3*FL|'
            'FR=0.3*FR|'
            'FC=0.7*FC|'
            'LFE=0.1*LFE|'
            'BL=0.1*BL|'
            'BR=0.1*BR|'
            'SL=0.1*SL|'
            'SR=0.1*SR'
        )

    elif layout == 'Stereo':
        # Input might be multi-channel. We want a stereo downmix that still
        # emphasizes FC and includes others at lower levels.
        # If original source had more channels, this mixes them into FL/FR.
        # For simplicity assume FL, FR, FC, BL, BR, SL, SR, LFE might exist and need mixing.
        # If the source has fewer channels, missing ones are treated as silence by ffmpeg.
        if source_channels > 2:
            return (
                'pan=stereo|'
                'FL=0.3*FL+0.7*FC+0.1*BL+0.1*SL+0.1*LFE|'
                'FR=0.3*FR+0.7*FC+0.1*BR+0.1*SR+0.1*LFE'
            )
        else:
            return (
                'pan=stereo|'
                'FL=0.7*FL|'
                'FR=0.7*FR'
            )

    elif layout == 'Mono':
        if source_channels > 2:
            return 'pan=mono|FC=0.3*FL+0.3*FR+0.7*FC'
        elif source_channels == 2:
            return 'pan=mono|FC=0.7*FL+0.7*FR'
        else:
            return 'pan=mono|FC=0.7*FC'

    else:
        return None


def encode_single_preference(audio_track, debug, transformation, codec, ch_str,
                             custom_ffmpeg_options, source_channels=None,
                             source_layout=None):
    file = audio_track.path
    lang = audio_track.language
    extension = audio_track.extension
    work_dir = os.path.dirname(file)

    # Source channels/layout depend only on the extracted track, not the
    # preference, so the caller probes once per track and passes it in. Fall
    # back to probing here if called without it.
    if source_channels is None:
        source_channels, source_layout = detect_source_channels_and_layout(debug, file)
    chosen_channels = channels_to_int(ch_str) if ch_str else None
    if chosen_channels is None and source_channels is not None:
        chosen_channels = source_channels

    chosen_layout = source_layout
    # Limit the chosen channel based on what the source actually is
    chosen_channels = min(int(source_channels), int(chosen_channels))

    # These codecs only supports up to 5.1 audio
    if codec in ("AC3", "EAC3", "DTS"):
        chosen_channels = min(6, chosen_channels)

    if chosen_channels == 6:
        chosen_layout = '5.1'
    elif chosen_channels == 8:
        chosen_layout = '7.1'
    elif chosen_channels == 2:
        chosen_layout = 'Stereo'
    elif chosen_channels == 1:
        chosen_layout = 'Mono'

    unique_id = str(uuid.uuid4())
    track_name = audio_track.name.replace(" (Original)", "")

    # If original no transformation or empty, just copy
    if codec == 'ORIG' and transformation is None or codec == '':
        final_out_ext = extension
        # Opaque output path: identity/metadata is carried on the AudioTrack,
        # never encoded into (or parsed from) the filename.
        final_out = os.path.join(work_dir, f"{unique_id}.{final_out_ext}")
        command = ["ffmpeg", "-i", file, "-c:a", "copy"] + custom_ffmpeg_options + [final_out]
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")
        subprocess.run(command, capture_output=True, text=True, check=True)

        pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
        audio_preferences = parse_preferred_codecs(pref_audio_formats)

        if track_name:
            if track_name == 'Original':
                track_name = f"{track_name} ({chosen_layout})"
            elif not track_name.endswith(' (Original)'):
                if len(audio_preferences) == 1:
                    if len(audio_preferences) == 1 and "ORIG" in audio_preferences:
                        pass
                else:
                    track_name = f"{track_name} (Original)"
            else:
                track_name = f"{track_name}"
        else:
            if len(audio_preferences) == 1:
                if len(audio_preferences) == 1 and "ORIG" in audio_preferences:
                    pass
            else:
                track_name = f"Original ({chosen_layout})"
        return AudioTrack(path=final_out, track_id=unique_id, language=lang,
                          name=track_name, extension=final_out_ext)

    # Single-pass: ffmpeg decodes `file` directly into the filter graph below,
    # so no intermediate WAV is written or re-read. If the source is Stereo and
    # the transformation is EOS, the overall volume is first decreased so the EOS
    # compressor is not too aggressive; that gain is merged into the EOS filter
    # chain (eos_volume_prefix) so it runs in the same pass.
    eos_volume_prefix = 'volume=0.8,' if (source_channels <= 2 and
                                          transformation in ("EOS", "EOS+")) else ''

    final_codec = codec.lower()
    if final_codec in ('orig', 'eos', 'eos+'):
        final_codec = extension

    final_out_ext = final_codec if final_codec != 'orig' else extension
    final_out = os.path.join(work_dir, f"{unique_id}.{final_out_ext}")
    ffmpeg_final_opts = []
    track_name_final = ''

    # Codec settings
    if codec == 'AAC':
        ffmpeg_final_opts += ['-c:a', 'aac', '-aq', '6', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"AAC {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"AAC {chosen_layout}"
    elif codec == 'DTS':
        ffmpeg_final_opts += ['-c:a', 'dts', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"DTS {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"DTS {chosen_layout}"
    elif codec == 'AC3':
        ffmpeg_final_opts += ['-c:a', 'ac3', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"Dolby Digital {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"Dolby Digital {chosen_layout}"
    elif codec == 'EAC3':
        ffmpeg_final_opts += ['-c:a', 'eac3', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"Dolby Digital Plus {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"Dolby Digital Plus {chosen_layout}"
    elif codec == 'OPUS':
        ffmpeg_final_opts += ['-c:a', 'libopus', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"Opus {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"Opus {chosen_layout}"
    elif codec == 'WAV':
        ffmpeg_final_opts += ['-c:a', 'pcm_s16le', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"PCM {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"PCM {chosen_layout}"
    elif codec == 'FLAC':
        ffmpeg_final_opts += ['-c:a', 'flac', '-strict', '-2']
        if track_name and track_name != 'Original':
            track_name_final = f"FLAC {chosen_layout} (from {track_name})"
        else:
            track_name_final = f"FLAC {chosen_layout}"
    elif codec == 'ORIG':
        ffmpeg_final_opts += ['-c:a', 'copy']
        track_name_final = track_name

    if transformation == 'EOS':
        compand_filter = (
            'compand=attacks=0:decays=0.3:soft-knee=6:points=-110.00/-110.00|-101.11/-101.15|-93.93/-93.97|-83.52/-84.05|-74.59/-74.81|-65.18/-65.23|-52.29/-51.54|-42.14/-39.32|-34.35/-27.25|-31.43/-22.64|-27.54/-18.38|-24.29/-15.90|-20.07/-13.77|-13.58/-10.18|-5.15/-8.04|2.64/-6.96|10.76/-5.36|20.17/-4.29:gain=0'
        )

        pan_filter = get_pan_filter_eos(source_channels, chosen_layout)

        if pan_filter:
            eos_filter = f'[0:a]{eos_volume_prefix}{compand_filter},{pan_filter}'
        else:
            # If no pan filter for this layout, just apply compand and limiter
            eos_filter = f'[0:a]{eos_volume_prefix}{compand_filter}'

        ffmpeg_final_opts += ["-filter_complex", eos_filter]
        chosen_layout_name = chosen_layout
        if chosen_layout == "5.1(side)":
            chosen_layout_name = "5.1"
        if track_name and track_name != 'Original':
            track_name_final = f"Even-Out-Sound (from {track_name})"
        else:
            track_name_final = f"Even-Out-Sound {chosen_layout_name}"
    elif transformation == 'EOS+':
        compand_filter = (
            'compand=attacks=0:decays=0.3:soft-knee=6:points=-110.00/-110.00|-101.11/-101.15|-93.93/-93.97|-83.52/-84.05|-74.59/-74.81|-65.18/-65.23|-52.29/-51.54|-42.14/-39.32|-34.35/-27.25|-31.43/-22.64|-27.54/-18.38|-24.29/-15.90|-20.07/-13.77|-13.58/-10.18|-5.15/-8.04|2.64/-6.96|10.76/-5.36|20.17/-4.29:gain=0'
        )

        pan_filter = get_pan_filter_eos_plus(source_channels, chosen_layout)

        if pan_filter:
            eos_filter = f'[0:a]{eos_volume_prefix}{compand_filter},{pan_filter}'
        else:
            # If no pan filter for this layout, just apply compand and limiter
            eos_filter = f'[0:a]{eos_volume_prefix}{compand_filter}'

        ffmpeg_final_opts += ["-filter_complex", eos_filter]
        chosen_layout_name = chosen_layout
        if chosen_layout == "5.1(side)":
            chosen_layout_name = "5.1"
        if track_name and track_name != 'Original':
            track_name_final = f"Even-Out-Sound+ (from {track_name})"
        else:
            track_name_final = f"Even-Out-Sound+ {chosen_layout_name}"
    else:
        if chosen_layout == '5.1':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5:5.1']
        elif chosen_layout == '5.1(side)':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5:5.1(side)']
        elif chosen_layout == '7.1':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5|6|7:7.1']
        elif chosen_layout == 'Stereo':
            ffmpeg_final_opts += ['-ac', '2']  # Use automatic downmixing
        elif chosen_layout == 'Mono':
            ffmpeg_final_opts += ['-ac', '1']  # Use automatic downmixing

    final_cmd = ["ffmpeg", "-i", file] + ffmpeg_final_opts + custom_ffmpeg_options + [final_out]
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(final_cmd)}{RESET}")
    result = subprocess.run(final_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {RED}[ERROR]{RESET} {result.stderr}")
        print(f"{RESET}")
    result.check_returncode()

    return AudioTrack(path=final_out, track_id=unique_id, language=lang,
                      name=track_name_final, extension=final_out_ext)


def encode_audio_tracks(internal_threads, debug, audio_tracks, preferred_codec_string):
    if not audio_tracks:
        return []

    preferences = parse_preferred_codecs(preferred_codec_string)
    custom_ffmpeg_options = []

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [AUDIO DEBUG] {RESET}Audio format preferences:\n\n{GREEN}{preferences}{RESET}\n")

    # Store futures by (track_index, preference_index) for ordering later
    futures_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        for track_index, audio_track in enumerate(audio_tracks):
            # Probe once per track; reused across all preferences for this track.
            source_channels, source_layout = detect_source_channels_and_layout(
                debug, audio_track.path)
            for pref_index, (transformation, codec, ch_str) in enumerate(preferences):
                future = executor.submit(
                    encode_single_preference, audio_track, debug,
                    transformation, codec, ch_str, custom_ffmpeg_options,
                    source_channels, source_layout
                )
                futures_map[future] = (track_index, pref_index)

        # Collect results
        results_map = {}
        for future in concurrent.futures.as_completed(futures_map):
            track_idx, pref_idx = futures_map[future]
            try:
                res = future.result()
                # Store result keyed by (track_idx, pref_idx) so we can restore order
                results_map[(track_idx, pref_idx)] = res
            except Exception as e:
                if debug:
                    print(f"Error processing track {track_idx}, preference {pref_idx}: {e}")
                    traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                    print(f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                    raise

    if not results_map:
        return []

    if debug:
        print()

    # Reconstruct results in the correct order
    # For each track in the order of audio_tracks, and each preference in the order given by preferences
    ordered_results = []
    for track_index in range(len(audio_tracks)):
        for pref_index in range(len(preferences)):
            if (track_index, pref_index) in results_map:
                ordered_results.append(results_map[(track_index, pref_index)])

    if not ordered_results:
        return []

    for audio_track in audio_tracks:
        if os.path.exists(audio_track.path):
            os.remove(audio_track.path)

    return ordered_results


def get_wanted_audio_tracks(debug, file_info, pref_audio_langs, remove_commentary, pref_audio_formats):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_audio_tracks:\n")
        print(f"{BLUE}preferred audio languages{RESET}: {pref_audio_langs}")
        print(f"{BLUE}preferred audio codec{RESET}: {pref_audio_formats}")
        print(f"{BLUE}remove commentary tracks{RESET}: {remove_commentary}")

    file_name = file_info["file_name"]

    only_keep_one_matching_audio_track = check_config(config, 'audio', 'only_keep_one_matching_audio_track')

    # Candidate buckets, each a list[AudioTrackCandidate]. A track may land in
    # several buckets; the cascade below decides which bucket ultimately wins.
    matched = []                  # language matched a preference
    unmatched = []                # no language matched (fallback pool)
    original = []                 # matched + flagged as an "Original" mix
    unmatched_original = []       # unmatched + flagged as an "Original" mix
    compatibility = []            # matched + compatibility downmix
    unmatched_compatibility = []  # unmatched + compatibility downmix

    first_audio_track_id = -1
    first_audio_track_lang = ''
    first_audio_track_name = ''

    default_audio_track = None
    default_audio_track_set = False
    total_audio_tracks = 0
    needs_processing = False
    first_audio_track_found = False
    und_track_found = False

    all_pref_settings_codecs = [codec for _, codec, _ in parse_preferred_codecs(pref_audio_formats)]
    copy_all_audio_tracks = len(all_pref_settings_codecs) == 1 and COPY_CODEC in all_pref_settings_codecs
    only_orig_pref = len(all_pref_settings_codecs) == 1 and ORIG_CODEC in all_pref_settings_codecs

    if copy_all_audio_tracks:
        pref_audio_formats_found = True
    else:
        pref_audio_formats_found = False
        needs_processing = True

    for track in file_info["tracks"]:
        if track["type"] != "audio":
            continue
        total_audio_tracks += 1

        properties = track["properties"]
        track_name = properties.get("track_name", "") or ""
        # Title names already encoded in the file name carry no extra info.
        if track_name.lower() in file_name.lower():
            track_name = ''
        track_language = properties.get("language", "")
        audio_codec = properties.get("codec_id", "")

        if track_language == UNDEFINED_LANG:
            track_language = DEFAULT_UND_LANG
            und_track_found = True

        if not first_audio_track_found:
            first_audio_track_id = track["id"]
            first_audio_track_lang = track_language
            first_audio_track_name = track_name
            first_audio_track_found = True

        if track_language in NORWEGIAN_VARIANTS:
            track_language = NORWEGIAN

        candidate = AudioTrackCandidate(track["id"], track_language, track_name, audio_codec)

        if track_language in pref_audio_langs:
            add_track = False
            if is_original_track(track_name):
                original.append(candidate)
            if is_compatibility_track(track_name):
                compatibility.append(candidate)
                add_track = False
            if not only_keep_one_matching_audio_track or \
                    [c.language for c in matched].count(track_language) == 0:
                add_track = True

            if add_track:
                matched.append(candidate)
                if not default_audio_track_set:
                    default_audio_track = track["id"]
                    default_audio_track_set = True

                # Removes commentary track if main track(s) is already added, and if pref is set to true
                if remove_commentary and is_commentary_track(track_name):
                    matched.remove(candidate)
                    default_audio_track_set = False

        elif track_language not in pref_audio_langs and not matched:
            add_track = False
            if is_original_track(track_name):
                unmatched_original.append(candidate)

            if not only_keep_one_matching_audio_track:
                add_track = True

            if is_compatibility_track(track_name):
                unmatched_compatibility.append(candidate)
                add_track = False

            if add_track:
                unmatched.append(candidate)
                if not default_audio_track_set:
                    default_audio_track = track["id"]
                    default_audio_track_set = True

                # Removes commentary track if main track(s) is already added, and if pref is set to true
                if remove_commentary and is_commentary_track(track_name):
                    unmatched.remove(candidate)
                    default_audio_track_set = False

    # The tracks we keep ("wanted") and the tracks we convert/extract start out
    # as the same matched list (mirroring the original aliasing); the cascade
    # below may repoint them, and the COPY branch later splits them apart.
    wanted = matched
    to_convert = wanted
    # The order check uses the kept languages as they stood after matching only.
    # It deliberately ignores the unmatched/compatibility/original repointing
    # below (matching the original behavior), so snapshot it here.
    order_check_langs = [c.language for c in matched]

    # If none of the language selections matched, fall back to the unmatched pool.
    # (The "und" special-case in the original is unreachable - undefined languages
    # are normalized to English before classification - so the unmatched tracks
    # are simply kept as-is.)
    if not wanted and unmatched:
        default_audio_track = unmatched[0].track_id
        wanted = unmatched
        to_convert = unmatched

    # Only add compatibility tracks if no other match has been found
    if not wanted and (unmatched_compatibility or compatibility):
        if compatibility:
            default_audio_track = compatibility[0].track_id
            wanted = compatibility
            to_convert = compatibility
        if not compatibility and unmatched_compatibility:
            default_audio_track = unmatched_compatibility[0].track_id
            wanted = unmatched_compatibility
            to_convert = unmatched_compatibility

    # If there is no audio tracks at all, no processing is needed
    if first_audio_track_id == -1:
        needs_processing = False

    # If the first audio track in the media is not matched,
    # and none other have matched, add it, but place it last in the list.
    if (not wanted and (first_audio_track_id not in [c.track_id for c in wanted])) and first_audio_track_id != -1:
        if not default_audio_track:
            default_audio_track = first_audio_track_id
        # wanted and to_convert are still the same list here, so this lands in both.
        to_convert.append(AudioTrackCandidate(first_audio_track_id, first_audio_track_lang, first_audio_track_name))
        order_check_langs.append(first_audio_track_lang)

    # If the relative order of the audio track langs is
    # not the same as the found audio langs, it needs processing
    min_index = 0
    for lang in order_check_langs:
        if lang in pref_audio_langs[min_index:]:
            current_index = pref_audio_langs.index(lang, min_index)
            min_index = current_index
        else:
            needs_processing = True
            break

    # If no tracks have been selected for either conversion
    # or extraction, then no processing is needed.
    if not to_convert and wanted:
        needs_processing = False

    if (len(wanted) != 0 and len(wanted) < total_audio_tracks) or und_track_found:
        needs_processing = True
    elif len(wanted) != 0 and len(wanted) == total_audio_tracks and only_orig_pref:
        needs_processing = False

    # If original tracks are found, only keep those
    if original or unmatched_original:
        needs_processing = True
        if unmatched_original and not original:
            wanted = unmatched_original
            default_audio_track = unmatched_original[0].track_id
            to_convert = unmatched_original
        elif original and not unmatched_original:
            wanted = original
            default_audio_track = original[0].track_id
            to_convert = original

    # If the preferred audio formats only contains 'COPY', then
    # no tracks will need to be converted or extracted.
    if copy_all_audio_tracks and wanted:
        pref_audio_formats_found = True
        to_convert = []
        needs_processing = False

    wanted_track_ids = [c.track_id for c in wanted]

    if debug:
        print(f"{BLUE}preferred audio codec found in all tracks{RESET}: {pref_audio_formats_found}")
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"\n{BLUE}all wanted audio track ids{RESET}: {wanted_track_ids}")
        print(f"{BLUE}default audio track id{RESET}: {default_audio_track}")
        print(f"{BLUE}tracks to be converted{RESET}:\n  {BLUE}ids{RESET}: {[c.track_id for c in to_convert]}, "
              f"{BLUE}langs{RESET}: {[c.language for c in to_convert]}, {BLUE}names{RESET}: "
              f"{[c.name for c in to_convert]}\n")

    return WantedAudioTracks(
        wanted_track_ids=wanted_track_ids,
        default_track_id=default_audio_track,
        needs_processing=needs_processing,
        pref_formats_found=pref_audio_formats_found,
        tracks_to_convert=to_convert,
    )
