import subprocess
import os
import concurrent.futures
from tqdm import tqdm
import re
from datetime import datetime

from scripts.misc import *


# Function to extract a single audio track
def extract_audio_track(debug, filename, track, language, name):
    base, _, _ = filename.rpartition('.')
    audio_filename = f"{base}.{track}.{language[:-1]}.mkv"
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        tasks = [executor.submit(extract_audio_track, debug, filename, track, language, name)
                 for track, language, name in zip(track_numbers, audio_languages, audio_names)]
        results = [future.result() for future in concurrent.futures.as_completed(tasks)]

    audio_files, audio_extensions, updated_audio_names, updated_audio_langs = zip(*results)

    return audio_files, updated_audio_langs, updated_audio_names, audio_extensions


def encode_audio_track(file, index, debug, languages, track_names, output_codec, custom_ffmpeg_options):
    base_and_lang_with_id, _, extension = file.rpartition('.')
    base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
    base, _, track_id = base_with_id.rpartition('.')

    # Get the audio stream info using FFmpeg
    command_probe = ['ffmpeg', '-i', file, '-hide_banner']
    result = subprocess.run(command_probe, stderr=subprocess.PIPE, text=True)
    audio_info = result.stderr

    # Search for audio codec information and determine the channel layout
    channel_layout = []
    codec_pattern = re.compile(r'Audio: ([^\n]+)')
    codec_match = codec_pattern.search(audio_info)
    if codec_match:
        codec_info = codec_match.group(1)
        if '5.1(side)' in codec_info or '5.1' in codec_info:
            if not output_codec.lower() == 'aac':
                channel_layout = ['-af', 'channelmap=channel_layout=5.1']
        elif '7.1' in codec_info:
            if not output_codec.lower() == 'aac':
                channel_layout = ['-af', 'channelmap=channel_layout=7.1']

    command = (["ffmpeg", "-i", file] + channel_layout
               + custom_ffmpeg_options +
               ["-strict", "-2", f"{base}.{track_id}.{lang}.{output_codec.lower()}"])
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing ffmpeg command: " + result.stderr)

    output_extension = output_codec.lower()
    output_lang = languages[index]
    output_name = ''

    return output_extension, output_lang, output_name, track_id


def encode_audio_tracks(internal_threads, debug, audio_files, languages, track_names, output_codec,
                        other_files, other_langs, other_names, keep_original_audio, other_track_ids):
    if not audio_files:
        return

    # Old version without center channel boost
    #custom_ffmpeg_options = ['-aq', '6', '-ac', '2', '-filter_complex', '[0:a]pan=stereo|c0=c0+c2|c1=c1+c2[out]', '-map', '[out]'] if output_codec.lower() == 'aac' else []
    custom_ffmpeg_options = ['-aq', '6', '-ac', '2',
                             '-filter_complex',
                             '[0:a]dynaudnorm,pan=stereo|FL<0.2FL+0.8FC+0.1BL|FR<0.2FR+0.8FC+0.1BR'] \
        if output_codec.lower() == 'aac' else []

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=internal_threads) as executor:
        futures = [executor.submit(encode_audio_track, file, index,
                                   debug, languages, track_names, output_codec, custom_ffmpeg_options)
                   for index, file in enumerate(audio_files)]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    output_audio_files_extensions, output_audio_langs, output_audio_names, all_track_ids = zip(*results)

    if keep_original_audio:
        output_audio_files_extensions += tuple(ext for file in audio_files for ext in [file.rpartition('.')[-1]])
        output_audio_langs += tuple(languages)  # Convert languages to a tuple before concatenating
        output_audio_names += tuple(name if name else "Original" for name in track_names)
        all_track_ids += tuple(file.rpartition('.')[0].rpartition('.')[0].rpartition('.')[-1] for file in audio_files)
    else:
        for audio_file in audio_files:
            os.remove(audio_file)

    output_audio_files_extensions = (tuple(ext for file in other_files for ext in [file.rpartition('.')[-1]])
                                     + output_audio_files_extensions)
    output_audio_langs = tuple(other_langs) + output_audio_langs
    output_audio_names = tuple(other_names) + output_audio_names
    all_track_ids = tuple(other_track_ids) + all_track_ids

    return output_audio_files_extensions, output_audio_langs, output_audio_names, all_track_ids


def get_wanted_audio_tracks(debug, file_info, pref_audio_langs, remove_commentary, pref_audio_codec):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_audio_tracks:\n")
        print(f"{BLUE}preferred audio languages{RESET}: {pref_audio_langs}")
        print(f"{BLUE}preferred audio codec{RESET}: {pref_audio_codec}")
        print(f"{BLUE}remove commentary tracks{RESET}: {remove_commentary}")

    file_name = file_info["file_name"]

    audio_track_ids = []
    audio_track_languages = []
    audio_track_names = []

    unmatched_audio_track_ids = []
    unmatched_audio_track_languages = []
    unmatched_audio_track_names = []
    unmatched_audio_track_codecs = []

    removed_audio_track_ids = []
    removed_audio_track_languages = []
    removed_audio_track_names = []
    removed_audio_track_codecs = []

    pref_audio_track_ids = []
    pref_audio_track_languages = []
    pref_audio_track_names = []

    audio_track_codecs = []

    tracks_ids_to_be_converted = []
    tracks_langs_to_be_converted = []
    tracks_names_to_be_converted = []

    other_tracks_ids = []
    other_tracks_langs = []
    other_tracks_names = []

    first_audio_track_id = -1
    first_audio_track_lang = ''
    first_audio_track_codec = ''
    first_audio_track_name = ''

    default_audio_track = None
    default_audio_track_set = False
    pref_default_audio_track = ''
    total_audio_tracks = 0
    preferred_audio_codec = pref_audio_codec
    needs_processing = False
    first_audio_track_found = False
    pref_codec_replaced_main = []

    # Check if there are any commentary tracks
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

    commentary_tracks_found = any("commentary" in track.lower() for track in all_track_names)

    for index, track in enumerate(file_info["tracks"]):
        if track["type"] == "audio":
            total_audio_tracks += 1

            track_name = ''
            track_language = ''
            audio_codec = ''
            preferred_audio_codec = pref_audio_codec
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
                first_audio_track_codec = audio_codec
                first_audio_track_name = track_name
                first_audio_track_found = True

            # If the preferred audio codec is not defined ('false'), set it
            # to current audio codec for that track
            if preferred_audio_codec.lower() == 'false':
                preferred_audio_codec = audio_codec

            if track_language in pref_audio_langs:
                if preferred_audio_codec in audio_codec.upper():
                    if (not remove_commentary and commentary_tracks_found) or (
                            "Original" in all_track_names[total_audio_tracks - 1]) \
                            or (pref_audio_track_languages.count(track_language) == 0):
                        # If the previous selected audio track language (not in pref codec) is the same matched
                        # language, then it should be removed, as a new replacement in the preferred codec has been found.
                        if audio_track_languages:
                            if audio_track_languages[-1] == track_language:
                                pref_default_audio_track = track["id"]
                                default_audio_track_set = True
                                removed_audio_track_ids.append(audio_track_ids[-1])
                                audio_track_ids.pop()
                                removed_audio_track_languages.append(audio_track_languages[-1])
                                audio_track_languages.pop()
                                removed_audio_track_names.append(audio_track_names[-1])
                                audio_track_names.pop()
                                removed_audio_track_codecs.append(audio_track_codecs[-1])
                                audio_track_codecs.pop()
                                pref_codec_replaced_main.append(track_language)

                        pref_audio_track_ids.append(track["id"])
                        pref_audio_track_languages.append(track_language)
                        pref_audio_track_names.append(track_name)
                        audio_track_codecs.append(audio_codec.upper())

                        if not default_audio_track_set:
                            pref_default_audio_track = track["id"]
                            default_audio_track_set = True

                        if remove_commentary and "commentary" in track_name.lower():
                            pref_audio_track_ids.remove(track["id"])
                            pref_audio_track_languages.remove(track_language)
                            pref_audio_track_names.remove(track_name)
                            audio_track_codecs.remove(audio_codec.upper())
                            # If all the tracks have now been removed due
                            # to if statement above, re-add it
                            if not audio_track_codecs and removed_audio_track_codecs:
                                audio_track_ids.append(removed_audio_track_ids[-1])
                                audio_track_languages.append(removed_audio_track_languages[-1])
                                audio_track_names.append(removed_audio_track_names[-1])
                                audio_track_codecs.append(removed_audio_track_codecs[-1])
                            else:
                                default_audio_track_set = False

                elif preferred_audio_codec not in audio_codec.upper():
                    if ((not remove_commentary and commentary_tracks_found)
                        or (audio_track_languages.count(track_language) == 0 and pref_audio_track_languages.count(
                                track_language) == 0)) \
                            or ("Original" in all_track_names[total_audio_tracks - 1]):

                        # If the next audio track is an original audio track that has previously been
                        # converted, it does not need to be converted again, but simply kept. Therefore, will
                        # be added as a "preferred audio codec" even though it may not be.
                        if "Original" in all_track_names[total_audio_tracks - 1] and len(all_track_names) > 1:
                            if all_track_codecs[total_audio_tracks - 2].upper() != preferred_audio_codec:
                                if audio_track_ids:
                                    audio_track_ids.pop()
                                    audio_track_languages.pop()
                                    audio_track_names.pop()
                                    audio_track_codecs.pop()

                                audio_track_ids.append(all_track_ids[total_audio_tracks - 1])
                                audio_track_languages.append(all_track_langs[total_audio_tracks - 1])
                                audio_track_names.append(all_track_names[total_audio_tracks - 1])
                                audio_track_codecs.append(all_track_codecs[total_audio_tracks - 1])

                            else:
                                pref_audio_track_ids.append(track["id"])
                                pref_audio_track_languages.append(track_language)
                                pref_audio_track_names.append(track_name)
                                audio_track_codecs.append(preferred_audio_codec)

                        else:
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

            else:
                unmatched_audio_track_ids.append(track["id"])
                unmatched_audio_track_languages.append(track_language)
                unmatched_audio_track_names.append(track_name)
                unmatched_audio_track_codecs.append(audio_codec)

    all_audio_track_ids = pref_audio_track_ids + audio_track_ids
    all_audio_track_langs = pref_audio_track_languages + audio_track_languages
    all_audio_track_names = pref_audio_track_names + audio_track_names

    if not audio_track_codecs:
        audio_track_codecs = unmatched_audio_track_codecs

    # If the preferred audio codec is in all the matching tracks, then it is fully found
    if all(preferred_audio_codec in item for item in audio_track_codecs):
        pref_audio_codec_found = True
        all_audio_track_ids = pref_audio_track_ids
        default_audio_track = pref_default_audio_track
    else:
        pref_audio_codec_found = False
        needs_processing = True
        tracks_ids_to_be_converted = audio_track_ids
        tracks_langs_to_be_converted = audio_track_languages
        tracks_names_to_be_converted = audio_track_names
        other_tracks_ids = pref_audio_track_ids
        other_tracks_langs = pref_audio_track_languages
        other_tracks_names = pref_audio_track_names

    if pref_audio_codec.lower() == 'false' or default_audio_track is None:
        default_audio_track = pref_default_audio_track
        needs_processing = False

    # If none of the language selections matched, assign those that are
    # unmatched as audio track ids + langs to keep.
    if not all_audio_track_langs:
        default_audio_track = unmatched_audio_track_ids[0]

        # If the language "und" (undefined) is in the unmatched languages,
        # assign it to be an english audio track. Else, keep the originals.
        if "und" in unmatched_audio_track_languages[0].lower():
            all_audio_track_ids = unmatched_audio_track_ids
            if (pref_audio_codec.lower() not in unmatched_audio_track_codecs[0].lower()) \
                    and pref_audio_codec.lower() != 'false':
                tracks_langs_to_be_converted = ['eng']
                tracks_ids_to_be_converted = unmatched_audio_track_ids
                tracks_names_to_be_converted = unmatched_audio_track_names
            else:
                other_tracks_langs = ['eng']
                other_tracks_ids = unmatched_audio_track_ids
                other_tracks_names = unmatched_audio_track_names
            needs_processing = True
        else:
            for index, codec in enumerate(unmatched_audio_track_codecs):
                if (pref_audio_codec.lower() not in codec.lower()) \
                        and pref_audio_codec.lower() != 'false':
                    if remove_commentary and "commentary" in unmatched_audio_track_names[index].lower():
                        continue
                    else:
                        tracks_langs_to_be_converted.append(unmatched_audio_track_languages[index])
                        tracks_ids_to_be_converted.append(unmatched_audio_track_ids[index])
                        all_audio_track_ids.append(unmatched_audio_track_ids[index])
                        tracks_names_to_be_converted.append(unmatched_audio_track_names[index])
                else:
                    if remove_commentary and "commentary" in unmatched_audio_track_names[index].lower():
                        continue
                    else:
                        other_tracks_langs.append(unmatched_audio_track_languages[index])
                        other_tracks_ids.append(unmatched_audio_track_ids[index])
                        all_audio_track_ids.append(unmatched_audio_track_ids[index])
                        other_tracks_names.append(unmatched_audio_track_names[index])
            if tracks_langs_to_be_converted:
                needs_processing = True

    # If the first audio track in the media is not matched, add it,
    # but place it last in the list. Unless a preferred codec has replaced a track above it,
    # then don't add the first audio track id
    if first_audio_track_id not in all_audio_track_ids and not pref_codec_replaced_main:
        all_audio_track_ids.append(first_audio_track_id)
        all_audio_track_langs.append(first_audio_track_lang)
        all_audio_track_names.append(first_audio_track_name)
        preferred_audio_codec = pref_audio_codec
        needs_processing = True
        if preferred_audio_codec.lower() != 'false':
            if first_audio_track_codec not in preferred_audio_codec:
                tracks_ids_to_be_converted.append(first_audio_track_id)
                tracks_langs_to_be_converted.append(first_audio_track_lang)
                tracks_names_to_be_converted.append(first_audio_track_name)
        else:
            other_tracks_ids.append(first_audio_track_id)
            other_tracks_langs.append(first_audio_track_lang)
            other_tracks_names.append(first_audio_track_name)

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
    if not other_tracks_ids and not tracks_ids_to_be_converted and all_audio_track_ids:
        needs_processing = False

    # If the wanted audio track ids are smaller than the total amount of
    # audio tracks, then it needs processing (track reduction)
    if len(all_audio_track_ids) != 0 and len(all_audio_track_ids) < total_audio_tracks:
        needs_processing = True

    if debug:
        print(f"{BLUE}preferred audio codec found in all tracks{RESET}: {pref_audio_codec_found}")
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"\n{BLUE}all wanted audio track ids{RESET}: {all_audio_track_ids}")
        print(f"{BLUE}default audio track id{RESET}: {default_audio_track}")
        print(f"{BLUE}tracks to be extracted{RESET}:\n  {BLUE}ids{RESET}: {other_tracks_ids}, "
              f"{BLUE}langs{RESET}: {other_tracks_langs}, {BLUE}names{RESET}: {other_tracks_names}")
        print(f"{BLUE}tracks to be converted{RESET}:\n  {BLUE}ids{RESET}: {tracks_ids_to_be_converted}, "
              f"{BLUE}langs{RESET}: {tracks_langs_to_be_converted}, {BLUE}names{RESET}: "
              f"{tracks_names_to_be_converted}\n")

    return (all_audio_track_ids, default_audio_track, needs_processing, pref_audio_codec_found,
            tracks_ids_to_be_converted, tracks_langs_to_be_converted, other_tracks_ids, other_tracks_langs,
            tracks_names_to_be_converted, other_tracks_names)
