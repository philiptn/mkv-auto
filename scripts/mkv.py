import subprocess
import json
import os
import re
from tqdm import tqdm
from datetime import datetime
import shutil
import time
import pycountry
import concurrent.futures
import base64
from collections import defaultdict, Counter
from itertools import chain

from scripts.misc import *
from scripts.audio import *
from scripts.subs import *
from scripts.file_operations import *


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


def convert_all_videos_to_mkv(logger, debug, input_folder, silent):
    header = "FFMPEG"
    description = "Convert media to MKV"

    video_files = []
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(('.mp4', '.avi', '.m4v', '.webm', '.ts', '.mov')):
                video_files.append(os.path.join(root, file))

    total_files = len(video_files)
    if total_files == 0:
        return

    completed_count = 0
    print_with_progress(logger, completed_count, total_files, header=header, description=description)

    for i, video_file in enumerate(video_files, start=1):
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
        completed_count += 1
        print_with_progress(logger, completed_count, total_files, header=header, description=description)


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
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} MKV file structure:\n")
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


def check_if_subs_in_mkv(filename):
    parsed_json, _ = get_mkv_info(False, filename, True)
    if parsed_json:
        for track in parsed_json['tracks']:
            if track['type'] == 'subtitles':
                return True
        else:
            return False


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
    parsed_json, _ = get_mkv_info(False, filename, True)
    for track in parsed_json['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def get_all_subtitle_languages(filename):
    all_langs = []
    parsed_json, _ = get_mkv_info(False, filename, True)
    for track in parsed_json['tracks']:
        if track['type'] == 'subtitles':
            for key, value in track["properties"].items():
                if key == 'language':
                    all_langs.append(value)
    return all_langs


def get_main_audio_track_language(file_info):
    # Get the main audio language
    for track in file_info['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    if value == 'nob' or value == 'nno':
                        value = 'nor'
                    language = pycountry.languages.get(alpha_3=value)
                    if language:
                        main_audio_track_lang = language.name
                        return main_audio_track_lang


def remove_all_mkv_track_tags(debug, filename):
    command = ['mkvpropedit', filename,
               '--edit', 'track:v1', '--set', 'name=',
               '--set', 'flag-default=1', '-e', 'info', '-s', 'title=']

    if debug:
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} Removing track tags in mkv...")
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {RED}[ERROR]{RESET} {result.stdout}")
        print(f"{RESET}")
    result.check_returncode()


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


def mkv_contains_video(file_path, dirpath):
    input_file = os.path.join(dirpath, file_path)
    try:
        # Run ffprobe command to get stream information
        command = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=index', '-of', 'json', input_file
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Parse the output
        probe_data = json.loads(result.stdout)

        # Check if the 'streams' key exists and contains at least one video stream
        if 'streams' in probe_data and len(probe_data['streams']) > 0:
            return True
        else:
            return False

    except Exception as e:
        print(f"An error occurred: {e}")
        return False


def remove_cc_hidden_in_file(debug, filename):
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


def trim_audio_and_subtitles_in_mkv_files(logger, debug, input_files, dirpath):
    total_files = len(input_files)
    mkv_files_need_processing_audio = [None] * total_files
    mkv_files_need_processing_subs = [None] * total_files
    all_missing_subs_langs = [None] * total_files
    max_worker_threads = get_worker_thread_count()

    header = "MKVMERGE"
    description = "Filter audio and subtitle tracks"

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_worker_threads) as executor:
        futures = {executor.submit(trim_audio_and_subtitles_in_mkv_files_worker, debug, input_file, dirpath): index for
                   index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                needs_processing_audio, needs_processing_subs, missing_subs_langs = future.result()
                if needs_processing_audio is not None:
                    mkv_files_need_processing_audio[index] = needs_processing_audio
                if needs_processing_subs is not None:
                    mkv_files_need_processing_subs[index] = needs_processing_subs
                if missing_subs_langs is not None:
                    all_missing_subs_langs[index] = missing_subs_langs
            except Exception as e:
                for file in input_files:
                    base, extension = os.path.splitext(file)
                    new_base = base + "_tmp"
                    temp_filename = new_base + extension
                    if os.path.exists(temp_filename):
                        os.remove(temp_filename)
                raise CorruptedFile

    return mkv_files_need_processing_audio, mkv_files_need_processing_subs, all_missing_subs_langs


def trim_audio_and_subtitles_in_mkv_files_worker(debug, input_file, dirpath):
    input_file = os.path.join(dirpath, input_file)
    check_integrity_of_mkv(input_file)

    # Get file info using mkvinfo
    file_info, pretty_file_info = get_mkv_info(debug, input_file, False)

    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    remove_commentary = check_config(config, 'audio', 'remove_commentary')
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')
    always_enable_subs = check_config(config, 'subtitles', 'always_enable_subs')
    download_missing_subs = check_config(config, 'subtitles', 'download_missing_subs')

    (wanted_audio_tracks, default_audio_track, needs_processing_audio,
     pref_audio_formats_found, track_ids_to_be_converted,
     track_langs_to_be_converted, track_names_to_be_converted) = get_wanted_audio_tracks(
        debug, file_info, pref_audio_langs, remove_commentary, pref_audio_formats)

    (wanted_subs_tracks, default_subs_track,
     needs_sdh_removal, needs_convert, sub_filetypes,
     subs_track_languages, subs_track_names, needs_processing_subs,
     a, missing_subs_langs) = get_wanted_subtitle_tracks(
        debug, file_info, pref_subs_langs)

    if needs_processing_audio:
        strip_tracks_in_mkv(debug, input_file, wanted_audio_tracks, default_audio_track,
                            wanted_subs_tracks, default_subs_track, always_enable_subs)

    if download_missing_subs.lower() == 'override':
        needs_processing_subs = True
        if pref_subs_langs != ['']:
            missing_subs_langs = pref_subs_langs
        else:
            main_lang = get_main_audio_track_language_3_letter(file_info)
            missing_subs_langs = [main_lang]

    return needs_processing_audio, needs_processing_subs, missing_subs_langs


def generate_audio_tracks_in_mkv_files(logger, debug, input_files, dirpath, need_processing_audio):
    total_files = len(input_files)
    all_ready_audio_tracks = [None] * total_files
    all_ready_subtitle_tracks = [None] * total_files
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    audio_format_preferences = parse_preferred_codecs(pref_audio_formats)
    audio_format_preferences_print = format_audio_preferences_print(audio_format_preferences)

    all_pref_settings_codecs = []
    audio_preferences = parse_preferred_codecs(pref_audio_formats)
    for transformation, codec, ch_str in audio_preferences:
        all_pref_settings_codecs.append(codec)
    disable_print = True if len(all_pref_settings_codecs) == 1 and ("COPY" or "") in all_pref_settings_codecs else False

    if all(not bool for bool in need_processing_audio):
        disable_print = True

    # Calculate number of workers and internal threads
    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    header = "FFMPEG"
    description = f"Process audio {print_multi_or_single(len(audio_format_preferences), 'format')}"

    if not disable_print:
        print()
        custom_print(logger,
                     f"{GREY}[AUDIO]{RESET} Requested {print_multi_or_single(len(audio_format_preferences_print), 'format')}:")
        for index, pref in enumerate(audio_format_preferences_print):
            if index + 1 == len(audio_format_preferences_print):
                custom_print_no_newline(logger, f"{GREY}[AUDIO]{RESET} {pref}")
            else:
                custom_print(logger, f"{GREY}[AUDIO]{RESET} {pref}")

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(generate_audio_tracks_in_mkv_files_worker, debug, input_file, dirpath,
                                   internal_threads): index for index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                ready_audio_tracks, ready_subtitle_tracks = future.result()
                if ready_audio_tracks is not None:
                    all_ready_audio_tracks[index] = ready_audio_tracks
                if ready_subtitle_tracks is not None:
                    all_ready_subtitle_tracks[index] = ready_subtitle_tracks
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise
    return all_ready_audio_tracks, all_ready_subtitle_tracks


def generate_audio_tracks_in_mkv_files_worker(debug, input_file, dirpath, internal_threads):
    input_file = os.path.join(dirpath, input_file)

    ready_audio_extensions = []
    ready_audio_langs = []
    ready_track_ids = []
    ready_track_names = []

    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    remove_commentary = check_config(config, 'audio', 'remove_commentary')

    # Get updated file info after mkv tracks reduction
    file_info, pretty_file_info = get_mkv_info(False, input_file, True)

    (wanted_audio_tracks, default_audio_track, needs_processing_audio,
     pref_audio_formats_found, track_ids_to_be_converted,
     track_langs_to_be_converted, track_names_to_be_converted) = get_wanted_audio_tracks(
        False, file_info, pref_audio_langs, remove_commentary, pref_audio_formats)

    # Generating audio tracks if preferred codec not found in all audio tracks
    if needs_processing_audio:
        if debug:
            print('')

        (extracted_for_convert_audio_files,
         extracted_for_convert_audio_langs,
         extracted_for_convert_audio_names,
         extracted_audio_extensions) = extract_audio_tracks_in_mkv(internal_threads, debug, input_file,
                                                                   track_ids_to_be_converted,
                                                                   track_langs_to_be_converted,
                                                                   track_names_to_be_converted)

        (ready_audio_extensions, ready_audio_langs,
         ready_track_names, ready_track_ids) = encode_audio_tracks(
            internal_threads, debug, extracted_for_convert_audio_files, extracted_for_convert_audio_langs,
            extracted_for_convert_audio_names, pref_audio_formats)

    # Dummy subtitle metadata needs to be returned for
    # rest of the pipeline to function properly
    return {
        'audio_extensions': ready_audio_extensions,
        'audio_langs': ready_audio_langs,
        'audio_ids': ready_track_ids,
        'audio_names': ready_track_names
    }, {
        'sub_extensions': None,
        'sub_langs': None,
        'sub_ids': None,
        'sub_names': None,
        'sub_forced': None
    }


def extract_subs_in_mkv_process(logger, debug, input_files, dirpath):
    total_files = len(input_files)
    all_subtitle_files = [None] * total_files

    header = "MKVEXTRACT"
    description = "Extract internal subtitles"

    # Disable tqdm if there are no subtitle tracks to extract
    disable_print = True if all(
        check_if_subs_in_mkv(os.path.join(dirpath, file)) == False for file in input_files) else False

    # Calculate number of workers and internal threads
    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(extract_subs_in_mkv_process_worker, debug, input_file, dirpath, internal_threads): index for
            index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                subtitle_files = future.result()
                if subtitle_files is not None:
                    all_subtitle_files[index] = subtitle_files
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise
    return all_subtitle_files


def extract_subs_in_mkv_process_worker(debug, input_file, dirpath, internal_threads):
    input_file_with_path = os.path.join(dirpath, input_file)
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')

    # Get updated file info after mkv tracks reduction
    file_info, pretty_file_info = get_mkv_info(debug, input_file_with_path, True)

    (wanted_subs_tracks, a, b, needs_convert,
     sub_filetypes, subs_track_languages,
     subs_track_names, e, subs_track_forced, f) = get_wanted_subtitle_tracks(debug, file_info, pref_subs_langs)

    subtitle_files = extract_subs_in_mkv(internal_threads, debug, input_file_with_path, wanted_subs_tracks,
                                         sub_filetypes, subs_track_languages, subs_track_forced, subs_track_names)

    return subtitle_files


def convert_to_srt_process(logger, debug, input_files, dirpath, subtitle_files_list):
    sub_files = [
        [f for f in sublist if isinstance(f, str) and f.endswith(('.mkv', '.srt', '.sup', '.ass', '.sub'))]
        for sublist in subtitle_files_list
    ]
    total_files = len(sub_files)

    all_ready_subtitle_tracks = [None] * total_files
    subtitle_tracks_to_be_processed = [None] * total_files
    all_replacements_list = [None] * total_files
    all_errored_subs = [None] * total_files
    all_missing_subs_langs = [None] * total_files
    main_audio_track_langs_list = [None] * total_files

    disable_print = False

    # Disable print if all the subtitles to be processed are SRT (therefore no OCR is needed)
    for subs in sub_files:
        if subs:
            if all(sub.endswith('.srt') for sub in subs):
                disable_print = True
            else:
                disable_print = False
                break
        else:
            disable_print = True

    # Calculate number of workers and internal threads, floor divide by 1.7 as
    # the OCR process uses multiple Tesseract processes internally.
    # Reduced threads to not overwhelm the system.
    max_worker_threads = get_max_ocr_threads()
    num_workers = max(1, max_worker_threads)  # Ensure num_workers is at least 1.
    internal_threads = max(1, max_worker_threads // num_workers)

    header = "SUBTITLES"
    description = "Convert subtitles to SRT"

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(convert_to_srt_process_worker, debug, input_file, dirpath, internal_threads,
                                   sub_files[index]): index for index, input_file in enumerate(input_files)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                ready_tracks, output_subtitles, all_replacements, errored_subs, missing_subs_langs, main_audio_track_langs = future.result()
                if ready_tracks is not None:
                    all_ready_subtitle_tracks[index] = ready_tracks
                if output_subtitles is not None:
                    subtitle_tracks_to_be_processed[index] = output_subtitles
                if all_replacements is not None:
                    all_replacements_list[index] = all_replacements
                if errored_subs is not None:
                    all_errored_subs[index] = errored_subs
                if missing_subs_langs is not None:
                    all_missing_subs_langs[index] = missing_subs_langs
                if main_audio_track_langs is not None:
                    main_audio_track_langs_list[index] = main_audio_track_langs

            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]
                subtitle_files = sub_files[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_files{RESET}: {subtitle_files}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    all_replacements_list_count = len([item for list in all_replacements_list for item in list])
    if all_replacements_list_count:
        custom_print(logger, f"{GREY}[SUBTITLES]{RESET} Fixed "
                             f"{all_replacements_list_count} OCR {print_multi_or_single(all_replacements_list_count, 'error')}.")

    if all_replacements_list_count:
        log_debug(logger, '')
        log_debug(logger, f"{GREY}[DEBUG]{RESET} During OCR, the following words were fixed:")

        flattened_replacements = list(chain.from_iterable(all_replacements_list))
        replacements_counter = Counter(flattened_replacements)
        for replacement, count in replacements_counter.items():
            if count > 1:
                log_debug(logger, f"{replacement} {GREY}({count} times){RESET}")
            else:
                log_debug(logger, replacement)
        log_debug(logger, '')

    all_errored_subs_count = len([item for list in all_errored_subs for item in list])
    if all_errored_subs_count:
        custom_print(logger, f"{GREY}[SUBTITLES]{RESET} {all_errored_subs_count} "
                             f"{print_multi_or_single(all_errored_subs_count, 'subtitle')} failed to be converted:")
        errored_subs_print = []
        for errored_sub in all_errored_subs:
            if errored_sub:
                errored_subs_print.append(errored_sub[0])
        for sub in sorted(errored_subs_print):
            custom_print(logger, f"{RED}[SUBTITLES]{RESET} {sub}")

    return (all_ready_subtitle_tracks, subtitle_tracks_to_be_processed,
            all_missing_subs_langs, all_errored_subs, main_audio_track_langs_list)


def convert_to_srt_process_worker(debug, input_file, dirpath, internal_threads, subtitle_files):
    input_file_with_path = os.path.join(dirpath, input_file)
    subtitle_files_to_process = subtitle_files
    errored_ass_subs = []

    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')

    # Get updated file info after mkv tracks reduction
    file_info, pretty_file_info = get_mkv_info(False, input_file_with_path, True)
    # Get main audio track language
    main_audio_track_lang = get_main_audio_track_language(file_info)

    (wanted_subs_tracks, a, b, needs_convert,
     sub_filetypes, subs_track_languages,
     subs_track_names, e, subs_track_forced, f) = get_wanted_subtitle_tracks(False, file_info, pref_subs_langs)

    if "ass" in sub_filetypes:
        all_subtitles, errored_ass_subs, missing_subs_langs = convert_ass_to_srt(subtitle_files_to_process, main_audio_track_lang)
        subtitle_files_to_process = all_subtitles

    (output_subtitles, updated_subtitle_languages, all_subs_track_ids,
     all_subs_track_names, all_subs_track_forced, updated_sub_filetypes,
     all_replacements, errored_ocr_subs, missing_subs_langs) = ocr_subtitles(
        internal_threads, debug, subtitle_files_to_process, main_audio_track_lang)

    sub_filetypes = updated_sub_filetypes
    errored_subs = errored_ass_subs + errored_ocr_subs

    return {
        'sub_extensions': sub_filetypes,
        'sub_langs': updated_subtitle_languages,
        'sub_ids': all_subs_track_ids,
        'sub_names': all_subs_track_names,
        'sub_forced': all_subs_track_forced
    }, output_subtitles, all_replacements, errored_subs, missing_subs_langs, main_audio_track_lang


def get_subtitle_tracks_metadata_for_repack(logger, subtitle_files_list):
    all_ready_subtitle_tracks = [None] * len(subtitle_files_list)
    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(return_subtitle_metadata_worker, subtitle_files_list[index], internal_threads): index
                   for index, input_file in enumerate(subtitle_files_list)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                index = futures[future]
                ready_tracks = future.result()
                if ready_tracks is not None:
                    all_ready_subtitle_tracks[index] = ready_tracks

            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                subtitle_files = subtitle_files_list[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_files{RESET}: {subtitle_files}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    return all_ready_subtitle_tracks


def return_subtitle_metadata_worker(subtitle_files, max_threads):
    (updated_subtitle_languages, all_subs_track_ids,
     all_subs_track_names, all_subs_track_forced, updated_sub_filetypes) = get_subtitle_tracks_metadata_lists(
        subtitle_files, max_threads)

    return {
        'sub_extensions': updated_sub_filetypes,
        'sub_langs': updated_subtitle_languages,
        'sub_ids': all_subs_track_ids,
        'sub_names': all_subs_track_names,
        'sub_forced': all_subs_track_forced
    }


def remove_sdh_process(logger, debug, subtitle_files_to_process_list):
    total_files = len(subtitle_files_to_process_list)
    all_replacements_list = [None] * total_files

    always_remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')
    if not always_remove_sdh:
        disable_print = True
    else:
        disable_print = False

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    header = "SUBTITLES"
    description = "Remove SDH from subtitles"

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(remove_sdh_process_worker, debug, list, internal_threads): index for index, list in
                   enumerate(subtitle_files_to_process_list)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                all_replacements = future.result()
                if all_replacements is not None:
                    all_replacements_list[index] = all_replacements
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                subtitle_files = subtitle_files_to_process_list[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_files{RESET}: {subtitle_files}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise
    all_replacements_list_count = len([item for list in all_replacements_list for item in list])
    return all_replacements_list_count


def remove_sdh_process_worker(debug, input_subtitles, internal_threads):
    all_replacements = []
    remove_music = check_config(config, 'subtitles', 'remove_music')
    always_remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')
    srt_files = [f for f in input_subtitles if f.endswith('.srt')]

    if always_remove_sdh:
        a, all_replacements = remove_sdh(internal_threads, debug, srt_files, remove_music, [], False)
    return all_replacements


def fetch_missing_subtitles_process(logger, debug, input_files, dirpath, total_external_subs,
                                    all_missing_subs_langs):
    total_files = len(input_files)

    # If no sub languages are missing, and no external subs are found, skip this process
    if all(sub == ['none'] for sub in all_missing_subs_langs) and not total_external_subs:
        return

    all_truly_missing_subs_langs = []
    all_downloaded_subs = [None] * total_files
    all_failed_downloads = [None] * total_files

    header = "SUBTITLES"
    description = f"Process missing subtitles"

    for index, input_file in enumerate(input_files):
        input_file_with_path = os.path.join(dirpath, input_file)
        mkv_base, _, mkv_extension = input_file_with_path.rpartition('.')

        truly_missing_subs_langs = []
        for lang in all_missing_subs_langs[index]:
            if lang != 'none' and lang and lang.lower() != 'und':
                if any(sub for sub in total_external_subs):
                    input_file_base = re.sub(r'^[^/]+/', '', input_files[index]).replace(".mkv", "")
                    if any(input_file_base in re.sub(r'^[^/]+/', '', sub).replace(".mkv", "") for sublist in
                           total_external_subs for sub in sublist):
                        if not any(lang[:-1] in re.sub(r'^[^/]+/', '', sub).replace(".mkv", "") for sublist in
                                   total_external_subs for sub in sublist):
                            truly_missing_subs_langs.append(lang[:-1])
                else:
                    truly_missing_subs_langs.append(lang[:-1])
        all_truly_missing_subs_langs.append(truly_missing_subs_langs)

    # Copy default or user subliminal config file to dirpath
    if os.path.exists('subliminal.toml'):
        shutil.copy('subliminal.toml', os.path.join(dirpath, 'subliminal.toml'))
    else:
        shutil.copy('subliminal_defaults.toml', os.path.join(dirpath, 'subliminal.toml'))

    # Calculate number of workers and internal threads
    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    # Max workers is set to 1 to throttle downloads with Subliminal
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futures = {executor.submit(fetch_missing_subtitles_process_worker, debug, input_file, dirpath,
                                   all_truly_missing_subs_langs[index], internal_threads): index for index, input_file
                   in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                downloaded_subs, failed_downloads = future.result()
                if downloaded_subs is not None:
                    all_downloaded_subs[index] = downloaded_subs
                if failed_downloads is not None:
                    all_failed_downloads[index] = failed_downloads
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]
                subtitle_lang = all_truly_missing_subs_langs[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_langs{RESET}: {subtitle_lang}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    success_len = len((set(f"'{item}'" for sublist in all_downloaded_subs for item in sublist)))
    failed_len = len((set(f"'{item}'" for sublist in all_failed_downloads for item in sublist)))
    truly_missing_subs_count = len((set(f"'{item}'" for sublist in all_truly_missing_subs_langs for item in sublist)))

    unique_items = set(item for sublist in all_truly_missing_subs_langs for item in sublist)

    colors = [GREY]
    if len(unique_items) > len(colors):
        color_cycle = (colors * ((len(unique_items) // len(colors)) + 1))[:len(unique_items)]
    else:
        color_cycle = random.sample(colors, len(unique_items))
    color_map = dict(zip(unique_items, color_cycle))

    unique_vals_print = " ".join(
        f"{color_map[item]}|{RESET}{item.upper()}{color_map[item]}|{RESET}"
        for item in unique_items
    )

    if success_len or failed_len:
        custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} "
                             f"Requested {print_multi_or_single(truly_missing_subs_count, 'language')}: {unique_vals_print}")
        custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} "
                             f"{GREEN}{CHECK} {success_len}{RESET}  {RED}{CROSS} {failed_len}{RESET}")

    return all_downloaded_subs


def fetch_missing_subtitles_process_worker(debug, input_file, dirpath, missing_subs_langs, internal_threads):
    mkv_base, _, mkv_extension = input_file.rpartition('.')
    extra_pattern = r"S000E\d{3}"
    tags_pattern = r"(" + "|".join(re.escape(tag) for tag in excluded_tags) + r")$"
    is_extra = bool(re.search(extra_pattern, input_file) or re.search(tags_pattern, mkv_base))

    file_info = reformat_filename(input_file, True)
    media_type = file_info["media_type"]

    downloaded_subs = []
    failed_downloads = []

    if debug:
        print('\n')

    if not media_type == 'other' and not is_extra:
        for index, lang in enumerate(missing_subs_langs):

            command = [
                'subliminal', '--debug', '--config', './subliminal.toml', 'download', '-l', lang, input_file
            ]

            if debug:
                print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
                print(f"{RESET}")

            # Sleep for random 1-3 seconds to not overwhelm the subliminal service providers
            time.sleep(random.uniform(1.0, 3.0))

            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=dirpath)
            stdout, stderr = process.communicate()
            return_code = process.returncode

            if debug:
                print(
                    f"{GREY}[UTC {get_timestamp()}]{RESET} {YELLOW}{stdout.decode('utf-8')}\n\n{stderr.decode('utf-8')}{RESET}")

            if os.path.exists(os.path.join(dirpath, f"{mkv_base}.{lang}.srt")):
                shutil.move(os.path.join(dirpath, f"{mkv_base}.{lang}.srt"),
                            os.path.join(dirpath, f"{mkv_base}_0_''_{index + 1}_{lang}.srt"))
                downloaded_subs.append(os.path.join(dirpath, f"{mkv_base}_0_''_{index + 1}_{lang}.srt"))
            else:
                failed_downloads.append(os.path.join(dirpath, f"{mkv_base}_0_''_{index + 1}_{lang}.srt"))

    return downloaded_subs, failed_downloads


def resync_sub_process(logger, debug, input_files, dirpath, subtitle_files_to_process_list):
    total_files = len(subtitle_files_to_process_list)

    resync_subtitles = check_config(config, 'subtitles', 'resync_subtitles')
    if not resync_subtitles:
        disable_print = True
    else:
        disable_print = False

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)
    internal_threads = max(1, max_worker_threads // num_workers)

    header = "FFSUBSYNC"
    description = "Synchronize subtitles"

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(resync_subs_process_worker, debug, input_file, dirpath,
                                   subtitle_files_to_process_list[index], internal_threads): index for index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                result = future.result()
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]
                subtitle_files = subtitle_files_to_process_list[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_files{RESET}: {subtitle_files}")
                print_no_timestamp(logger, f"  {BLUE}internal_threads{RESET}: {internal_threads}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise
    return result


def resync_subs_process_worker(debug, input_file, dirpath, subtitle_files_to_process, internal_threads):
    input_file_with_path = os.path.join(dirpath, input_file)
    resync_subtitles = check_config(config, 'subtitles', 'resync_subtitles')

    if resync_subtitles:
        resync_srt_subs(internal_threads, debug, input_file_with_path, subtitle_files_to_process)


def remove_clutter_process(logger, debug, input_files, dirpath):
    total_files = len(input_files)
    all_updated_input_files = [None] * total_files
    hidden_cc_found = False

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    header = "FFMPEG"
    description = f"Remove hidden CC in video stream"

    if any(has_closed_captions(os.path.join(dirpath, file)) for file in input_files):
        hidden_cc_found = True
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(remove_clutter_process_worker, debug, input_file, dirpath): index for
                   index, input_file in enumerate(input_files)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if hidden_cc_found:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                updated_filename = future.result()
                if updated_filename is not None:
                    all_updated_input_files[index] = updated_filename
            except Exception as e:
                raise CorruptedFile
    return all_updated_input_files


def remove_clutter_process_worker(debug, input_file, dirpath):
    input_file_with_path = os.path.join(dirpath, input_file)
    updated_filename = input_file
    file_tag = check_config(config, 'general', 'file_tag')

    remove_all_mkv_track_tags(debug, input_file_with_path)

    mkv_video_codec = get_mkv_video_codec(input_file_with_path)
    if has_closed_captions(input_file_with_path):
        # Will remove hidden CC data as long as
        # video codec is not MPEG2 (DVD)
        if mkv_video_codec != 'MPEG-1/2':
            remove_cc_hidden_in_file(debug, input_file_with_path)

    if file_tag.lower() != "default" and not input_file.lower().startswith('snapchat'):
        updated_filename = replace_tags_in_file(input_file, file_tag)
        updated_filename_with_path = os.path.join(dirpath, updated_filename)
        shutil.move(input_file_with_path, updated_filename_with_path)

    return updated_filename


def repack_mkv_tracks_process(logger, debug, input_files, dirpath, audio_tracks_list,
                              subtitle_tracks_list):
    total_files = len(input_files)
    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    header = "MKVMERGE"
    description = "Repack tracks into MKV"

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(repack_mkv_tracks_process_worker, debug, input_file, dirpath, audio_tracks_list[index],
                            subtitle_tracks_list[index]): index for index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                result = future.result()
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]
                audio_tracks = audio_tracks_list[index]
                subtitle_tracks = subtitle_tracks_list[index]

                # Print the error and traceback
                custom_print(logger, f"{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}audio_tracks{RESET}: {audio_tracks}")
                print_no_timestamp(logger, f"  {BLUE}subtitle_tracks{RESET}: {subtitle_tracks}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise


def repack_mkv_tracks_process_worker(debug, input_file, dirpath, audio_tracks, subtitle_tracks):
    input_file_with_path = os.path.join(dirpath, input_file)

    repack_tracks_in_mkv(debug, input_file_with_path, audio_tracks, subtitle_tracks)


def process_external_subs(logger, debug, dirpath, input_files, all_missing_subs_langs):
    total_files = len(input_files)
    subtitle_tracks_to_be_processed = [None] * total_files
    updated_all_missing_subs_langs = [None] * total_files

    max_worker_threads = get_worker_thread_count()
    num_workers = min(total_files, max_worker_threads)

    header = "SUBTITLES"
    description = "Process external subtitles"

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(process_external_subs_worker, debug, input_file, dirpath,
                                   all_missing_subs_langs[index]): index for index, input_file in
                   enumerate(input_files)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                output_subtitles, missing_subs_langs = future.result()
                if output_subtitles is not None:
                    subtitle_tracks_to_be_processed[index] = output_subtitles
                if missing_subs_langs is not None:
                    updated_all_missing_subs_langs[index] = missing_subs_langs

            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = input_files[index]
                missing_subs_langs = all_missing_subs_langs[index]

                # Print the error and traceback
                custom_print(logger, f"\n{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}missing_subs_langs{RESET}: {missing_subs_langs}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    return subtitle_tracks_to_be_processed, updated_all_missing_subs_langs


def normalize_title(title):
    title = re.sub(r'\(\d{4}\)', '', title)
    title = re.sub(r'[\W_]+', '', title)
    return title.lower()


def process_external_subs_worker(debug, input_file, dirpath, missing_subs_langs):
    download_missing_subs = check_config(config, 'subtitles', 'download_missing_subs')

    pattern_season_episode = re.compile(r's(\d{2})e(\d{2})', re.IGNORECASE)
    match = pattern_season_episode.search(input_file)
    if match:
        season, episode = match.groups()
        season_episode = f's{season}e{episode}'.lower()
    else:
        season_episode = None

    base, extension = os.path.splitext(input_file)
    raw_name_no_ext = os.path.splitext(os.path.basename(input_file))[0]

    base_name_normalized = normalize_title(raw_name_no_ext)

    input_file_with_path = os.path.join(dirpath, input_file)
    all_langs = []
    all_sub_files = []
    updated_missing_subs_langs = []
    num = 1000

    subtitle_files = sorted(
        [f for f in os.listdir(dirpath) if f.split('.')[-1].lower() in ['srt', 'ass', 'sup', 'sub', 'idx']]
    )

    subtitle_pairs = {}
    for subtitle in subtitle_files:
        sub_base, sub_ext = os.path.splitext(subtitle)
        matched_base = None
        for existing_base in subtitle_pairs:
            if sub_base.startswith(existing_base):
                matched_base = existing_base
                break
        if sub_ext == ".idx":
            if matched_base:
                subtitle_pairs[matched_base]["idx"] = subtitle
            else:
                subtitle_pairs[sub_base] = {"idx": subtitle, "num": None}
        elif sub_ext == ".sub":
            if matched_base:
                subtitle_pairs[matched_base]["sub"] = subtitle
            else:
                subtitle_pairs[sub_base] = {"sub": subtitle, "num": None}

    processed_subs = set()

    for subtitle in subtitle_files:
        if subtitle in processed_subs:
            continue

        sub_base, sub_ext = os.path.splitext(subtitle)
        subtitle_path = os.path.join(dirpath, subtitle)

        sub_base_normalized = normalize_title(sub_base)

        if season_episode:
            match_condition = (season_episode in sub_base.lower() and base_name_normalized in sub_base_normalized)
        else:
            match_condition = (base_name_normalized in sub_base_normalized)

        if match_condition:
            lang_match = re.search(r'\.([a-z]{2,3})\.[^.]+$', subtitle, re.IGNORECASE)
            if lang_match:
                lang_part = lang_match.group(1)
                if len(lang_part) == 2:
                    try:
                        lang_code = pycountry.languages.get(alpha_2=lang_part).alpha_3
                    except:
                        lang_code = lang_part
                else:
                    lang_code = lang_part
            else:
                file_info, _ = get_mkv_info(False, input_file_with_path, False)
                main_audio_track_lang = get_main_audio_track_language(file_info)
                if main_audio_track_lang == "und":
                    lang_code = 'eng'
                else:
                    try:
                        lang_code = pycountry.languages.get(name=main_audio_track_lang).alpha_3
                    except:
                        lang_code = 'eng'

            all_langs.append(lang_code)
            language = pycountry.languages.get(alpha_3=lang_code)
            language_name = language.name if language else ''
            if sub_ext in ('.idx', '.sub', '.sup'):
                language_name = 'Original'
            output_name_b64 = base64.b64encode(language_name.encode("utf-8")).decode("utf-8")

            if sub_base in subtitle_pairs and subtitle_pairs[sub_base]["num"] is None:
                subtitle_pairs[sub_base]["num"] = num
                num += 1

            if sub_base in subtitle_pairs and subtitle_pairs[sub_base]["num"] is not None:
                assigned_num = subtitle_pairs[sub_base]["num"]
                for ext in ['idx', 'sub']:
                    if ext in subtitle_pairs[sub_base]:
                        orig_name = subtitle_pairs[sub_base][ext]
                        new_name = f"{base}_0_'{output_name_b64}'_{assigned_num}_{lang_code}.{ext}"
                        new_path = os.path.join(dirpath, new_name)
                        all_sub_files.append(new_path)
                        os.rename(os.path.join(dirpath, orig_name), new_path)
                        processed_subs.add(orig_name)
            else:
                new_subtitle_name = f"{base}_0_'{output_name_b64}'_{num}_{lang_code}.{sub_ext.lstrip('.')}"
                new_subtitle_path = os.path.join(dirpath, new_subtitle_name)
                all_sub_files.append(new_subtitle_path)
                os.rename(subtitle_path, new_subtitle_path)
                processed_subs.add(subtitle)
                num += 1

    for lang in missing_subs_langs:
        if lang not in all_langs:
            updated_missing_subs_langs.append(lang)
    if not updated_missing_subs_langs:
        updated_missing_subs_langs.append('none')

    if download_missing_subs.lower() == 'always':
        if pref_subs_langs:
            updated_missing_subs_langs = pref_subs_langs
        else:
            file_info = get_mkv_info(False, input_file_with_path, True)
            main_lang = get_main_audio_track_language_3_letter(file_info)
            updated_missing_subs_langs = [main_lang]

    return all_sub_files, updated_missing_subs_langs


def move_files_to_output_process(logger, debug, input_files, dirpath, all_dirnames, output_dir):
    total_files = len(input_files)
    normalize_filenames = check_config(config, 'general', 'normalize_filenames')
    files = input_files
    files.sort()

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    # If filenames are to be fully normalized,
    # limit workers to not hit TVMAZE rate limiting
    if normalize_filenames.lower() == 'full':
        num_workers = min(2, max_worker_threads)

    header = "INFO"
    description = f"Move {print_multi_or_single(total_files, 'file')} to destination folder"

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(move_files_to_output_process_worker, logger, debug, input_file, dirpath, all_dirnames,
                                   output_dir): input_file for index, input_file in enumerate(files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                result = future.result()
            except Exception as e:
                # Fetch the variables that were passed to the thread
                index = futures[future]
                input_file = files[index]

                # Print the error and traceback
                custom_print(logger, f"\n{RED}[ERROR]{RESET} {e}")
                print_no_timestamp(logger, f"  {BLUE}debug{RESET}: {debug}")
                print_no_timestamp(logger, f"  {BLUE}input_file{RESET}: {input_file}")
                print_no_timestamp(logger, f"  {BLUE}dirpath{RESET}: {dirpath}")
                print_no_timestamp(logger, f"  {BLUE}all_dirnames{RESET}: {all_dirnames}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise


def move_files_to_output_process_worker(logger, debug, input_file, dirpath, all_dirnames, output_dir):
    input_file_with_path = os.path.join(dirpath, input_file)

    move_file_to_output(logger, debug, input_file_with_path, output_dir, all_dirnames)


def strip_tracks_in_mkv(debug, filename, audio_tracks, default_audio_track,
                        sub_tracks, default_subs_track, always_enable_subs):
    if debug:
        print(f"{GREY}\n[UTC {get_timestamp()}] [DEBUG]{RESET} strip_tracks_in_mkv:\n")
        print(f"{BLUE}always enable subs{RESET}: {always_enable_subs}")
        print(f"{BLUE}audio tracks to keep{RESET}: {audio_tracks}")
        print(f"{BLUE}subtitle tracks to keep{RESET}: {sub_tracks}")
        print(f"{BLUE}default audio track{RESET}: {default_audio_track}")
        print(f"{BLUE}default subtitle track{RESET}: {default_subs_track}")

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
                  subs_default_track, default_subs_track_str] + [filename]
    # Remove empty entries
    command = [arg for arg in command if arg]

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        os.remove(temp_filename)
    result.check_returncode()

    os.remove(filename)
    shutil.move(temp_filename, filename)


def check_integrity_of_mkv(filename):
    command = ["mkvmerge", "--identify", filename]

    result = subprocess.run(command, capture_output=True, text=True)
    result.check_returncode()


def repack_tracks_in_mkv(debug, filename, audio_tracks, subtitle_tracks):
    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')
    pref_subs_ext = check_config(config, 'subtitles', 'pref_subs_ext')
    always_enable_subs = check_config(config, 'subtitles', 'always_enable_subs')
    forced_subtitles_priority = check_config(config, 'subtitles', 'forced_subtitles_priority')

    base, extension = os.path.splitext(filename)

    def get_codec(filepath):
        cmd = [
            "ffprobe", "-v", "quiet", "-show_entries",
            "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout.strip().lower()

    def unify_codec(acodec):
        if acodec.startswith("dts"):
            return "dts"
        if acodec.endswith("ac3"):
            return "ac3"
        return acodec

    all_tracks = []
    for name, ext, lang, track_id in zip(
            audio_tracks['audio_names'],
            audio_tracks['audio_extensions'],
            audio_tracks['audio_langs'],
            audio_tracks['audio_ids']
    ):
        try:
            final_audio_lang = pycountry.languages.get(alpha_3=lang).alpha_2
        except:
            final_audio_lang = lang[:-1]

        track_file = f"{base}.{track_id}.{final_audio_lang}.{ext}"
        codec = get_codec(track_file)
        codec = unify_codec(codec)

        is_eos = "even-out-sound" in name.lower()
        is_orig = "original" in name.lower()

        all_tracks.append({
            'name': name,
            'ext': ext,
            'lang': lang,
            'track_id': track_id,
            'codec': codec,
            'is_eos': is_eos,
            'is_orig': is_orig
        })

    # Keep track of which (codec, lang) combos have an ORIG track
    has_orig = set()
    for t in all_tracks:
        if t['is_orig']:
            has_orig.add((t['codec'], t['lang']))

    filtered_tracks = []
    for t in all_tracks:
        key = (t['codec'], t['lang'])
        if t['is_eos'] or t['is_orig']:
            # Always keep EOS and ORIG tracks
            filtered_tracks.append(t)
        else:
            # Normal track
            # Only exclude if there's an ORIG track of the same codec/lang
            if key not in has_orig:
                filtered_tracks.append(t)

    def reorder_tracks(tracks, preferences):
        def match(track, pref):
            transformation, codec, _ = pref
            if transformation == "EOS":
                return track.get("is_eos", False)
            if transformation is None:
                if codec == "ORIG":
                    return track.get("is_orig", False)
                return track.get("codec", "").lower() == codec.lower()
            return False

        def pref_index(track):
            for i, pref in enumerate(preferences):
                if match(track, pref):
                    return i
            return len(preferences)

        return sorted(tracks, key=pref_index)

    # Apply sorting
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    preferences = parse_preferred_codecs(pref_audio_formats)
    filtered_tracks = reorder_tracks(filtered_tracks, preferences)

    # Extract final lists
    audio_track_names = [t['name'] for t in filtered_tracks]
    audio_filetypes = [t['ext'] for t in filtered_tracks]
    audio_languages = [t['lang'] for t in filtered_tracks]
    audio_track_ids = [t['track_id'] for t in filtered_tracks]

    # Unpack subtitle metadata
    sub_filetypes = subtitle_tracks['sub_extensions']
    sub_languages = subtitle_tracks['sub_langs']
    sub_track_ids = subtitle_tracks['sub_ids']
    sub_track_names = subtitle_tracks['sub_names']
    sub_track_forced = subtitle_tracks['sub_forced']

    sub_files_list = []
    audio_files_list = []
    final_sub_filetypes = []
    final_sub_languages = []
    final_sub_track_ids = []
    final_sub_track_names = []
    final_sub_track_forced = []
    final_audio_filetypes = []
    final_audio_languages = []
    final_audio_track_ids = []
    final_audio_track_names = []

    # Initialize first_pref_audio_index to -1 (indicating no match found yet)
    first_pref_audio_index = -1
    # Iterate through pref_audio_langs to find the first matching language in audio_languages
    for i, lang in enumerate(pref_audio_langs):
        if lang in final_audio_languages:
            first_pref_audio_index = i
            break

    # If the first preferred language is found in the audio languages,
    # reorder the list to place the preferred language first
    if audio_languages:
        # Function to get the priority of each language
        def get_priority_langs(lang):
            try:
                return pref_audio_langs.index(lang)
            except ValueError:
                return len(pref_audio_langs)

        paired = zip(audio_languages, audio_filetypes, audio_track_ids, audio_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority_langs(x[0]))
        sorted_audio_languages, sorted_audio_filetypes, sorted_audio_track_ids, sorted_audio_track_names = zip(
            *sorted_paired)

        final_audio_languages = list(sorted_audio_languages)
        final_audio_filetypes = list(sorted_audio_filetypes)
        final_audio_track_ids = list(sorted_audio_track_ids)
        final_audio_track_names = list(sorted_audio_track_names)

    # If the first preferred language is found in the sub languages,
    # reorder the list to place the preferred language first
    if sub_languages:
        def get_priority_sub_langs(lang):
            try:
                return pref_subs_langs.index(lang)
            except ValueError:
                return len(pref_subs_langs)

        paired = zip(sub_languages, sub_filetypes, sub_track_ids, sub_track_names, sub_track_forced)
        sorted_paired = sorted(paired, key=lambda x: get_priority_sub_langs(x[0]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names, sorted_sub_track_forced = zip(
            *sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)
        final_sub_track_forced = list(sorted_sub_track_forced)

    # Reorder sub filetypes to priority list
    filetype_priority = pref_subs_ext
    if sub_filetypes:
        def get_priority_sub_filetypes(filetype):
            try:
                return filetype_priority.index(filetype)
            except ValueError:
                return len(filetype_priority)  # Default priority for unknown file types

        paired = zip(final_sub_languages, final_sub_filetypes, final_sub_track_ids, final_sub_track_names,
                     final_sub_track_forced)
        sorted_paired = sorted(paired, key=lambda x: get_priority_sub_filetypes(x[1]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names, sorted_sub_track_forced = zip(
            *sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)
        final_sub_track_forced = list(sorted_sub_track_forced)

    if debug:
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} repack_tracks_in_mkv:\n")
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
              f"\n  {BLUE}names{RESET}: {final_sub_track_names}"
              f"\n  {BLUE}forced{RESET}: {final_sub_track_forced}")

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    default_locked = False

    for index, filetype in enumerate(final_audio_filetypes):
        if not default_locked:
            default_track_str = "0:yes"
            default_locked = True
        else:
            default_track_str = "0:no"
        lang_str = f"0:{final_audio_languages[index]}"
        name_str = f"0:{final_audio_track_names[index]}"
        try:
            final_audio_language = pycountry.languages.get(alpha_3=final_audio_languages[index]).alpha_2
        except:
            final_audio_language = final_audio_languages[index][:-1]
        filelist_str = f"{base}.{final_audio_track_ids[index]}.{final_audio_language}.{filetype}"
        audio_files_list += ('--default-track', default_track_str,
                             '--language', lang_str,
                             '--track-name', name_str,
                             filelist_str)

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
        if forced_subtitles_priority.lower() == 'last':
            forced_str = f"0:0"
        else:
            forced_str = f"0:{final_sub_track_forced[index]}"
        sub_track_name = base64.b64encode(final_sub_track_names[index].encode("utf-8")).decode("utf-8")
        filelist_str = (f"{base}_{final_sub_track_forced[index]}_'{sub_track_name}'_"
                        f"{final_sub_track_ids[index]}_{final_sub_languages[index]}.{filetype}")
        sub_files_list += ('--default-track', default_track_str,
                           '--language', lang_str,
                           '--track-name', name_str,
                           '--forced-display-flag', forced_str,
                           filelist_str)

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

    if result.returncode != 0 and not os.path.exists(temp_filename):
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {RED}[ERROR]{RESET} {result.stdout}")
        print(f"{RESET}")
        result.check_returncode()

    os.remove(filename)
    shutil.move(temp_filename, filename)

    if audio_filetypes:
        for index, filetype in enumerate(final_audio_filetypes):
            try:
                final_audio_language = pycountry.languages.get(alpha_3=final_audio_languages[index]).alpha_2
            except:
                final_audio_language = final_audio_languages[index][:-1]
            os.remove(f"{base}.{final_audio_track_ids[index]}.{final_audio_language}.{filetype}")
    if sub_filetypes:
        # Need to add the .idx file as well to filetypes list for final deletion
        for index, filetype in enumerate(final_sub_filetypes):
            if filetype == "sub":
                final_sub_filetypes.append('idx')
                final_sub_languages.append(final_sub_languages[index])
                final_sub_track_ids.append(final_sub_track_ids[index])
                final_sub_track_names.append(final_sub_track_names[index])
                final_sub_track_forced.append(final_sub_track_forced[index])

        for index, filetype in enumerate(final_sub_filetypes):
            sub_track_name = base64.b64encode(final_sub_track_names[index].encode("utf-8")).decode("utf-8")
            try:
                os.remove(f"{base}_{final_sub_track_forced[index]}_'{sub_track_name}'_"
                          f"{final_sub_track_ids[index]}_{final_sub_languages[index]}.{filetype}")
            except:
                pass
