import subprocess
import json
import os
import re
from tqdm import tqdm
from datetime import datetime
import shutil
import time
import concurrent.futures

# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'

# Calculate max_workers as 80% of the available logical cores
max_workers = int(os.cpu_count() * 0.8)


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def convert_video_to_mkv(debug, video_file, output_file):
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


def convert_all_videos_to_mkv(debug, input_folder, silent):
    video_files = []
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.endswith(('.mp4', '.avi', '.m4v', '.webm', '.ts')):
                video_files.append(os.path.join(root, file))

    total_files = len(video_files)
    if total_files == 0:
        return

    pbar = tqdm(total=total_files, bar_format='\r{desc}{bar:8} {percentage:3.0f}%', leave=False, disable=silent)
    for i, video_file in enumerate(video_files, start=1):
        pbar.set_description(f'{GREY}[INFO]{RESET} Converting file {i} of {total_files} to MKV')
        if video_file.endswith('.mp4'):
            # If the function returns "True", then there are
            # tx3g subtitles in the mp4 file that needs to be converted.
            if convert_mp4_to_mkv_with_subtitles(debug, video_file):
                pass
            else:
                output_file = os.path.splitext(video_file)[0] + '.mkv'
                convert_video_to_mkv(debug, video_file, output_file)
        else:
            output_file = os.path.splitext(video_file)[0] + '.mkv'
            convert_video_to_mkv(debug, video_file, output_file)
        pbar.update(1)  # Update progress bar by one file
    pbar.close()


def format_tracks_as_blocks(json_data, line_width=80):
    formatted_blocks = []
    for track in json_data.get('tracks', []):  # Safely access 'tracks'
        line = ""
        block = []
        for key, value in track.items():
            # Handling None values to be printed as 'null'
            value_repr = 'null' if value is None else f"'{value}'" if isinstance(value, str) else str(value)
            entry = f"{key}: {value_repr}, "
            if len(line + entry) > line_width:
                block.append(line.rstrip())
                line = ""
            line += entry
        block.append(line.rstrip())  # Add remaining data to the block
        formatted_blocks.append('\n'.join(block))

    return '\n\n'.join(formatted_blocks)


# Function to simplify the JSON structure
def simplify_json(data, fields_to_keep):
    simplified = {key: data[key] for key in fields_to_keep if key in data}
    simplified['tracks'] = [
        {
            'id': track.get('id'),
            'type': track.get('type'),
            'codec_name': track.get('codec'),
            'language': track.get('properties', {}).get('language'),
            'track_name': track.get('properties', {}).get('track_name'),
            'default_track': track.get('properties', {}).get('default_track'),
            'forced_track': track.get('properties', {}).get('forced_track', False),
            'codec_id': track.get('properties', {}).get('codec_id')
        } for track in data.get('tracks', [])
    ]
    return simplified


def get_mkv_info(debug, filename, silent):
    command = ["mkvmerge", "-J", filename]
    done = False
    result = None
    printed = False
    while not done:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            if not printed and not silent:
                print(
                    f"{GREY}[UTC {get_timestamp()}] [INFO]{RESET} Incoming file(s) detected in input folder. Waiting...")
                printed = True
            time.sleep(5)
        if result.returncode == 0:
            done = True

    # Parse the JSON output and pretty-print it
    parsed_json = json.loads(result.stdout)
    pretty_json = json.dumps(parsed_json, indent=2)

    # Simplifying the JSON
    fields_to_keep = ['file_name', 'tracks']
    simplified_json = simplify_json(parsed_json, fields_to_keep)
    compact_json = format_tracks_as_blocks(simplified_json, 70)

    # Function to colorize text
    def colorize(text):
        colored_text = ""
        for line in text.split('\n'):
            for part in line.split(', '):
                if ':' in part:
                    key, value = part.split(':', 1)
                    colored_text += f"{BLUE}{key}{RESET}: {value.strip()}, "
            colored_text = colored_text.rstrip(', ') + '\n'
        return colored_text

    colored_text = colorize(compact_json)

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} MKV file structure:\n")
        print(colored_text)
    return parsed_json, pretty_json


def get_mkv_video_codec(filename):
    parsed_json, _ = get_mkv_info(False, filename, True)
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
    parsed_json, _ = get_mkv_info(filename, True)
    for track in parsed_json['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def get_all_subtitle_languages(filename):
    all_langs = []
    parsed_json, _ = get_mkv_info(filename, True)
    for track in parsed_json['tracks']:
        if track['type'] == 'subtitles':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def remove_all_mkv_track_tags(debug, filename):
    command = ['mkvpropedit', filename,
               '--edit', 'track:v1', '--set', 'name=',
               '--set', 'flag-default=1', '-e', 'info', '-s', 'title=']

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} Removing track tags in mkv...")
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvpropedit command: " + result.stderr)


def convert_mp4_to_mkv_with_subtitles(debug, mp4_file):
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

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(mkvmerge_cmd)}")
        print(f"{RESET}")

    try:
        subprocess.run(mkvmerge_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        print(f"Error occurred while merging files into {mkv_file}")
        return None

    for srt_file, _ in srt_files:
        os.remove(srt_file)
    os.remove(mp4_file)

    return True


def remove_cc_hidden_in_file(debug, filename):
    print(f"{GREY}[UTC {get_timestamp()}] [FFMPEG]{RESET} Removing Closed Captions (CC) from video stream...")
    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ['ffmpeg', '-i', filename, '-codec', 'copy', '-map', '0',
               '-map', '-v', '-map', 'V', '-bsf:v', 'filter_units=remove_types=6', temp_filename]

    # Remove empty entries
    command = [arg for arg in command if arg]

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing ffmpeg command: " + result.stderr)
        print(f"{GREY}[UTC {get_timestamp()}] [INFO]{RESET} Skipping ffmpeg process...")
        try:
            os.remove(temp_filename)
        except:
            pass
    else:
        os.remove(filename)
        shutil.move(temp_filename, filename)


def strip_tracks_in_mkv(debug, filename, audio_tracks, default_audio_track,
                        sub_tracks, default_subs_track, always_enable_subs):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} strip_tracks_in_mkv:\n")
        print(f"{BLUE}always enable subs{RESET}: {always_enable_subs}")
        print(f"{BLUE}audio tracks to keep{RESET}: {audio_tracks}")
        print(f"{BLUE}subtitle tracks to keep{RESET}: {sub_tracks}")
        print(f"{BLUE}default audio track{RESET}: {default_audio_track}")
        print(f"{BLUE}default subtitle track{RESET}: {default_subs_track}\n")

    print(f"{GREY}[UTC {get_timestamp()}] [MKVMERGE]{RESET} Filtering audio and subtitle tracks...")

    subtitle_tracks = ''
    subs_default_track = ''
    default_subs_track_str = ''

    # If no audio tracks has been selected, copy all as fallback,
    # else, generate copy string
    if len(audio_tracks) == 0:
        audio = ''
        audio_tracks_str = ''
        audio_default_track = ''
        default_audio_track_str = ''
    else:
        audio = '--atracks'
        audio_tracks_str = ','.join(map(str, audio_tracks))
        audio_default_track = "--default-track"
        default_audio_track_str = f'{default_audio_track}:yes'

    if always_enable_subs and len(sub_tracks) != 0:
        subs_default_track = "--default-track"
        default_subs_track_str = f'{default_subs_track}:yes'

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
               audio_default_track, default_audio_track_str] + [
                  subs, subtitle_tracks,
                  subs_default_track, default_subs_track_str] + [
                  filename]
    # Remove empty entries
    command = [arg for arg in command if arg]

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing mkvmerge command: " + result.stdout)
        print("Continuing...")

    os.remove(filename)
    shutil.move(temp_filename, filename)


def repack_tracks_in_mkv(debug, filename, sub_filetypes, sub_languages, pref_subs_langs,
                         audio_filetypes, audio_languages, pref_audio_langs, audio_track_ids,
                         audio_track_names, sub_track_ids, sub_track_names):
    sub_files_list = []
    final_sub_languages = sub_languages
    audio_files_list = []
    final_audio_languages = audio_languages
    final_audio_filetypes = []
    final_sub_filetypes = []
    final_audio_track_ids = audio_track_ids
    final_audio_track_names = audio_track_names
    final_sub_track_ids = sub_track_ids
    final_sub_track_names = sub_track_names

    # If the first preferred language is found in the audio languages,
    # reorder the list to place the preferred language first
    if audio_languages:
        # Function to get the priority of each language
        def get_priority(lang):
            try:
                return pref_audio_langs.index(lang)
            except ValueError:
                return len(pref_audio_langs)

        paired = zip(audio_languages, audio_filetypes, audio_track_ids, audio_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_audio_languages, sorted_audio_filetypes, sorted_audio_track_ids, sorted_audio_track_names = zip(
            *sorted_paired)

        # Convert tuples back to lists if necessary
        final_audio_languages = list(sorted_audio_languages)
        final_audio_filetypes = list(sorted_audio_filetypes)
        final_audio_track_ids = list(sorted_audio_track_ids)
        final_audio_track_names = list(sorted_audio_track_names)

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
        paired = zip(sub_languages, sub_filetypes, sub_track_ids, sub_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names = zip(*sorted_paired)

        # Convert tuples back to lists if necessary
        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} repack_tracks_in_mkv:\n")
        print(f"{BLUE}preferred audio languages{RESET}: {pref_audio_langs}")
        print(f"{BLUE}preferred subtitle languages{RESET}: {pref_subs_langs}\n")
        print(f"{BLUE}audio tracks to be added{RESET}:"
              f"\n  {BLUE}filetypes{RESET}: {final_audio_filetypes}"
              f"\n  {BLUE}langs{RESET}: {final_audio_languages}"
              f"\n  {BLUE}ids{RESET}: {final_audio_track_ids}"
              f"\n  {BLUE}names{RESET}: {final_audio_track_names}")
        print(f"{BLUE}subtitle tracks to be added{RESET}:"
              f"\n  {BLUE}filetypes{RESET}: {final_sub_filetypes}"
              f"\n  {BLUE}langs{RESET}: {final_sub_languages}"
              f"\n  {BLUE}ids{RESET}: {final_sub_track_ids}"
              f"\n  {BLUE}names{RESET}: {final_sub_track_names}\n")

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    default_locked = False

    for index, filetype in enumerate(final_audio_filetypes):
        if not default_locked:
            if final_audio_languages[index] == pref_audio_langs[first_pref_audio_index]:
                default_track_str = "0:yes"
                default_locked = True
            else:
                default_track_str = "0:no"
        else:
            default_track_str = "0:no"
        lang_str = f"0:{final_audio_languages[index]}"
        name_str = f"0:{final_audio_track_names[index]}"
        filelist_str = f"{base}.{final_audio_track_ids[index]}.{final_audio_languages[index][:-1]}.{filetype}"
        audio_files_list += ('--default-track', default_track_str, '--language', lang_str,
                             '--track-name', name_str, filelist_str)

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
        lang_str = f"0:{final_sub_languages[index]}"
        name_str = f"0:{final_sub_track_names[index]}"
        filelist_str = f"{base}.{final_sub_track_ids[index]}.{final_sub_languages[index][:-1]}.{filetype}"
        sub_files_list += ('--default-track', default_track_str,
                           '--language', lang_str,
                           '--track-name', name_str, filelist_str)

    print(f"{GREY}[UTC {get_timestamp()}] [MKVMERGE]{RESET} Repacking tracks into mkv...")
    if audio_filetypes:
        command = ["mkvmerge", "--no-subtitles", "--no-audio", "--output",
                   temp_filename, filename] + audio_files_list + sub_files_list
    else:
        command = ["mkvmerge", "--no-subtitles", "--output", temp_filename, filename] + sub_files_list

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

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
                final_sub_track_names.append(final_sub_track_names[index])

        for index, filetype in enumerate(final_sub_filetypes):
            os.remove(f"{base}.{final_sub_track_ids[index]}.{final_sub_languages[index][:-1]}.{filetype}")


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
                    if ((not remove_commentary and commentary_tracks_found)
                            or (pref_audio_track_languages.count(track_language) == 0 and audio_track_languages.count(track_language) == 0))\
                            or ("Original" in all_track_names[total_audio_tracks - 1]):
                        pref_audio_track_ids.append(track["id"])
                        pref_audio_track_languages.append(track_language)
                        pref_audio_track_names.append(track_name)
                        audio_track_codecs.append(audio_codec.upper())

                        if not default_audio_track_set:
                            pref_default_audio_track = track["id"]
                            default_audio_track_set = True

                        if remove_commentary and "commentary" in track_name.lower() \
                                and track_language in audio_track_languages:
                            pref_audio_track_ids.remove(track["id"])
                            pref_audio_track_languages.remove(track_language)
                            pref_audio_track_names.remove(track_name)
                            audio_track_codecs.remove(audio_codec.upper())
                            default_audio_track_set = False

                elif preferred_audio_codec not in audio_codec.upper():
                    if ((not remove_commentary and commentary_tracks_found)
                            or (audio_track_languages.count(track_language) == 0 and pref_audio_track_languages.count(track_language) == 0))\
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
                        if remove_commentary and "commentary" in track_name.lower() \
                                and track_language in audio_track_languages:
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

    if len(all_audio_track_ids) != 0 and len(all_audio_track_ids) < total_audio_tracks:
        needs_processing = True

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
        all_audio_track_ids = unmatched_audio_track_ids
        default_audio_track = unmatched_audio_track_ids[0]

        # If the language "und" (undefined) is in the unmatched languages,
        # assign it to be an english audio track. Else, keep the originals.
        if "und" in unmatched_audio_track_languages[0].lower():
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
            if (pref_audio_codec.lower() not in unmatched_audio_track_codecs[0].lower()) \
                    and pref_audio_codec.lower() != 'false':
                tracks_langs_to_be_converted = unmatched_audio_track_languages
                tracks_ids_to_be_converted = unmatched_audio_track_ids
                tracks_names_to_be_converted = unmatched_audio_track_names
            else:
                other_tracks_langs = unmatched_audio_track_languages
                other_tracks_ids = unmatched_audio_track_ids
                other_tracks_names = unmatched_audio_track_names

    # If the first audio track in the media is not matched, add it,
    # but place it last in the list
    if first_audio_track_id not in all_audio_track_ids:
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

    if debug:
        print(f"{BLUE}preferred audio codec found{RESET}: {pref_audio_codec_found}")
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


def get_wanted_subtitle_tracks(debug, file_info, pref_langs):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_subtitle_tracks:\n")
        print(f"{BLUE}preferred subtitle languages{RESET}: {pref_langs}")

    total_subs_tracks = 0
    pref_subs_langs = pref_langs

    subs_track_ids = []
    subs_track_languages = []
    subs_track_names = []

    unmatched_subs_track_languages = []
    unmatched_subs_track_names = []

    forced_track_ids = []
    forced_track_languages = []
    forced_track_names = []
    forced_sub_filetypes = []

    default_subs_track = ''
    all_sub_filetypes = []
    sub_filetypes = []
    srt_track_ids = []
    ass_track_ids = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False
    srt_ass_track_removed = []

    # Get all subtitle codecs
    for track in file_info['tracks']:
        if track['type'] == 'subtitles':
            all_sub_filetypes.append(track['codec'])

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

                if forced_track:
                    forced_track_ids.append(track["id"])
                    forced_track_languages.append(track_language)
                    forced_track_names.append(track_name)
                    if track["codec"] == "HDMV PGS":
                        forced_sub_filetypes.append('sup')
                    elif track["codec"] == "VobSub":
                        forced_sub_filetypes.append('sub')
                    elif track["codec"] == "SubRip/SRT":
                        forced_sub_filetypes.append('srt')
                    elif track["codec"] == "SubStationAlpha":
                        forced_sub_filetypes.append('ass')

                # If the track language is "und" (undefined), assume english subtitles
                if track_language.lower() == "und":
                    track_language = 'eng'
                    pref_subs_langs.append('eng')

                if subs_track_languages.count(track_language) == 0 and not forced_track:
                    if track["codec"] == "HDMV PGS":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('sup')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "VobSub":
                        # If VobSub is the only subtitle type in the file (DVD), keep it.
                        # If it is a mix of Vobsub and PGS (BluRay), only the PGS should be kept.
                        if all(codec == "VobSub" for codec in all_sub_filetypes):
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            sub_filetypes.append('sub')
                            needs_convert = True
                            needs_processing = True
                    elif track["codec"] == "SubRip/SRT":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if ((track["codec"] != "SubRip/SRT" and track["codec"] != "SubStationAlpha")
                            and subs_track_languages.count(track_language) == 1 and
                            track_language not in srt_ass_track_removed):

                        if 'srt' in sub_filetypes:
                            sub_filetypes.remove('srt')
                            subs_track_languages.remove(track_language)
                            subs_track_names.pop()
                            srt_ass_track_removed.append(track_language)

                        if 'ass' in sub_filetypes:
                            sub_filetypes.remove('ass')
                            subs_track_languages.remove(track_language)
                            subs_track_names.pop()
                            srt_ass_track_removed.append(track_language)

                        if track["codec"] == "HDMV PGS":
                            sub_filetypes.append('sup')
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True
                        elif track["codec"] == "VobSub":
                            sub_filetypes.append('sub')
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

                        subs_tracks_ids_no_srt = [x for x in subs_track_ids if x not in srt_track_ids]
                        subs_tracks_ids_no_ass = [x for x in subs_tracks_ids_no_srt if x not in ass_track_ids]
                        subs_track_ids = subs_tracks_ids_no_ass

    # If none of the subtitles matched, add the forced tracks as a last effort
    if len(subs_track_ids) == 0:
        subs_track_ids = forced_track_ids
        subs_track_languages = forced_track_languages
        subs_track_names = forced_track_names
        sub_filetypes = forced_sub_filetypes

    # Sets the default subtitle track to first entry in preferences,
    # reverts to any entry if not first
    for track_id, lang in zip(subs_track_ids, subs_track_languages):
        if lang == pref_subs_langs[0]:
            default_subs_track = track_id
            break
        elif lang in pref_subs_langs:
            default_subs_track = track_id
            break

    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True

    if debug:
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"{BLUE}needs SDH removal{RESET}: {needs_sdh_removal}")
        print(f"{BLUE}needs to be converted{RESET}: {needs_convert}")
        print(f"\n{BLUE}all wanted subtitle track ids{RESET}: {subs_track_ids}")
        print(f"{BLUE}default subtitle track id{RESET}: {default_subs_track}")
        print(f"{BLUE}subtitle tracks to be extracted{RESET}:\n  {BLUE}filetypes{RESET}: {sub_filetypes}, "
              f"{BLUE}langs{RESET}: {subs_track_languages}, {BLUE}names{RESET}: {subs_track_names}\n")

    return subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert, \
        sub_filetypes, subs_track_languages, subs_track_names, needs_processing


# Function to extract a single subtitle track
def extract_subtitle(debug, filename, track, output_filetype, language):
    base, _, _ = filename.rpartition('.')
    subtitle_filename = f"{base}.{track}.{language[:-1]}.{output_filetype}"
    command = ["mkvextract", filename, "tracks", f"{track}:{subtitle_filename}"]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvextract command: " + result.stderr)

    return subtitle_filename


def extract_subs_in_mkv(debug, filename, track_numbers, output_filetypes, subs_languages):
    print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Extracting subtitles...")
    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks and store futures in a list
        futures = [executor.submit(extract_subtitle, debug, filename, track, filetype, language)
                   for track, filetype, language in zip(track_numbers, output_filetypes, subs_languages)]

        # Wait for the futures to complete and get the results in the order they were submitted
        results = [future.result() for future in futures]

    if debug:
        print('')

    return results


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

    return audio_filename, 'mkv', name


def extract_audio_tracks_in_mkv(debug, filename, track_numbers, audio_languages, audio_names):
    if not track_numbers:
        print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Error: No track numbers passed.")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        tasks = [executor.submit(extract_audio_track, debug, filename, track, language, name)
                 for track, language, name in zip(track_numbers, audio_languages, audio_names)]
        results = [future.result() for future in concurrent.futures.as_completed(tasks)]

    audio_files, audio_extensions, audio_names = zip(*results)  # Unpack results into separate lists

    return audio_files, audio_languages, audio_names, audio_extensions


def encode_audio_track(file, index, debug, languages, track_names, output_codec, custom_ffmpeg_options):
    base_and_lang_with_id, _, extension = file.rpartition('.')
    base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
    base, _, track_id = base_with_id.rpartition('.')

    command = ["ffmpeg", "-i", file] + custom_ffmpeg_options + ["-strict", "-2",
                                                                f"{base}.{track_id}.{lang}.{output_codec.lower()}"]
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing ffmpeg command: " + result.stderr)

    output_extension = output_codec.lower()
    output_lang = languages[index]
    output_name = ''

    return output_extension, output_lang, output_name, track_id


def encode_audio_tracks(debug, audio_files, languages, track_names, output_codec,
                        other_files, other_langs, other_names, keep_original_audio, other_track_ids):
    if not audio_files:
        return

    if len(audio_files) > 1:
        track_str = 'tracks'
    else:
        track_str = 'track'

    print(f"{GREY}[UTC {get_timestamp()}] [FFMPEG]{RESET} Generating {output_codec.upper()} audio {track_str}...")

    custom_ffmpeg_options = ['-aq', '6', '-ac', '2'] if output_codec.lower() == 'aac' else []

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(encode_audio_track, file, index,
                                   debug, languages, track_names, output_codec, custom_ffmpeg_options)
                   for index, file in enumerate(audio_files)]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    if debug:
        print('')

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
