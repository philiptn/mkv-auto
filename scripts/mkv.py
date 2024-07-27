import subprocess
import json
import os
import re
from tqdm import tqdm
from datetime import datetime
import shutil
import time
import pycountry
from scripts.misc import *


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
    codec = None
    parsed_json, _ = get_mkv_info(False, filename, True)
    if parsed_json:
        for track in parsed_json['tracks']:
            if track['type'] == 'video':
                codec = track['codec']
    return codec


def has_closed_captions(file_path):
    # Command to get ffprobe output
    command = ['ffprobe', file_path]

    # Execute the command and capture the output
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode()

    # Search for "Closed Captions" in the video stream description
    if "Stream #0:0" in output and "Video:" in output and "Closed Captions" in output:
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
                         audio_track_names, sub_track_ids, sub_track_names, always_enable_subs, pref_subs_ext):
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

    # Reorder audio filetypes to priority list
    if final_audio_filetypes:
        filetype_priority = final_audio_filetypes[0]
        def get_priority(filetype):
            try:
                return filetype_priority.index(filetype)
            except ValueError:
                return len(filetype_priority)  # Default priority for unknown file types

        paired = zip(audio_languages, audio_filetypes, audio_track_ids, audio_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[1]))
        sorted_audio_languages, sorted_audio_filetypes, sorted_audio_track_ids, sorted_audio_track_names = zip(
            *sorted_paired)

        final_audio_languages = list(sorted_audio_languages)
        final_audio_filetypes = list(sorted_audio_filetypes)
        final_audio_track_ids = list(sorted_audio_track_ids)
        final_audio_track_names = list(sorted_audio_track_names)

    # If the first preferred language is found in the sub languages,
    # reorder the list to place the preferred language first
    if sub_languages:
        def get_priority(lang):
            try:
                return pref_subs_langs.index(lang)
            except ValueError:
                return len(pref_subs_langs)

        paired = zip(sub_languages, sub_filetypes, sub_track_ids, sub_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names = zip(*sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)

    # Reorder sub filetypes to priority list
    filetype_priority = pref_subs_ext
    if sub_filetypes:
        def get_priority(filetype):
            try:
                return filetype_priority.index(filetype)
            except ValueError:
                return len(filetype_priority)  # Default priority for unknown file types

        paired = zip(final_sub_languages, final_sub_filetypes, final_sub_track_ids, final_sub_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[1]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names = zip(*sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} repack_tracks_in_mkv:\n")
        print(f"{BLUE}preferred audio languages{RESET}: {pref_audio_langs}")
        print(f"{BLUE}preferred subtitle languages{RESET}: {pref_subs_langs}")
        print(f"{BLUE}preferred subtitle extensions{RESET}: {pref_subs_ext}\n")
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
    for index, filetype in enumerate(final_sub_filetypes):
        default_track_str = "0:no"
        # mkvmerge does not support the .sub file as input,
        # and requires the .idx specified instead
        if filetype == "sub":
            filetype = "idx"
        if not default_locked:
            if always_enable_subs:
                default_track_str = "0:yes"
            default_locked = True
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
