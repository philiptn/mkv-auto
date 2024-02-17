import subprocess
import json
import os
import re
from tqdm import tqdm
from datetime import datetime
import shutil


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def convert_video_to_mkv(video_file, output_file):
    # FFmpeg command
    command = [
        'ffmpeg', '-fflags', '+genpts', '-i', video_file, '-c', 'copy',
        '-y', output_file
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    # Verifying completion
    return_code = process.returncode
    if return_code != 0:
        print(f"Failed to convert {video_file}")
        print("Error from FFmpeg:", stderr.decode())  # Print the exact error

    os.remove(video_file)


def convert_all_videos_to_mkv(input_folder, silent):
    video_files = []
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.endswith(('.mp4', '.avi', '.m4v', '.webm')):
                video_files.append(os.path.join(root, file))

    total_files = len(video_files)
    if total_files == 0:
        return

    pbar = tqdm(total=total_files, bar_format='\r{desc}{bar:8} {percentage:3.0f}%', leave=False, disable=silent)
    for i, video_file in enumerate(video_files, start=1):
        pbar.set_description(f'[UTC {get_timestamp()}] [INFO] Converting file {i} of {total_files} to MKV')
        if video_file.endswith('.mp4'):
            # If the function returns "True", then there are
            # tx3g subtitles in the mp4 file that needs to be converted.
            if convert_mp4_to_mkv_with_subtitles(video_file):
                output_file = os.path.splitext(video_file)[0] + '.mkv'
            else:
                output_file = os.path.splitext(video_file)[0] + '.mkv'
                convert_video_to_mkv(video_file, output_file)
        else:
            output_file = os.path.splitext(video_file)[0] + '.mkv'
            convert_video_to_mkv(video_file, output_file)
        pbar.update(1)  # Update progress bar by one file
    pbar.close()


def get_mkv_info(filename):
    command = ["mkvmerge", "-J", filename]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stderr)

    # Parse the JSON output and pretty-print it
    parsed_json = json.loads(result.stdout)
    pretty_json = json.dumps(parsed_json, indent=2)
    return parsed_json, pretty_json


def get_mkv_video_codec(filename):
    parsed_json, _ = get_mkv_info(filename)
    for track in parsed_json['tracks']:
        if track['type'] == 'video':
            return track['codec']
    return None


def has_closed_captions(file_path):
    # Command to get ffprobe output
    command = ['ffprobe', file_path]

    # Execute the command and capture the output
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode()

    # Search for "Closed Captions" in the video stream description
    if "Stream #0:0: Video:" in output and "Closed Captions" in output:
        return True
    else:
        return False


def get_all_audio_languages(filename):
    all_langs = []
    parsed_json, _ = get_mkv_info(filename)
    for track in parsed_json['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def get_all_subtitle_languages(filename):
    all_langs = []
    parsed_json, _ = get_mkv_info(filename)
    for track in parsed_json['tracks']:
        if track['type'] == 'subtitles':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def remove_all_mkv_track_tags(filename):
    command = ['mkvpropedit', filename,
               '--edit', 'track:v1', '--set', 'name=',
               '--edit', 'track:a1', '--set', 'name=',
               '--set', 'flag-default=1', '-e', 'info', '-s', 'title=']
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvpropedit command: " + result.stderr)


def convert_mp4_to_mkv_with_subtitles(mp4_file):
    def clean_srt_file(srt_file):
        with open(srt_file, 'r', encoding='utf-8') as file:
            content = file.read()

        cleaned_content = re.sub(r'<[^>]+>', '', content)

        with open(srt_file, 'w', encoding='utf-8') as file:
            file.write(cleaned_content)

    def get_subtitle_streams(file):
        cmd = ['ffprobe', '-loglevel', 'error', '-show_streams', file]
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
            output = result.stdout.decode()
        except subprocess.CalledProcessError:
            print(f"Error occurred while running ffprobe on {file}")
            return None

        pattern = r'\[STREAM\]\nindex=(\d+)\n(?:[^\[]*?)codec_name=mov_text(?:[^\[]*?)\nTAG:language=(\w+)'
        return re.findall(pattern, output)

    subtitle_streams = get_subtitle_streams(mp4_file)
    if not subtitle_streams:
        return False

    srt_files = []

    for index, language in subtitle_streams:
        srt_file = f"{os.path.splitext(mp4_file)[0]}_{index}.{language}.srt"
        cmd = ['ffmpeg', '-y', '-i', mp4_file, '-map', f'0:{index}', '-c:s', 'srt', srt_file]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except subprocess.CalledProcessError:
            print(f"Error occurred while extracting subtitles from {mp4_file}")
            return None
        clean_srt_file(srt_file)
        srt_files.append((srt_file, language))

    mkv_file = os.path.splitext(mp4_file)[0] + '.mkv'

    mkvmerge_cmd = ['mkvmerge', '-o', mkv_file, mp4_file]
    for srt_file, language in srt_files:
        mkvmerge_cmd.extend(['--language', f'0:{language}', srt_file])

    try:
        subprocess.run(mkvmerge_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        print(f"Error occurred while merging files into {mkv_file}")
        return None

    for srt_file, _ in srt_files:
        os.remove(srt_file)
    os.remove(mp4_file)

    return True


def remove_cc_hidden_in_file(filename):
    print(f"[UTC {get_timestamp()}] [FFMPEG] Removing Closed Captions (CC) from video stream...")
    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ['ffmpeg', '-i', filename, '-codec', 'copy', '-map', '0',
               '-map', '-v', '-map', 'V', '-bsf:v', 'filter_units=remove_types=6', temp_filename]

    # Remove empty entries
    command = [arg for arg in command if arg]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing ffmpeg command: " + result.stderr)
        print(f"[UTC {get_timestamp()}] [INFO] Skipping ffmpeg process...")
        try:
            os.remove(temp_filename)
        except:
            pass
    else:
        os.remove(filename)
        shutil.move(temp_filename, filename)


def strip_tracks_in_mkv(filename, audio_tracks, default_audio_track,
                        sub_tracks, default_subs_track, always_enable_subs):
    print(f"[UTC {get_timestamp()}] [MKVMERGE] Filtering audio and subtitle tracks...")
    audio_track_names_list = []
    subtitle_tracks = ''
    subs_default_track = ''
    default_subs_track_str = ''
    subs_track_names_list = []

    # If no audio tracks has been selected, copy all as fallback,
    # else, generate copy string
    if len(audio_tracks) == 0:
        audio = ''
        audio_tracks_str = ''
        audio_track_names_list = []
        audio_default_track = ''
        default_audio_track_str = ''
    else:
        audio = '--atracks'
        audio_tracks_str = ','.join(map(str, audio_tracks))
        audio_default_track = "--default-track"
        default_audio_track_str = f'{default_audio_track}:yes'
        # For generating an empty title for each audio track
        for audio_track in audio_tracks:
            audio_track_names_list += ["--track-name", f"{audio_track}:"]

    if always_enable_subs and len(sub_tracks) != 0:
        subs_default_track = "--default-track"
        default_subs_track_str = f'{default_subs_track}:yes'
        for sub_track in sub_tracks:
            subs_track_names_list += ["--track-name", f"{sub_track}:"]
    if len(sub_tracks) == 0:
        subs = "--no-subtitles"
    else:
        subs = '--subtitle-tracks'
        subtitle_tracks = ','.join(map(str, sub_tracks))

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ["mkvmerge",
               "--output", temp_filename,
               audio, audio_tracks_str,
               audio_default_track, default_audio_track_str] + audio_track_names_list + [
                  subs, subtitle_tracks,
                  subs_default_track, default_subs_track_str] + subs_track_names_list + [
                  filename]
    # Remove empty entries
    command = [arg for arg in command if arg]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing mkvmerge command: " + result.stdout)
        print("Continuing...")

    os.remove(filename)
    shutil.move(temp_filename, filename)


def repack_tracks_in_mkv(filename, sub_filetypes, sub_languages, pref_subs_langs,
                         audio_filetypes, audio_languages, pref_audio_langs, audio_track_ids, sub_track_ids):
    sub_files_list = []
    final_sub_languages = sub_languages
    audio_files_list = []
    final_audio_languages = audio_languages
    final_audio_filetypes = []
    final_sub_filetypes = []
    final_audio_track_ids = audio_track_ids
    final_sub_track_ids = sub_track_ids

    # If the first preferred language is found in the audio languages,
    # reorder the list to place the preferred language first
    if audio_languages:
        # Function to get the priority of each language
        def get_priority(lang):
            try:
                return pref_audio_langs.index(lang)
            except ValueError:
                return len(pref_audio_langs)

        # Zip the audio_languages and audio_filetypes together, sort them, and unzip them
        paired = zip(audio_languages, audio_filetypes, audio_track_ids)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_audio_languages, sorted_audio_filetypes, sorted_audio_track_ids = zip(*sorted_paired)

        # Convert tuples back to lists if necessary
        final_audio_languages = list(sorted_audio_languages)
        final_audio_filetypes = list(sorted_audio_filetypes)
        final_audio_track_ids = list(sorted_audio_track_ids)

    # Initialize first_pref_audio_index to -1 (indicating no match found yet)
    first_pref_audio_index = -1
    # Iterate through pref_audio_langs to find the first matching language in audio_languages
    for i, lang in enumerate(pref_audio_langs):
        if lang in final_audio_languages:
            first_pref_audio_index = i
            break

    # If the first preferred language is found in the sub languages,
    # reorder the list to place the preferred language first
    if sub_languages:
        # Function to get the priority of each language
        def get_priority(lang):
            try:
                return pref_subs_langs.index(lang)
            except ValueError:
                return len(pref_subs_langs)

        # Zip the subs_languages and subs_filetypes together, sort them, and unzip them
        paired = zip(sub_languages, sub_filetypes, sub_track_ids)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids = zip(*sorted_paired)

        # Convert tuples back to lists if necessary
        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    default_locked = False
    default_track_str = []

    for index, filetype in enumerate(final_audio_filetypes):
        if not default_locked:
            if final_audio_languages[index] == pref_audio_langs[first_pref_audio_index]:
                default_track_str = "0:yes"
                default_locked = True
            else:
                default_track_str = "0:no"
        else:
            default_track_str = "0:no"
        langs_str = f"0:{final_audio_languages[index]}"
        filelist_str = f"{base}.{final_audio_track_ids[index]}.{final_audio_languages[index][:-1]}.{filetype}"
        audio_files_list += '--default-track', default_track_str, '--language', langs_str, filelist_str

    default_locked = False
    default_track_str = []
    for index, filetype in enumerate(final_sub_filetypes):
        # mkvmerge does not support the .sub file as input,
        # and requires the .idx specified instead
        if filetype == "sub":
            filetype = "idx"
        if not default_locked:
            if filetype == "srt":
                default_track_str = "0:yes"
                default_locked = True
        else:
            default_track_str = "0:no"
        langs_str = f"0:{final_sub_languages[index]}"
        filelist_str = f"{base}.{final_sub_track_ids[index]}.{final_sub_languages[index][:-1]}.{filetype}"
        sub_files_list += '--default-track', default_track_str, '--language', langs_str, filelist_str

    if audio_filetypes:
        # Remove all subtitle and audio tracks
        print(f"[UTC {get_timestamp()}] [MKVMERGE] Removing existing tracks in mkv...")
        command = ["mkvmerge", "--output", temp_filename, "--no-subtitles", "--no-audio", filename]
    else:
        # Remove all subtitle tracks
        print(f"[UTC {get_timestamp()}] [MKVMERGE] Removing existing subtitles in mkv...")
        command = ["mkvmerge", "--output", temp_filename, "--no-subtitles", filename]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)
    os.remove(filename)
    shutil.move(temp_filename, filename)

    print(f"[UTC {get_timestamp()}] [MKVMERGE] Repacking tracks into mkv...")
    if audio_filetypes:
        command = ["mkvmerge",
                   "--output", temp_filename, filename] + audio_files_list + sub_files_list
    else:
        command = ["mkvmerge",
                   "--output", temp_filename, filename] + sub_files_list

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)

    os.remove(filename)
    shutil.move(temp_filename, filename)

    if audio_filetypes:
        for index, filetype in enumerate(final_audio_filetypes):
            os.remove(f"{base}.{final_audio_track_ids[index]}.{final_audio_languages[index][:-1]}.{filetype}")
    if sub_filetypes:
        # Need to add the .idx file as well to filetypes list for final deletion
        for index, filetype in enumerate(final_sub_filetypes):
            if filetype == "sub":
                final_sub_filetypes.append('idx')
                final_sub_languages.append(final_sub_languages[index])
                final_sub_track_ids.append(final_sub_track_ids[index])

        for index, filetype in enumerate(final_sub_filetypes):
            os.remove(f"{base}.{final_sub_track_ids[index]}.{final_sub_languages[index][:-1]}.{filetype}")


def get_wanted_audio_tracks(file_info, pref_audio_langs, remove_commentary, pref_audio_codec):
    audio_track_ids = []
    audio_track_languages = []
    unmatched_audio_track_ids = []
    unmatched_audio_track_languages = []
    unmatched_audio_track_codecs = []

    pref_audio_track_ids = []
    pref_audio_track_languages = []
    audio_track_codecs = []
    latest_audio_codec = ''
    preferred_audio_codec = pref_audio_codec

    tracks_ids_to_be_converted = []
    tracks_langs_to_be_converted = []
    other_tracks_ids = []
    other_tracks_langs = []
    first_audio_track_id = -1
    first_audio_track_lang = ''
    first_audio_track_codec = ''

    default_audio_track = ''
    pref_default_audio_track = ''
    total_audio_tracks = 0
    needs_processing = False
    pref_audio_codec_found = False
    first_audio_track_found = False

    for track in file_info["tracks"]:
        if track["type"] == "audio":
            total_audio_tracks += 1

            track_name = ''
            track_language = ''
            audio_codec = ''
            preferred_audio_codec = pref_audio_codec
            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'language':
                    track_language = value
                if key == 'codec_id':
                    audio_codec = value
            if not first_audio_track_found:
                first_audio_track_id = track["id"]
                first_audio_track_lang = track_language
                first_audio_track_codec = audio_codec
                first_audio_track_found = True
            if track_language in pref_audio_langs:
                # If the preferred audio codec is not defined ('false'), set it
                # to current audio codec for that track
                if preferred_audio_codec.lower() == 'false':
                    preferred_audio_codec = audio_codec
                    latest_audio_codec = audio_codec
                if pref_audio_track_languages.count(
                        track_language) == 0 and preferred_audio_codec in audio_codec.upper():
                    pref_audio_track_ids.append(track["id"])
                    pref_audio_track_languages.append(track_language)
                    audio_track_codecs.append(audio_codec.upper())
                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower() \
                            and track_language in audio_track_languages:
                        pref_audio_track_ids.remove(track["id"])
                        pref_audio_track_languages.remove(track_language)
                    else:
                        pref_default_audio_track = track["id"]
                elif audio_track_languages.count(
                        track_language) == 0 and preferred_audio_codec not in audio_codec.upper():
                    audio_track_ids.append(track["id"])
                    audio_track_languages.append(track_language)
                    audio_track_codecs.append(audio_codec.upper())
                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower() \
                            and track_language in audio_track_languages:
                        audio_track_ids.remove(track["id"])
                        audio_track_languages.remove(track_language)
                    else:
                        default_audio_track = track["id"]
            else:
                unmatched_audio_track_ids.append(track["id"])
                unmatched_audio_track_languages.append(track_language)
                unmatched_audio_track_codecs.append(audio_codec)

    all_audio_track_ids = audio_track_ids + pref_audio_track_ids
    all_audio_track_langs = audio_track_languages + pref_audio_track_languages

    if len(audio_track_ids) != 0 and len(audio_track_ids) < total_audio_tracks:
        needs_processing = True

    preferred_audio_codec = pref_audio_codec

    # If the preferred audio codec is in all of the matching tracks, or with unique langs, then it is fully found
    if all(preferred_audio_codec in item for item in audio_track_codecs):
        pref_audio_codec_found = True
        all_audio_track_ids = pref_audio_track_ids
        default_audio_track = pref_default_audio_track
    elif any(preferred_audio_codec in codec for codec in audio_track_codecs) and all(
            pref_audio_track_languages[0] in item for item in all_audio_track_langs):
        pref_audio_codec_found = True
        all_audio_track_ids = pref_audio_track_ids
        default_audio_track = pref_default_audio_track
    else:
        pref_audio_codec_found = False
        tracks_ids_to_be_converted = audio_track_ids
        tracks_langs_to_be_converted = audio_track_languages
        other_tracks_ids = pref_audio_track_ids
        other_tracks_langs = pref_audio_track_languages

    if pref_audio_codec.lower() == 'false':
        default_audio_track = pref_default_audio_track

    # If none of the language selections matched, assign those that are
    # unmatched as audio track ids + langs to keep.
    if not all_audio_track_langs:
        all_audio_track_ids = unmatched_audio_track_ids
        default_audio_track = unmatched_audio_track_ids[0]

        # If the language "und" (undefined) is in the unmatched languages,
        # assign it to be an english audio track. Else, keep the originals.
        if "und" in unmatched_audio_track_languages[0].lower():
            if (unmatched_audio_track_codecs[0].lower() not in pref_audio_codec.lower()) \
                    and preferred_audio_codec.lower() != 'false':
                tracks_langs_to_be_converted = ['eng']
                tracks_ids_to_be_converted = unmatched_audio_track_ids
            else:
                other_tracks_langs = ['eng']
                other_tracks_ids = unmatched_audio_track_ids
            needs_processing = True
        else:
            tracks_langs_to_be_converted = unmatched_audio_track_languages

    # If the first audio track in the media is not matched, add it,
    # but place it last in the list
    if first_audio_track_id not in all_audio_track_ids:
        all_audio_track_ids.append(first_audio_track_id)
        all_audio_track_langs.append(first_audio_track_lang)
        preferred_audio_codec = pref_audio_codec
        needs_processing = True
        if preferred_audio_codec.lower() != 'false':
            if first_audio_track_codec not in preferred_audio_codec:
                tracks_ids_to_be_converted.append(first_audio_track_id)
                tracks_langs_to_be_converted.append(first_audio_track_lang)
        else:
            other_tracks_ids.append(first_audio_track_id)
            other_tracks_langs.append(first_audio_track_lang)

    iter_pref_audio_langs = iter(pref_audio_langs)
    # If the relative order of the audio track langs is
    # not the same as the found audio langs, it needs processing
    if not all(elem in iter_pref_audio_langs for elem in all_audio_track_langs):
        needs_processing = True

    return all_audio_track_ids, default_audio_track, needs_processing, pref_audio_codec_found, \
        tracks_ids_to_be_converted, tracks_langs_to_be_converted, other_tracks_ids, other_tracks_langs


def get_wanted_subtitle_tracks(file_info, pref_langs):
    total_subs_tracks = 0
    pref_subs_langs = pref_langs

    subs_track_ids = []
    subs_track_languages = []

    unmatched_subs_track_ids = []
    unmatched_subs_track_languages = []

    default_subs_track = ''
    all_sub_filetypes = []
    selected_sub_filetypes = []
    sub_filetypes = []
    srt_track_ids = []
    ass_track_ids = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False

    # Check for matching subs languages
    for track in file_info["tracks"]:
        if track["type"] == "subtitles":
            track_language = ''
            for key, value in track["properties"].items():
                if key == 'language':
                    track_language = value
            if track_language in pref_subs_langs:
                subs_track_languages.append(track_language)
            else:
                unmatched_subs_track_languages.append(track_language)
    # If none of the subs track matches the language preference,
    # set the preferred sub languages to the ones found, and run the detection
    # using that as the reference.
    if not subs_track_languages:
        pref_subs_langs = unmatched_subs_track_languages
    # Reset the found subs languages
    subs_track_languages = []

    for track in file_info["tracks"]:
        if track["type"] == "subtitles":
            total_subs_tracks += 1
            track_language = ''
            track_name = ''
            forced_track_val = ''

            for key, value in track["properties"].items():
                if key == 'language':
                    track_language = value
                if key == 'forced_track':
                    forced_track_val = value
                if key == 'track_name':
                    track_name = value

            if forced_track_val or "forced" in track_name.lower():
                forced_track = True
            else:
                forced_track = False

            if track_language in pref_subs_langs:
                needs_processing = True
                needs_sdh_removal = True

                # If the track language is "und" (undefined), assume english subtitles
                if track_language.lower() == "und":
                    track_language = 'eng'
                    pref_subs_langs.append('eng')

                if subs_track_languages.count(track_language) == 0 and not forced_track:
                    selected_sub_filetypes.append(track["codec"])
                    subs_track_ids.append(track["id"])
                    subs_track_languages.append(track_language)

                    if track["codec"] == "HDMV PGS":
                        sub_filetypes.append('sup')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "VobSub":
                        sub_filetypes.append('sub')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "SubRip/SRT":
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if (track["codec"] != "SubRip/SRT" and track["codec"] != "SubStationAlpha") \
                            and subs_track_languages.count(track_language) == 1:
                        if track["codec"] == "HDMV PGS" and sub_filetypes.count("sup") == 0:
                            sub_filetypes.append('sup')
                            selected_sub_filetypes.append(track["codec"])
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            needs_convert = True
                            needs_processing = True
                        elif track["codec"] == "VobSub" and sub_filetypes.count("sub") == 0:
                            sub_filetypes.append('sub')
                            selected_sub_filetypes.append(track["codec"])
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            needs_convert = True
                            needs_processing = True

                        if 'srt' in sub_filetypes:
                            sub_filetypes.remove('srt')

                        if 'ass' in sub_filetypes:
                            sub_filetypes.remove('ass')

                        subs_tracks_ids_no_srt = [x for x in subs_track_ids if x not in srt_track_ids]
                        subs_tracks_ids_no_ass = [x for x in subs_tracks_ids_no_srt if x not in ass_track_ids]
                        subs_track_ids = subs_tracks_ids_no_ass

    # Sets the default subtitle track to first entry in preferences,
    # reverts to any entry if not first
    for track_id, lang in zip(subs_track_ids, subs_track_languages):
        if lang == pref_subs_langs[0]:
            default_subs_track = track_id
            break
        elif lang in pref_subs_langs:
            default_subs_track = track_id
            break

    # Remove language duplicates
    seen = set()
    result = []
    for item in subs_track_languages:
        if item not in seen:
            seen.add(item)
            result.append(item)
    subs_track_languages = result

    subs_track_ids.sort()
    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True

    return subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert, \
        sub_filetypes, subs_track_languages, needs_processing


def extract_subs_in_mkv(filename, track_numbers, output_filetypes, subs_languages):
    subtitle_files = []
    base, _, extension = filename.rpartition('.')

    for index, track in enumerate(track_numbers):
        subtitle_filename = f"{base}.{track}.{subs_languages[index][:-1]}.{output_filetypes[index]}"
        command = ["mkvextract", filename, "tracks",
                   f"{track}:{subtitle_filename}"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing mkvextract command: " + result.stdout)
        subtitle_files.append(subtitle_filename)

    return subtitle_files


def extract_audio_tracks_in_mkv(filename, track_numbers, audio_languages):
    if not track_numbers:
        print(f"[UTC {get_timestamp()}] [MKVEXTRACT] Error: No track numbers passed.")
        return
    audio_files = []
    audio_extensions = []
    track_ids = []
    base, _, _ = filename.rpartition('.')

    for index, track in enumerate(track_numbers):
        audio_filename = f"{base}.{track}.{audio_languages[index][:-1]}.mkv"
        command = ["mkvextract", filename, "tracks", f"{track}:{audio_filename}"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing mkvextract command: " + result.stdout)
        audio_files.append(audio_filename)
        audio_extensions.append('mkv')

    return audio_files, audio_languages, audio_extensions


def encode_audio_tracks(audio_files, languages, output_codec, other_files, other_langs, keep_original_audio):
    if not audio_files:
        return
    if len(audio_files) > 1:
        track_str = "tracks"
    else:
        track_str = "track"
    print(f"[UTC {get_timestamp()}] [FFMPEG] Generating {output_codec.upper()} audio {track_str}...")

    output_audio_files_extensions = []
    output_audio_langs = []
    other_output_audio_files_extensions = []
    other_output_audio_langs = []
    custom_ffmpeg_options = []
    all_track_ids = []

    # If the output codec is AAC audio, increase the
    # bitrate (audio quality 6) and limit audio to stereo (two channels)
    if output_codec.lower() == 'aac':
        custom_ffmpeg_options = ['-aq', '6', '-ac', '2']

    for index, file in enumerate(audio_files):
        base_and_lang_with_id, _, extension = file.rpartition('.')
        base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
        base, _, track_id = base_with_id.rpartition('.')

        command = ["ffmpeg", "-i", file] + custom_ffmpeg_options + ["-strict", "-2",
                                                                    f"{base}.{track_id}.{lang}.{output_codec.lower()}"]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing ffmpeg command: " + result.stderr)

        output_audio_files_extensions.append(f"{output_codec.lower()}")
        output_audio_langs.append(f"{languages[index]}")
        all_track_ids.append(track_id)

        if keep_original_audio:
            # Adding the original files to the output as well
            output_audio_files_extensions.append(extension)
            output_audio_langs.append(languages[index])
            all_track_ids.append(track_id)
        else:
            os.remove(file)

    # Adding the other files to return list
    for index, file in enumerate(other_files):
        base_and_lang, _, extension = file.rpartition('.')
        other_output_audio_files_extensions.append(extension)
        other_output_audio_langs.append(other_langs[index])

    output_audio_files_extensions = output_audio_files_extensions + other_output_audio_files_extensions
    output_audio_langs = output_audio_langs + other_output_audio_langs

    return output_audio_files_extensions, output_audio_langs, all_track_ids
