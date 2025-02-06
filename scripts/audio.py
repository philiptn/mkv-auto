import subprocess
import os
import json
import concurrent.futures
from tqdm import tqdm
import re
import uuid
from datetime import datetime

from scripts.misc import *


# Function to extract a single audio track
def extract_audio_track(debug, filename, track, language, name):
    base, _, _ = filename.rpartition('.')
    try:
        audio_language = pycountry.languages.get(alpha_3=language).alpha_2
    except:
        audio_language = language[:-1]
    audio_filename = f"{base}.{track}.{audio_language}.mkv"
    command = ["mkvextract", filename, "tracks", f"{track}:{audio_filename}"]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvextract command: " + result.stderr)

    return audio_filename, 'mkv', name, language


def extract_audio_tracks_in_mkv(internal_threads, debug, filename, track_numbers, audio_languages, audio_names):
    if not track_numbers:
        print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Error: No track numbers passed.")
        return

    if debug:
        print()

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        # Create a mapping of futures to their inputs for ordering
        futures = {
            executor.submit(extract_audio_track, debug, filename, track, language, name): (track, language, name)
            for track, language, name in zip(track_numbers, audio_languages, audio_names)
        }

        # Prepare containers for the results in the correct order
        ordered_results = [None] * len(track_numbers)

        for future in concurrent.futures.as_completed(futures):
            try:
                # Get the original input index for this future
                track, language, name = futures[future]
                index = track_numbers.index(track)

                # Fetch the result and store it in the correct order
                audio_file, audio_extension, updated_audio_name, updated_audio_lang = future.result()
                ordered_results[index] = (audio_file, audio_extension, updated_audio_name, updated_audio_lang)
            except Exception as e:
                # Handle exceptions here, if necessary
                print(f"Error extracting audio track: {e}")
                raise

    # Unzip the ordered results into separate lists
    audio_files, audio_extensions, updated_audio_names, updated_audio_langs = zip(*ordered_results)

    return audio_files, updated_audio_langs, updated_audio_names, audio_extensions


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
                else:
                    preferences.append((None, c, ch))
            else:
                val = item.upper()
                if val == "EOS":
                    preferences.append(("EOS", "AC3", None))
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


def get_pan_filter(source_channels, layout):
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
        else:
            return 'pan=mono|FC=0.7*FL+0.7*FR'

    else:
        return None


def encode_single_preference(file, index, debug, languages, track_names, transformation, codec, ch_str,
                             custom_ffmpeg_options):
    base_and_lang_with_id, _, extension = file.rpartition('.')
    base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
    base, _, original_track_id = base_with_id.rpartition('.')

    source_channels, source_layout = detect_source_channels_and_layout(debug, file)
    chosen_channels = channels_to_int(ch_str) if ch_str else None
    if chosen_channels is None and source_channels is not None:
        chosen_channels = source_channels
    if chosen_channels is None:
        chosen_channels = 2

    chosen_layout = source_layout
    # Limit the chosen channel based on what the source actually is
    chosen_channels = min(int(source_channels), int(chosen_channels))

    # OPUS only supports up to Stereo audio
    if codec == "OPUS":
        chosen_channels = min(2, chosen_channels)
    # Other codecs only supports up to 5.1 audio
    elif codec in ("AC3", "EAC3", "DTS"):
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
    track_name = track_names[index].replace(" (Original)", "")

    # If original no transformation or empty, just copy
    if codec == 'ORIG' and transformation is None or codec == '':
        final_out_ext = extension
        final_out = f"{base}.{unique_id}.{lang}.{final_out_ext}"
        command = ["ffmpeg", "-i", file, "-c:a", "copy"] + custom_ffmpeg_options + [final_out]
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")
        subprocess.run(command, capture_output=True, text=True, check=True)

        pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
        audio_preferences = parse_preferred_codecs(pref_audio_formats)

        if track_name:
            if track_name == 'Original':
                track_name = f"{track_name}"
            elif not track_name.endswith(' (Original)'):
                if len(audio_preferences) == 1:
                    a, pref_codec, b = audio_preferences
                    if len(audio_preferences) == 1 and pref_codec == "ORIG":
                        pass
                else:
                    track_name = f"{track_name} (Original)"
            else:
                track_name = f"{track_name}"
        else:
            track_name = "Original"
        return final_out_ext, languages[index], track_name, unique_id

    # Unique temp wav
    temp_wav = f"{base}.{unique_id}.{lang}.temp.wav"

    # Decode to WAV
    decode_cmd = ["ffmpeg", "-i", file, "-c:a", "pcm_s16le", "-f", "wav", temp_wav]
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(decode_cmd)}{RESET}")
    subprocess.run(decode_cmd, capture_output=True, text=True, check=True)

    final_codec = codec.lower()
    if final_codec in ('orig', 'eos'):
        final_codec = extension

    final_out_ext = final_codec if final_codec != 'orig' else extension
    final_out = f"{base}.{unique_id}.{lang}.{final_out_ext}"
    ffmpeg_final_opts = []
    track_name_final = ''

    # Codec settings
    if codec == 'AAC':
        ffmpeg_final_opts += ['-c:a', 'aac', '-aq', '6', '-strict', '-2']
        if track_name:
            track_name_final = f"AAC (from {track_name})"
        else:
            track_name_final = f"AAC {chosen_layout}"
    elif codec == 'DTS':
        ffmpeg_final_opts += ['-c:a', 'dts', '-strict', '-2']
        if track_name:
            track_name_final = f"DTS (from {track_name})"
        else:
            track_name_final = f"DTS {chosen_layout}"
    elif codec == 'AC3':
        ffmpeg_final_opts += ['-c:a', 'ac3', '-strict', '-2']
        if track_name:
            track_name_final = f"Dolby Digital (from {track_name})"
        else:
            track_name_final = f"Dolby Digital {chosen_layout}"
    elif codec == 'EAC3':
        ffmpeg_final_opts += ['-c:a', 'eac3', '-strict', '-2']
        if track_name:
            track_name_final = f"Dolby Digital Plus (from {track_name})"
        else:
            track_name_final = f"Dolby Digital Plus {chosen_layout}"
    elif codec == 'OPUS':
        ffmpeg_final_opts += ['-c:a', 'opus', '-strict', '-2']
        if track_name:
            track_name_final = f"Opus (from {track_name})"
        else:
            track_name_final = f"Opus {chosen_layout}"
    elif codec == 'WAV':
        ffmpeg_final_opts += ['-c:a', 'pcm_s16le', '-strict', '-2']
        if track_name:
            track_name_final = f"PCM (from {track_name})"
        else:
            track_name_final = f"PCM {chosen_layout}"
    elif codec == 'FLAC':
        ffmpeg_final_opts += ['-c:a', 'flac', '-strict', '-2']
        if track_name:
            track_name_final = f"FLAC (from {track_name})"
        else:
            track_name_final = f"FLAC {chosen_layout}"
    elif codec == 'ORIG':
        ffmpeg_final_opts += ['-c:a', 'copy']
        if track_name:
            if track_name == 'Original':
                track_name_final = f"{track_name}"
            elif not track_name.endswith(' (Original)'):
                track_name_final = f"{track_name} (Original)"
            else:
                track_name_final = f"{track_name}"
        else:
            track_name_final = "Original"

    # Apply transformations
    if transformation == 'EOS':
        compand_filter = (
            'compand=attacks=0:decays=0.3:soft-knee=6:points=-110.00/-110.00|-100.00/-105.00|-88.88/-98.04|-80.00/-90.00|-75.00/-85.00|-63.89/-68.04|-51.56/-51.73|-42.14/-39.32|-34.35/-27.25|-31.43/-22.64|-27.54/-18.38|-24.29/-15.90|-20.07/-13.77|-13.58/-10.18|-5.15/-8.04|2.64/-6.96|10.76/-5.36|20.17/-4.29:gain=0'
        )

        pan_filter = get_pan_filter(source_channels, chosen_layout)

        if pan_filter:
            eos_filter = f'[0:a]{compand_filter},{pan_filter}'
        else:
            # If no pan filter for this layout, just apply compand and limiter
            eos_filter = f'[0:a]{compand_filter}'

        ffmpeg_final_opts += ["-filter_complex", eos_filter]
        chosen_layout_name = chosen_layout
        if chosen_layout == "5.1(side)":
            chosen_layout_name = "5.1"
        if track_name:
            track_name_final = f"Even-Out-Sound (from {track_name})"
        else:
            track_name_final = f"Even-Out-Sound {chosen_layout_name}"
    else:
        if chosen_layout == '5.1':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5:5.1']
        elif chosen_layout == '5.1(side)':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5:5.1(side)']
        elif chosen_layout == '7.1':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1|2|3|4|5|6|7:7.1']
        elif chosen_layout == 'Stereo':
            ffmpeg_final_opts += ['-af', 'channelmap=0|1:stereo']
        elif chosen_layout == 'Mono':
            ffmpeg_final_opts += ['-af', 'channelmap=0:mono']

    final_cmd = ["ffmpeg", "-i", temp_wav] + ffmpeg_final_opts + custom_ffmpeg_options + [final_out]
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(final_cmd)}{RESET}")
    result = subprocess.run(final_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {RED}[ERROR]{RESET} {result.stderr}")
        print(f"{RESET}")
    result.check_returncode()

    os.remove(temp_wav)

    return final_out_ext, languages[index], track_name_final, unique_id


def encode_audio_tracks(internal_threads, debug, audio_files, languages, track_names, preferred_codec_string):
    if not audio_files:
        return

    preferences = parse_preferred_codecs(preferred_codec_string)
    custom_ffmpeg_options = []

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [AUDIO DEBUG] {RESET}Audio format preferences:\n\n{GREEN}{preferences}{RESET}\n")

    # Store futures by (track_index, preference_index) for ordering later
    futures_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        for track_index, file in enumerate(audio_files):
            for pref_index, (transformation, codec, ch_str) in enumerate(preferences):
                future = executor.submit(
                    encode_single_preference, file, track_index, debug, languages, track_names,
                    transformation, codec, ch_str, custom_ffmpeg_options
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
        return (), (), (), ()

    if debug:
        print()

    # Reconstruct results in the correct order
    # For each track in the order of audio_files, and each preference in the order given by preferences
    ordered_results = []
    for track_index in range(len(audio_files)):
        for pref_index in range(len(preferences)):
            if (track_index, pref_index) in results_map:
                ordered_results.append(results_map[(track_index, pref_index)])

    if not ordered_results:
        return (), (), (), ()

    output_audio_files_extensions, output_audio_langs, output_audio_names, all_track_ids = zip(*ordered_results)
    for audio_file in audio_files:
        if os.path.exists(audio_file):
            os.remove(audio_file)

    return output_audio_files_extensions, output_audio_langs, output_audio_names, all_track_ids


def get_wanted_audio_tracks(debug, file_info, pref_audio_langs, remove_commentary, pref_audio_formats):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_audio_tracks:\n")
        print(f"{BLUE}preferred audio languages{RESET}: {pref_audio_langs}")
        print(f"{BLUE}preferred audio codec{RESET}: {pref_audio_formats}")
        print(f"{BLUE}remove commentary tracks{RESET}: {remove_commentary}")

    file_name = file_info["file_name"]

    audio_track_ids = []
    audio_track_languages = []
    audio_track_names = []

    unmatched_audio_track_ids = []
    unmatched_audio_track_languages = []
    unmatched_audio_track_names = []
    unmatched_audio_track_codecs = []

    unmatched_original_audio_track_ids = []
    unmatched_original_audio_track_languages = []
    unmatched_original_audio_track_names = []
    unmatched_original_audio_track_codecs = []

    audio_track_codecs = []

    original_audio_track_ids = []
    original_audio_track_languages = []
    original_audio_track_names = []
    original_audio_track_codecs = []

    first_audio_track_id = -1
    first_audio_track_lang = ''
    first_audio_track_name = ''

    default_audio_track = None
    default_audio_track_set = False
    total_audio_tracks = 0
    needs_processing = False
    first_audio_track_found = False

    all_pref_settings_codecs = []
    audio_preferences = parse_preferred_codecs(pref_audio_formats)
    for transformation, codec, ch_str in audio_preferences:
        all_pref_settings_codecs.append(codec)
    copy_all_audio_tracks = True if len(all_pref_settings_codecs) == 1 and "COPY" in all_pref_settings_codecs else False

    if len(all_pref_settings_codecs) == 1 and "COPY" in all_pref_settings_codecs:
        pref_audio_formats_found = True
    else:
        pref_audio_formats_found = False
        needs_processing = True

    all_track_names = []
    all_track_codecs = []
    all_track_ids = []
    all_track_langs = []
    for track in file_info["tracks"]:
        if track["type"] == "audio":
            track_name = None
            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'codec_id':
                    all_track_codecs.append(value)
                if key == 'language':
                    all_track_langs.append(value)
            if not track_name:
                track_name = ''
            all_track_names.append(track_name)
        all_track_ids.append(track["id"])

    for index, track in enumerate(file_info["tracks"]):
        if track["type"] == "audio":
            track_name = ''
            track_language = ''
            audio_codec = ''
            for key, value in track["properties"].items():
                if key == 'track_name':
                    if not value.lower() in file_name.lower():
                        track_name = value
                if key == 'language':
                    track_language = value
                if key == 'codec_id':
                    audio_codec = value
            if not first_audio_track_found:
                first_audio_track_id = track["id"]
                first_audio_track_lang = track_language
                first_audio_track_name = track_name
                first_audio_track_found = True

            if track_language == 'nob' or track_language == 'nno':
                track_language = 'nor'

            if track_language in pref_audio_langs:
                if 'original' in track_name.lower():
                    original_audio_track_ids.append(track["id"])
                    original_audio_track_names.append(track_name)
                    original_audio_track_languages.append(track_language)
                    original_audio_track_codecs.append(audio_codec)

                # Only keep unique audio tracks that
                # match language, differentiate based on name
                add_track = False
                if audio_track_languages.count(track_language) == 0:
                    add_track = True
                elif not all(track_name == name for name in all_track_names):
                    add_track = True

                if add_track:
                    total_audio_tracks += 1

                    audio_track_ids.append(track["id"])
                    audio_track_languages.append(track_language)
                    audio_track_names.append(track_name)
                    audio_track_codecs.append(audio_codec.upper())

                    if not default_audio_track_set:
                        default_audio_track = track["id"]
                        default_audio_track_set = True

                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower():
                        audio_track_ids.remove(track["id"])
                        audio_track_languages.remove(track_language)
                        audio_track_names.remove(track_name)
                        audio_track_codecs.remove(audio_codec.upper())
                        default_audio_track_set = False

            elif track_language not in pref_audio_langs and not audio_track_ids:
                if 'original' in track_name.lower():
                    unmatched_original_audio_track_ids.append(track["id"])
                    unmatched_original_audio_track_names.append(track_name)
                    unmatched_original_audio_track_languages.append(track_language)
                    unmatched_original_audio_track_codecs.append(audio_codec)

                # Only keep unique audio tracks that
                # match language, differentiate based on name
                add_track = False
                if unmatched_audio_track_languages.count(track_language) == 0:
                    add_track = True
                elif not all(track_name == name for name in all_track_names):
                    add_track = True

                if add_track:
                    total_audio_tracks += 1
                    unmatched_audio_track_ids.append(track["id"])
                    unmatched_audio_track_languages.append(track_language)
                    unmatched_audio_track_names.append(track_name)
                    unmatched_audio_track_codecs.append(audio_codec)

                    if not default_audio_track_set:
                        default_audio_track = track["id"]
                        default_audio_track_set = True

                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower():
                        unmatched_audio_track_ids.remove(track["id"])
                        unmatched_audio_track_languages.remove(track_language)
                        unmatched_audio_track_names.remove(track_name)
                        unmatched_audio_track_codecs.remove(audio_codec.upper())
                        default_audio_track_set = False

    all_audio_track_ids = audio_track_ids
    all_audio_track_langs = audio_track_languages
    all_audio_track_names = audio_track_names

    tracks_ids_to_be_converted = audio_track_ids
    tracks_langs_to_be_converted = audio_track_languages
    tracks_names_to_be_converted = audio_track_names

    # If none of the language selections matched, assign those that are
    # unmatched as audio track ids + langs to keep.
    if not all_audio_track_ids and unmatched_audio_track_ids:
        default_audio_track = unmatched_audio_track_ids[0]
        all_audio_track_ids = unmatched_audio_track_ids
        # If the language "und" (undefined) is in the unmatched languages,
        # assign it to be an english audio track. Else, keep the originals.
        if "und" in unmatched_audio_track_languages[0].lower():
            tracks_ids_to_be_converted = unmatched_audio_track_ids
            tracks_langs_to_be_converted = ['eng']
            tracks_names_to_be_converted = unmatched_audio_track_names
        else:
            tracks_ids_to_be_converted = unmatched_audio_track_ids
            tracks_langs_to_be_converted = unmatched_audio_track_languages
            tracks_names_to_be_converted = unmatched_audio_track_names

    # If there is no audio tracks at all, no processing is needed
    if first_audio_track_id == -1:
        needs_processing = False
    # If the first audio track in the media is not matched,
    # and none other have matched, add it, but place it last in the list.
    if not all_audio_track_ids and first_audio_track_id not in all_audio_track_ids and first_audio_track_id != -1:
        if not default_audio_track:
            default_audio_track = first_audio_track_id
        all_audio_track_ids.append(first_audio_track_id)
        all_audio_track_langs.append(first_audio_track_lang)
        all_audio_track_names.append(first_audio_track_name)
        tracks_ids_to_be_converted.append(first_audio_track_id)
        tracks_langs_to_be_converted.append(first_audio_track_lang)
        tracks_names_to_be_converted.append(first_audio_track_name)

    # If the relative order of the audio track langs is
    # not the same as the found audio langs, it needs processing
    min_index = 0
    for lang in all_audio_track_langs:
        if lang in pref_audio_langs[min_index:]:
            current_index = pref_audio_langs.index(lang, min_index)
            min_index = current_index
        else:
            needs_processing = True
            break

    # If no tracks have been selected for either conversion
    # or extraction, then no processing is needed.
    if not tracks_ids_to_be_converted and all_audio_track_ids:
        needs_processing = False

    # If the wanted audio track ids are smaller than the total amount of
    # audio tracks, then it needs processing (track reduction)
    if len(all_audio_track_ids) != 0 and len(all_audio_track_ids) < total_audio_tracks:
        needs_processing = True

    # If original tracks are found, only keep those
    if original_audio_track_ids or unmatched_original_audio_track_ids:
        if unmatched_original_audio_track_ids and not original_audio_track_ids:
            all_audio_track_ids = unmatched_original_audio_track_ids
            default_audio_track = unmatched_original_audio_track_ids[0]
            tracks_ids_to_be_converted = unmatched_original_audio_track_ids
            tracks_langs_to_be_converted = unmatched_original_audio_track_languages
            tracks_names_to_be_converted = unmatched_original_audio_track_names
        else:
            all_audio_track_ids = original_audio_track_ids
            default_audio_track = original_audio_track_ids[0]
            tracks_ids_to_be_converted = original_audio_track_ids
            tracks_langs_to_be_converted = original_audio_track_languages
            tracks_names_to_be_converted = original_audio_track_names

    # If the preferred audio formats only contains 'COPY', then
    # no tracks will need to be converted or extracted.
    if copy_all_audio_tracks and all_audio_track_ids:
        pref_audio_formats_found = True
        tracks_ids_to_be_converted = []
        tracks_langs_to_be_converted = []
        tracks_names_to_be_converted = []

    if debug:
        print(f"{BLUE}preferred audio codec found in all tracks{RESET}: {pref_audio_formats_found}")
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"\n{BLUE}all wanted audio track ids{RESET}: {all_audio_track_ids}")
        print(f"{BLUE}default audio track id{RESET}: {default_audio_track}")
        print(f"{BLUE}tracks to be converted{RESET}:\n  {BLUE}ids{RESET}: {tracks_ids_to_be_converted}, "
              f"{BLUE}langs{RESET}: {tracks_langs_to_be_converted}, {BLUE}names{RESET}: "
              f"{tracks_names_to_be_converted}\n")

    return (all_audio_track_ids, default_audio_track, needs_processing, pref_audio_formats_found,
            tracks_ids_to_be_converted, tracks_langs_to_be_converted, tracks_names_to_be_converted)
