import subprocess
import json
import tempfile
import os
import re
import threading
from tqdm import tqdm
from datetime import datetime
import shutil
import time
import pycountry
import concurrent.futures
import base64
from collections import defaultdict, Counter
from itertools import chain
from pathlib import Path
from queue import Queue

from modules.misc import *
from modules.audio import *
from modules.subs import *
from modules.file_operations import *
from modules.integrations import *
from modules.encode_estimator import EncodeEstimator


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
            if file.lower().endswith(CONVERTIBLE_VIDEO_EXTENSIONS):
                video_files.append(os.path.join(root, file))

    total_files = len(video_files)
    if total_files == 0:
        return

    completed_count = 0
    print_with_progress(logger, completed_count, total_files, header=header, description=description, show_cpu=True)

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
        print_with_progress(logger, completed_count, total_files, header=header, description=description, show_cpu=True)


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
                    f"{GREY}[UTC {get_timestamp_short()}] [INFO]{RESET} Incoming file(s) detected in input folder. Waiting...")
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


def strip_mkv_title_and_track_names(debug, file_path):
    file_path = Path(file_path)

    # Check if the file exists
    if not file_path.is_file() or file_path.suffix.lower() != '.mkv':
        raise ValueError(f"The specified file is not a valid MKV file: {file_path}")

    # Ensure required tools are available
    for tool in ['mkvpropedit', 'mkvmerge']:
        if not shutil.which(tool):
            raise EnvironmentError(f"{tool} is not installed or not in PATH.")

    try:
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} Removing all track names in MKV...")

        # Get track IDs using mkvmerge
        command = ['mkvmerge', '-i', str(file_path)]
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        lines = result.stdout.splitlines()
        track_ids = [int(line.split('Track ID ')[1].split(':')[0]) for line in lines if 'Track ID' in line]

        # Remove track names (mkvpropedit uses 1-based index)
        for track_id in track_ids:
            track_index = track_id + 1
            command = ['mkvpropedit', str(file_path), '--edit', f'track:{track_index}', '--set', 'name=']
            if debug:
                print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to process file: {file_path}\n{e.stderr}")


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
    # Create a temporary empty tags XML
    empty_xml_content = '<?xml version="1.0"?><Tags></Tags>'
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml", mode="w") as tmp:
        tmp.write(empty_xml_content)
        empty_xml_path = tmp.name

    command = [
        'mkvpropedit', filename,
        '--tags', f'all:{empty_xml_path}',
        '--edit', 'track:v1', '--set', 'name=',
        '--set', 'flag-default=1',
        '--edit', 'info', '--set', 'title='
    ]

    if debug:
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} Cleaning MKV metadata...")
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    os.remove(empty_xml_path)

    if result.returncode != 0:
        print(f"\n{GREY}[UTC {get_timestamp()}] {RED}[ERROR]{RESET} {result.stderr}")
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


def trim_audio_in_mkv_files(logger, debug, input_files, dirpath):
    total_files = len(input_files)
    mkv_files_need_processing_audio = [None] * total_files
    mkv_files_need_processing_subs = [None] * total_files
    all_missing_subs_langs = [None] * total_files
    max_worker_threads = get_worker_thread_count()

    header = "MKVMERGE"
    description = "Filter audio tracks"

    # A remux costs roughly its input size, so bytes are the unit here too. The
    # strip writes a "<name>_tmp.mkv" beside the source (see
    # strip_audio_tracks_in_mkv), which is what makes a file's progress visible
    # before it finishes - the probing that precedes it is comparatively quick.
    progress = ByteProgress(total_file_size(dirpath, input_files))
    estimator = ThroughputEstimator(progress.total_bytes(), progress.done_bytes)

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description, disk_paths=dirpath,
                        estimator=estimator)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_worker_threads) as executor:
        futures = {executor.submit(trim_audio_in_mkv_files_worker, logger, debug, input_file, dirpath,
                                   progress=progress): index for
                   index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description,
                                disk_paths=dirpath, estimator=estimator)
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
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    return mkv_files_need_processing_audio, mkv_files_need_processing_subs, all_missing_subs_langs


def trim_audio_in_mkv_files_worker(logger, debug, input_file, dirpath, progress=None):
    input_file = os.path.join(dirpath, input_file)
    file_size = os.path.getsize(input_file) if os.path.exists(input_file) else 0
    check_integrity_of_mkv(input_file)

    # Get file info using mkvinfo
    file_info, pretty_file_info = get_mkv_info(debug, input_file, False)

    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    remove_commentary = check_config(config, 'audio', 'remove_commentary')
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')
    download_missing_subs = check_config(config, 'subtitles', 'download_missing_subs')

    wanted_audio = get_wanted_audio_tracks(
        debug, file_info, pref_audio_langs, remove_commentary, pref_audio_formats)
    needs_processing_audio = wanted_audio.needs_processing

    if needs_processing_audio:
        # The remux writes here before replacing the source, so its growth is
        # this file's visible progress.
        base, extension = os.path.splitext(input_file)
        if progress is not None:
            progress.start(input_file, base + "_tmp" + extension)
        strip_audio_tracks_in_mkv(logger, debug, input_file,
                                  wanted_audio.wanted_track_ids, wanted_audio.default_track_id)

    if progress is not None:
        progress.finish(input_file, file_size)

    file_info, pretty_file_info = get_mkv_info(debug, input_file, False)

    (wanted_subs_tracks, default_subs_track,
     needs_sdh_removal, needs_convert, sub_filetypes,
     subs_track_languages, subs_track_names, needs_processing_subs,
     a, missing_subs_langs) = get_wanted_subtitle_tracks(
        debug, file_info, pref_subs_langs)

    if download_missing_subs.lower() == 'override':
        needs_processing_subs = True
        if pref_subs_langs != ['']:
            missing_subs_langs = pref_subs_langs
        else:
            main_lang = get_main_audio_track_language_3_letter(file_info)
            missing_subs_langs = [main_lang]

    return needs_processing_audio, needs_processing_subs, missing_subs_langs


# A track whose channel count the container did not report still has to be
# priced somehow; 5.1 is the common case. Mirrors the estimator's own habit of
# falling back to a plausible figure rather than to zero.
AUDIO_DEFAULT_CHANNELS = 6


class AudioJobProgress:
    """Job-indexed bridge from the audio encode threads to the batch ETA and counter.

    encode_single_preference() runs on a nested pool inside each file's worker,
    so every method here is reached from several threads at once. The counter is
    read by the spinner's render thread, which is why it is lock-guarded rather
    than a bare int.

    A ``job`` of None means the preference is a stream copy: it advances the
    visible counter but is never priced (see is_copy_only_preference).
    """

    def __init__(self, estimator, job_index_map):
        self._estimator = estimator
        self._job_index_map = job_index_map
        self._lock = threading.Lock()
        self._done = 0

    def index_of(self, file_index, track_index, pref_index):
        """Estimator index for this job, or None if it is not priced."""
        return self._job_index_map.get((file_index, track_index, pref_index))

    def start(self, job):
        if job is not None and self._estimator is not None:
            self._estimator.note_start(job)

    def advance(self, job, fraction):
        if job is not None and self._estimator is not None:
            self._estimator.note_progress(job, fraction)

    def finish(self, job):
        if job is not None and self._estimator is not None:
            self._estimator.note_complete(job)
        with self._lock:
            self._done += 1

    def done(self):
        with self._lock:
            return self._done


def audio_track_channels(file_info, track_id):
    """Channel count for a source track from the mkvmerge JSON, or None."""
    for track in file_info.get('tracks', []):
        if track.get('id') == track_id:
            return track.get('properties', {}).get('audio_channels')
    return None


def mkv_duration_seconds(file_info, path):
    """Container duration in seconds: mkvmerge's own figure, else an ffprobe.

    mkvmerge reports this in nanoseconds and omits it for some sources, so the
    ffprobe is the fallback rather than the primary - it costs another process.
    """
    duration_ns = file_info.get('container', {}).get('properties', {}).get('duration')
    if duration_ns:
        return duration_ns / 1_000_000_000
    try:
        return probe_duration_seconds(path)
    except Exception:
        # A missing duration only costs the ETA its normaliser; the stage itself
        # runs identically, and the estimator prices such a job from wall time.
        return None


def probe_audio_work(input_file, dirpath):
    """Everything the batch ETA needs about one file, before any encoding.

    Performs exactly the probe generate_audio_tracks_in_mkv_files_worker() used
    to do itself, so hoisting this costs no extra mkvmerge call - and guarantees
    the priced job list is the one the worker actually encodes.
    """
    input_path = os.path.join(dirpath, input_file)
    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    remove_commentary = check_config(config, 'audio', 'remove_commentary')

    file_info, _ = get_mkv_info(False, input_path, True)
    wanted_audio = get_wanted_audio_tracks(
        False, file_info, pref_audio_langs, remove_commentary, pref_audio_formats)

    duration = mkv_duration_seconds(file_info, input_path)
    channels = {c.track_id: audio_track_channels(file_info, c.track_id)
                for c in wanted_audio.tracks_to_convert}
    return wanted_audio, duration, channels


def build_audio_jobs(input_files, probe_results, preferences):
    """Split the batch into encode jobs and price the ones that are transcodes.

    Returns (jobs, job_index_map, total_jobs). ``total_jobs`` counts every
    (track x preference) unit including stream copies - that is what the visible
    counter tracks - while ``jobs`` holds only the transcodes, since a copy
    finishes near-instantly and would drag the estimator's cost median down.
    """
    jobs = []
    job_index_map = {}
    total_jobs = 0

    for file_index, probe in enumerate(probe_results):
        if probe is None:
            continue
        wanted_audio, duration, channels_by_id = probe
        if not wanted_audio.needs_processing:
            continue

        for track_index, candidate in enumerate(wanted_audio.tracks_to_convert):
            channels = channels_by_id.get(candidate.track_id) or AUDIO_DEFAULT_CHANNELS
            for pref_index, (transformation, codec, ch_str) in enumerate(preferences):
                total_jobs += 1
                if is_copy_only_preference(transformation, codec):
                    continue
                job_index_map[(file_index, track_index, pref_index)] = len(jobs)
                jobs.append({
                    'index': len(jobs),
                    'name': f"{input_files[file_index]}#{candidate.track_id}:{codec}",
                    'duration': duration,
                    # The estimator normalises by 'pixels' = width*height. For
                    # audio the analogous cost driver is channel count, so a
                    # 5.1 track is priced ~3x a stereo one of equal length.
                    'width': channels,
                    'height': 1,
                    'is_4k': False,
                    # ffmpeg's audio encoders are effectively single-threaded;
                    # the parallelism here is across jobs, not within one.
                    'threads': 1,
                })

    return jobs, job_index_map, total_jobs


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
    num_workers, internal_threads = compute_thread_allocation(total_files, max_worker_threads)

    header = "AUDIO"
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

    # Probe every file before any encoding starts. EncodeEstimator takes a fixed
    # job list at construction, and the worker's own probe would come too late -
    # and could disagree with whatever had been priced. This is the same probe
    # the worker used to run, hoisted and parallelised, so the count is unchanged.
    probe_results = [None] * total_files
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as prober:
        probe_futures = {prober.submit(probe_audio_work, input_file, dirpath): index
                         for index, input_file in enumerate(input_files)}
        for future in concurrent.futures.as_completed(probe_futures):
            probe_results[probe_futures[future]] = future.result()

    jobs, job_index_map, total_jobs = build_audio_jobs(input_files, probe_results,
                                                       audio_format_preferences)

    # No status text is set on the estimator: the line simply carries no ETA
    # until there is a real one, rather than a placeholder holding the space.
    estimator = EncodeEstimator(jobs, max_worker_threads) if jobs else None
    if estimator is not None:
        estimator.set_slots(max_worker_threads)
    reporter = AudioJobProgress(estimator, job_index_map) if total_jobs else None

    # A local spinner, not print_with_progress: the counter is advanced from the
    # encode threads, and print_with_progress mutates a module global on every
    # call. Here only the spinner's own render thread reads the counter.
    spinner = None
    if not disable_print and total_jobs:
        print()
        spinner = ContinuousSpinner(interval=0.15)
        spinner.set_line_func(make_batch_eta_line(header, description, estimator,
                                                  reporter.done, total_jobs, show_cpu=True))
        spinner.start()

    # Use ThreadPoolExecutor to handle multithreading
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(generate_audio_tracks_in_mkv_files_worker, debug, input_file, dirpath,
                                       internal_threads, file_index=index,
                                       wanted_audio=probe_results[index][0],
                                       duration=probe_results[index][1],
                                       reporter=reporter): index
                       for index, input_file in enumerate(input_files)}

            for future in concurrent.futures.as_completed(futures):
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
    finally:
        if spinner is not None:
            final_line = (f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} "
                          f"{description} {DONE}{CHECK}{RESET}")
            spinner.stop(final_line)
            logger.info(f"[UTC {get_timestamp()}] [{header}] {description} {CHECK}")
            logger.debug(f"[UTC {get_timestamp()}] [{header}] {description} {CHECK}")
            logger.color(final_line)
        if estimator is not None:
            log_debug(logger, f"[AUDIO] ETA estimator: {estimator.debug_line()}")

    return all_ready_audio_tracks, all_ready_subtitle_tracks


def generate_audio_tracks_in_mkv_files_worker(debug, input_file, dirpath, internal_threads,
                                              file_index=None, wanted_audio=None,
                                              duration=None, reporter=None):
    input_file = os.path.join(dirpath, input_file)

    ready_audio_paths = []
    ready_audio_extensions = []
    ready_audio_langs = []
    ready_track_ids = []
    ready_track_names = []

    pref_audio_langs = check_config(config, 'audio', 'pref_audio_langs')
    pref_audio_formats = check_config(config, 'audio', 'pref_audio_formats')
    remove_commentary = check_config(config, 'audio', 'remove_commentary')

    # Normally handed in by the caller's pre-pass, which already ran this exact
    # probe to price the batch. Repeat it here only when called standalone.
    if wanted_audio is None:
        # Get updated file info after mkv tracks reduction
        file_info, pretty_file_info = get_mkv_info(False, input_file, True)

        wanted_audio = get_wanted_audio_tracks(
            False, file_info, pref_audio_langs, remove_commentary, pref_audio_formats)

    # Generating audio tracks if preferred codec not found in all audio tracks
    if wanted_audio.needs_processing:
        if debug:
            print('')

        extracted_audio_tracks = extract_audio_tracks_in_mkv(internal_threads, debug, input_file,
                                                             wanted_audio.tracks_to_convert)

        encoded_audio_tracks = encode_audio_tracks(
            internal_threads, debug, extracted_audio_tracks, pref_audio_formats,
            file_index=file_index, duration=duration, reporter=reporter)

        ready_audio_paths = [t.path for t in encoded_audio_tracks]
        ready_audio_extensions = [t.extension for t in encoded_audio_tracks]
        ready_audio_langs = [t.language for t in encoded_audio_tracks]
        ready_track_ids = [t.track_id for t in encoded_audio_tracks]
        ready_track_names = [t.name for t in encoded_audio_tracks]

    # Dummy subtitle metadata needs to be returned for
    # rest of the pipeline to function properly
    return {
        'audio_paths': ready_audio_paths,
        'audio_extensions': ready_audio_extensions,
        'audio_langs': ready_audio_langs,
        'audio_ids': ready_track_ids,
        'audio_names': ready_track_names
    }, {
        'sub_paths': None,
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
    num_workers, internal_threads = compute_thread_allocation(total_files, max_worker_threads)

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description, disk_paths=dirpath)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(extract_subs_in_mkv_process_worker, logger, debug, input_file, dirpath, internal_threads): index for
            index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if not disable_print:
                print_with_progress(logger, completed_count, total_files, header=header, description=description,
                                    disk_paths=dirpath)
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


def has_dolby_vision(file_path):
    scan_cmd = ["dovi_convert", "scan", file_path]

    try:
        proc = subprocess.run(
            scan_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
    except Exception:
        return False

    if proc.returncode != 0:
        return False

    output = proc.stdout

    # True only if Dolby Vision profile detected
    return "DV Profile" in output


def classify_dolby_vision(file_path):
    """
    Run a single `dovi_convert scan` and classify the file:
      "none"    - no Dolby Vision detected (or scan failed)
      "p5"      - Dolby Vision Profile 5 (cannot convert to 8.1)
      "convert" - conversion to Profile 8.1 required
      "skip"    - Dolby Vision present but already 8.1 / no conversion needed
    """
    scan_cmd = ["dovi_convert", "scan", file_path]

    try:
        proc = subprocess.run(
            scan_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
    except Exception:
        return "none"

    if proc.returncode != 0 or "DV Profile" not in proc.stdout:
        return "none"

    status_line = next(
        (line.strip() for line in proc.stdout.splitlines()
         if "status:" in line.lower()),
        ""
    )

    action = ""
    for line in proc.stdout.splitlines():
        if "action:" in line.lower():
            action = line.split(":", 1)[1].strip().replace("\r", "").upper()
            break

    if "Profile 5" in status_line:
        return "p5"

    if "CONVERT" in action:
        return "convert"

    return "skip"


def dovi_scan_and_convert_single(logger, debug, input_file, dirpath,
                                 progress: ProgressState, worker_id):
    """
    Scan and convert Dolby Vision using dovi_convert turbo mode.
    Adds detailed debugging and failure detection.
    """

    media_file = os.path.join(dirpath, input_file)

    filesize_info = {
        "initial_file_size": os.path.getsize(media_file),
        "resulting_file_size": 0
    }

    progress.start_worker(worker_id)
    start_time = time.time()
    file_converted = "skip"

    log_debug(logger, f"[DOVI] Processing: {media_file}")

    # Only process typical video containers
    if not input_file.lower().endswith((".mkv", ".mp4", ".m2ts", ".ts")):
        log_debug(logger, f"[DOVI] Skipping (unsupported container): {input_file}")
        progress.finish_worker(worker_id)
        filesize_info["resulting_file_size"] = filesize_info["initial_file_size"]
        return input_file, filesize_info, file_converted

    # -------------------------------------------------
    # Stage 1: Scan
    # -------------------------------------------------
    scan_cmd = ["dovi_convert", "scan", media_file]
    log_debug(logger, f"[DOVI] Scan command: {' '.join(scan_cmd)}")

    scan_proc = subprocess.run(
        scan_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if debug or scan_proc.returncode != 0:
        log_debug(logger, f"[DOVI][SCAN][STDOUT]\n{scan_proc.stdout}")
        log_debug(logger, f"[DOVI][SCAN][STDERR]\n{scan_proc.stderr}")

    if scan_proc.returncode != 0:
        log_debug(logger, f"[DOVI] Scan failed (code {scan_proc.returncode}) — skipping")
        progress.finish_worker(worker_id)
        filesize_info["resulting_file_size"] = filesize_info["initial_file_size"]
        return input_file, filesize_info, file_converted

    progress.update_worker_progress(worker_id, 0.25)

    output = scan_proc.stdout.lower()

    # Hard check: skip if no Dolby Vision detected
    if "dv profile" not in output:
        log_debug(logger, f"[DOVI] No Dolby Vision detected: {input_file}")
        progress.finish_worker(worker_id)
        filesize_info["resulting_file_size"] = filesize_info["initial_file_size"]
        return input_file, filesize_info, "skip"

    # Extract status line for debugging
    if "status:" in output:
        status_line = next(
            (line.strip() for line in scan_proc.stdout.splitlines()
             if "status:" in line.lower()),
            ""
        )
        log_debug(logger, f"[DOVI] {status_line}")

    if "action:" in output:
        action_line = next(
            (line.strip() for line in scan_proc.stdout.splitlines()
             if "action:" in line.lower()),
            ""
        )
        log_debug(logger, f"[DOVI] {action_line}")

    # Skip if no conversion required
    action = None
    for line in scan_proc.stdout.splitlines():
        if "action:" in line.lower():
            action = line.split(":", 1)[1]
            action = action.strip().replace("\r", "").upper()
            break

    log_debug(logger, f"[DOVI] Parsed action: {action}")

    if "Profile 5" in status_line:
        log_debug(logger, f"[DOVI] Profile 5 detected. Unable to convert to Profile 8.1: {input_file}")
        file_converted = "p5"
        progress.finish_worker(worker_id)
        filesize_info["resulting_file_size"] = filesize_info["initial_file_size"]
        return input_file, filesize_info, file_converted

    if "CONVERT" not in action:
        log_debug(logger, f"[DOVI] No conversion required: {input_file}")
        progress.finish_worker(worker_id)
        filesize_info["resulting_file_size"] = filesize_info["initial_file_size"]
        return input_file, filesize_info, file_converted

    # -------------------------------------------------
    # Stage 2: Convert
    # Pass --include-simple so Simple FEL files (DV Profile 7 FEL (Simple))
    # are auto-converted in non-interactive mode; without it dovi_convert
    # skips/blocks on them.
    # -------------------------------------------------
    log_debug(logger, f"[DOVI] Starting conversion: {input_file}")

    convert_cmd = [
        "dovi_convert",
        "convert",
        "--include-simple",
        media_file
    ]

    log_debug(logger, f"[DOVI] Convert command: {' '.join(convert_cmd)}")

    process = subprocess.Popen(
        convert_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    output_lines = []
    saw_progress = False

    for line in process.stdout:
        output_lines.append(line)
        l = line.lower().strip()

        if debug:
            log_debug(logger, f"[DOVI] {line.strip()}")

        if "[1/3]" in l:
            progress.update_worker_progress(worker_id, 0.50)
            saw_progress = True
        elif "[2/3]" in l:
            progress.update_worker_progress(worker_id, 0.75)
        elif "[3/3]" in l:
            progress.update_worker_progress(worker_id, 0.95)

    process.wait()

    full_output = "".join(output_lines)

    if process.returncode != 0:
        log_debug(logger, "[DOVI] Conversion FAILED")
        log_debug(logger, f"[DOVI] Exit code: {process.returncode}")
        log_debug(logger, f"[DOVI] Output:\n{full_output}")

        raise subprocess.CalledProcessError(
            process.returncode,
            convert_cmd,
            output=full_output
        )

    if not saw_progress:
        log_debug(logger, "[DOVI] Warning: no stage progress detected")

    # -------------------------------------------------
    # Stage 3: Cleanup backup
    # -------------------------------------------------
    backup_file = media_file + ".bak.dovi_convert"

    if os.path.exists(backup_file):
        try:
            os.remove(backup_file)
            log_debug(logger, f"[DOVI] Removed backup: {backup_file}")
        except Exception as e:
            log_debug(logger, f"[DOVI] Failed to remove backup: {e}")

    # -------------------------------------------------
    # Validate result
    # -------------------------------------------------
    if os.path.exists(media_file):
        new_size = os.path.getsize(media_file)
        filesize_info["resulting_file_size"] = new_size

        if new_size == filesize_info["initial_file_size"]:
            log_debug(logger, "[DOVI] Warning: file size unchanged after conversion")
    else:
        log_debug(logger, "[DOVI] ERROR: output file missing after conversion")

    elapsed = time.time() - start_time
    log_debug(logger, f"[DOVI] Finished {input_file} in {elapsed:.2f}s")
    file_converted = "true"
    # -------------------------------------------------
    # Stage 4: Upgrade filename (Profile 7 → DV HDR)
    # -------------------------------------------------
    try:
        new_filename = upgrade_dv_to_dv_hdr_filename(input_file)

        if new_filename != input_file:
            old_path = media_file
            new_path = os.path.join(dirpath, new_filename)

            if not os.path.exists(new_path):
                os.rename(old_path, new_path)
                log_debug(logger, f"[DOVI] Renamed DV → DV HDR: {input_file} -> {new_filename}")
                input_file = new_filename
            else:
                log_debug(logger, f"[DOVI] Rename skipped (target exists): {new_filename}")

    except Exception as e:
        log_debug(logger, f"[DOVI] Filename upgrade failed: {e}")

    progress.finish_worker(worker_id)

    return input_file, filesize_info, file_converted


def convert_dovi_files(logger, debug, input_files, dirpath):
    """
    Multithreaded Dolby Vision conversion using dovi_convert turbo mode.
    Only converts when scan says Action: CONVERT.
    """

    original_files = list(input_files)

    # -------------------------------------------------
    # Pre-scan: find files that contain Dolby Vision
    # -------------------------------------------------
    dovi_jobs = []

    for index, f in enumerate(input_files):
        if not f.lower().endswith((".mkv", ".mp4", ".m2ts", ".ts")):
            continue

        full_path = os.path.join(dirpath, f)

        # Only enqueue files with real work: files needing conversion, or
        # Profile 5 (reported as a skip). Files already at Profile 8.1 are
        # left out so the stage produces no output at all when nothing is done.
        if classify_dolby_vision(full_path) in ("convert", "p5"):
            dovi_jobs.append((index, f))

    if not dovi_jobs:
        return original_files

    total_files = len(dovi_jobs)

    max_worker_threads = get_worker_thread_count()
    num_workers = min(max_worker_threads, total_files)

    header = "DOVI"
    description = "Dolby Vision"
    done_description = "Dolby Vision → Profile 8.1"
    skip_description = "Dolby Vision Profile 5 → Skip"

    progress = ProgressState(total_files, num_workers)

    worker_id_pool = Queue()
    for wid in range(num_workers):
        worker_id_pool.put(wid)

    print()
    start_time = time.time()

    SPINNER = ContinuousSpinner()
    SPINNER.set_line_func(make_progress_line_no_temp(progress, header, description, start_time,
                                                     disk_paths=dirpath))
    SPINNER.start()

    results = {}
    files_converted = []

    def worker_wrapper(job_index, original_index, filename):
        worker_id = worker_id_pool.get()
        try:
            updated_filename, filesize_info, file_converted = dovi_scan_and_convert_single(
                logger,
                debug,
                filename,
                dirpath,
                progress,
                worker_id
            )
            return original_index, updated_filename, file_converted
        finally:
            worker_id_pool.put(worker_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:

        futures = [
            executor.submit(worker_wrapper, i, index, filename)
            for i, (index, filename) in enumerate(dovi_jobs)
        ]

        for future in concurrent.futures.as_completed(futures):
            original_index, updated_filename, file_converted = future.result()

            results[original_index] = updated_filename
            files_converted.append(file_converted)


    final_files = list(original_files)
    for index, new_name in results.items():
        if new_name:
            final_files[index] = new_name

    converted_any = any(c == 'true' for c in files_converted)
    failed_any = any(c == 'fail' for c in files_converted)

    if converted_any and failed_any:
        # Partial: some converted, some failed
        stop_print = (f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} "
                      f"{done_description} {DONE}~{RESET}")
    elif converted_any:
        # Success: at least one converted, none failed
        stop_print = (f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} "
                      f"{done_description} {DONE}{CHECK}{RESET}")
    elif all(c == 'p5' for c in files_converted):
        # Nothing converted, everything was Profile 5
        stop_print = (f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} "
                      f"{skip_description}")
    else:
        # Nothing actually converted: don't claim a conversion happened
        stop_print = None

    if stop_print is None:
        SPINNER.stop()
    else:
        SPINNER.stop(stop_print)

    return final_files


def extract_subs_in_mkv_process_worker(logger, debug, input_file, dirpath, internal_threads):
    input_file_with_path = os.path.join(dirpath, input_file)
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')

    # Get updated file info after mkv tracks reduction
    file_info, pretty_file_info = get_mkv_info(debug, input_file_with_path, True)

    (wanted_subs_tracks, a, b, needs_convert,
     sub_filetypes, subs_track_languages,
     subs_track_names, e, subs_track_forced, f) = get_wanted_subtitle_tracks(debug, file_info, pref_subs_langs)

    subtitle_files = extract_subs_in_mkv(logger, internal_threads, debug, input_file_with_path, wanted_subs_tracks,
                                         sub_filetypes, subs_track_languages, subs_track_forced, subs_track_names)

    return subtitle_files


def convert_to_srt_process(logger, debug, input_files, dirpath, subtitle_files_list, errored_subs_bool):
    sub_files = [
        [t for t in (sublist or []) if t is not None and t.extension in ('srt', 'sup', 'ass', 'sub')]
        for sublist in subtitle_files_list
    ]
    total_files = len(sub_files)

    all_ready_subtitle_tracks = [None] * total_files
    subtitle_tracks_to_be_processed = [None] * total_files
    all_replacements_list = [None] * total_files
    all_errored_subs = [None] * total_files
    all_missing_subs_langs = [None] * total_files
    main_audio_track_langs_list = [None] * total_files
    subtitle_tracks_all = [None] * total_files

    disable_print = False

    # Disable print if all the subtitles to be processed are SRT (therefore no OCR is needed)
    for subs in sub_files:
        if subs:
            if all(sub.extension == 'srt' for sub in subs):
                disable_print = True
            else:
                disable_print = False
                break
        else:
            disable_print = True

    max_worker_threads, memory_per_thread, max_mem_allowed = get_max_ocr_threads()

    if errored_subs_bool:
        max_worker_threads = 1
        memory_per_thread = max_mem_allowed

    num_workers = max(1, max_worker_threads)  # Ensure num_workers is at least 1.
    internal_threads = max(1, max_worker_threads // num_workers)

    header = "SUBTITLES"
    description = "Convert subtitles to SRT"

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(convert_to_srt_process_worker, logger, debug, input_file, dirpath, internal_threads,
                                   sub_files[index], memory_per_thread): index for index, input_file in enumerate(input_files)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                if not disable_print and completed_count < total_files:
                    print_with_progress(logger, completed_count, total_files, header=header, description=description)
                index = futures[future]
                ready_tracks, output_subtitles, subtitles_all, all_replacements, errored_subs, missing_subs_langs, main_audio_track_langs = future.result()
                if ready_tracks is not None:
                    all_ready_subtitle_tracks[index] = ready_tracks
                if output_subtitles is not None:
                    subtitle_tracks_to_be_processed[index] = output_subtitles
                if subtitles_all is not None:
                    subtitle_tracks_all[index] = subtitles_all
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
        if not disable_print:
            if [item for list in all_errored_subs for item in list]:
                print_with_progress(logger, completed_count, -1, header=header, description=description)
            else:
                print_with_progress(logger, completed_count, total_files, header=header, description=description)

    all_replacements_list_count = len([item for list in all_replacements_list for item in list])

    if all_replacements_list_count:
        flattened_replacements = list(chain.from_iterable(all_replacements_list))
        log_debug(logger, '')
        log_debug(logger, f"{GREY}[DEBUG]{RESET} During OCR, the following {len(flattened_replacements)} "
                          f"{print_multi_or_single(len(flattened_replacements), 'word')} were fixed:")
        replacements_counter = Counter(flattened_replacements)
        for replacement, count in replacements_counter.items():
            if count > 1:
                log_debug(logger, f"{replacement} {GREY}({count} times){RESET}")
            else:
                log_debug(logger, replacement)
        log_debug(logger, '')

    all_errored_subs_count = len([item for list in all_errored_subs for item in list])
    if all_errored_subs_count:
        if errored_subs_bool:
            verb = 'were' if all_errored_subs_count > 1 else 'was'
            print()
            custom_print_no_newline(logger, f"{GREY}[SUBTITLES]{RESET} {all_errored_subs_count} "
                                 f"{print_multi_or_single(all_errored_subs_count, 'subtitle')} {verb} not able to be converted.")
        elif not errored_subs_bool:
            print()
            custom_print(logger, f"{GREY}[SUBTITLES]{RESET} {all_errored_subs_count} "
                                 f"{print_multi_or_single(all_errored_subs_count, 'subtitle')} failed to be converted.")

        errored_subs_print = []
        for errored_sub in all_errored_subs:
            if errored_sub:
                errored_subs_print.append(os.path.basename(errored_sub[0].path))
        errored_subs_print.sort()

        for index, sub in enumerate(errored_subs_print):
            log_debug(logger, f"[OCR ERROR] '{sub}'")

    return (all_ready_subtitle_tracks, subtitle_tracks_to_be_processed, subtitle_tracks_all,
            all_missing_subs_langs, all_errored_subs, main_audio_track_langs_list)


def convert_to_srt_process_worker(logger, debug, input_file, dirpath, internal_threads, subtitle_files, memory_per_thread):
    input_file_with_path = os.path.join(dirpath, input_file)
    subtitle_files_to_process = subtitle_files

    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')

    # Get updated file info after mkv tracks reduction
    file_info, pretty_file_info = get_mkv_info(False, input_file_with_path, True)
    # Get main audio track language
    main_audio_track_lang = get_main_audio_track_language(file_info)

    (wanted_subs_tracks, a, b, needs_convert,
     sub_filetypes, subs_track_languages,
     subs_track_names, e, subs_track_forced, f) = get_wanted_subtitle_tracks(False, file_info, pref_subs_langs)

    errored_ass_subs = []
    if "ass" in sub_filetypes:
        all_subtitles, errored_ass_subs, missing_subs_langs = convert_ass_to_srt(subtitle_files_to_process, main_audio_track_lang)
        subtitle_files_to_process = all_subtitles

    (output_subtitles, subtitles_all, all_replacements,
     errored_ocr_subs, missing_subs_langs) = ocr_subtitles(
        logger, internal_threads, memory_per_thread, debug, subtitle_files_to_process, main_audio_track_lang)

    errored_subs = errored_ass_subs + errored_ocr_subs

    return (build_subtitle_repack_dict(subtitles_all), output_subtitles, subtitles_all,
            all_replacements, errored_subs, missing_subs_langs, main_audio_track_lang)


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


def return_subtitle_metadata_worker(subtitle_tracks, max_threads):
    return build_subtitle_repack_dict(subtitle_tracks)


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
    a, memory_per_thread, b = get_max_ocr_threads()

    header = "SUBTITLES"
    description = "Remove SDH from subtitles"

    if not disable_print:
        # Initialize progress
        print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(remove_sdh_process_worker, logger, debug, list, internal_threads,
                                   memory_per_thread): index for index, list in enumerate(subtitle_files_to_process_list)}
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


def remove_sdh_process_worker(logger, debug, input_subtitles, internal_threads, memory_per_thread):
    all_replacements = []
    remove_music = check_config(config, 'subtitles', 'remove_music')
    always_remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')
    srt_files = [t for t in (input_subtitles or []) if t is not None and t.extension == 'srt']

    if always_remove_sdh:
        a, all_replacements = remove_sdh(internal_threads, logger, debug, srt_files, remove_music, [],
                                         False, memory_per_thread)
    return all_replacements


def to_alpha2(lang):
    if not lang:
        return None

    lang = lang.lower()

    if len(lang) == 2:
        return lang

    if len(lang) == 3:
        try:
            return pycountry.languages.get(alpha_3=lang).alpha_2
        except:
            return None

    return None


def to_alpha2(lang):
    if not lang:
        return None

    lang = lang.lower()

    if len(lang) == 2:
        return lang

    if len(lang) == 3:
        try:
            return pycountry.languages.get(alpha_3=lang).alpha_2
        except:
            return None

    return None


def fetch_missing_subtitles_process(logger, debug, input_files, dirpath,
                                    total_external_subs, all_missing_subs_langs):
    total_files = len(input_files)

    if all(sub == ['none'] for sub in all_missing_subs_langs) and not total_external_subs:
        return

    all_truly_missing_subs_langs = []
    all_downloaded_subs = [None] * total_files
    all_failed_downloads = [None] * total_files
    all_downloaded_subs_simple = [None] * total_files
    all_failed_downloads_simple = [None] * total_files

    header = "SUBLIMINAL"
    description = f"Process missing subtitles"

    for index, input_file in enumerate(input_files):
        truly_missing_subs_langs = []

        # Languages already provided by this file's external subtitles
        external_for_file = total_external_subs[index] if (total_external_subs and index < len(total_external_subs)) else []
        episode_external_langs = {to_alpha2(t.language) for t in (external_for_file or []) if t}

        for lang in all_missing_subs_langs[index]:
            if not lang or lang == 'none' or lang.lower() == 'und':
                continue

            lang2 = to_alpha2(lang)
            if not lang2:
                continue

            if lang2 not in episode_external_langs:
                truly_missing_subs_langs.append(lang2)

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
                                   all_truly_missing_subs_langs[index], internal_threads, logger): index for index, input_file
                   in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                downloaded_subs, failed_downloads, downloaded_subs_simple, failed_downloads_simple = future.result()
                if downloaded_subs is not None:
                    all_downloaded_subs[index] = downloaded_subs
                if failed_downloads is not None:
                    all_failed_downloads[index] = failed_downloads
                if downloaded_subs_simple is not None:
                    all_downloaded_subs_simple[index] = downloaded_subs_simple
                if failed_downloads_simple is not None:
                    all_failed_downloads_simple[index] = failed_downloads_simple
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
        print()
        custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} "
                             f"Requested {print_multi_or_single(truly_missing_subs_count, 'language')}: {unique_vals_print}")
        custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} "
                             f"{GREEN}{CHECK} {success_len}{RESET}  {RED}{CROSS} {failed_len}{RESET}")

        combined_downloaded = [item for sublist in all_downloaded_subs_simple for item in sublist]
        combined_failed = [item for sublist in all_failed_downloads_simple for item in sublist]

        if combined_downloaded:
            downloaded_subs_info = return_media_info_string(logger, combined_downloaded, GREEN)
            for index, info in enumerate(downloaded_subs_info):
                if index + 1 == len(downloaded_subs_info) and not combined_failed:
                    custom_print_no_newline(logger, f"{GREY}[SUBLIMINAL]{RESET} {info}")
                else:
                    custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} {info}")

        if combined_failed:
            failed_downloads_info = return_media_info_string(logger, combined_failed, RED)
            for index, info in enumerate(failed_downloads_info):
                if index + 1 == len(failed_downloads_info):
                    custom_print_no_newline(logger, f"{GREY}[SUBLIMINAL]{RESET} {info}")
                else:
                    custom_print(logger, f"{GREY}[SUBLIMINAL]{RESET} {info}")

    return all_downloaded_subs


def fetch_missing_subtitles_process_worker(debug, input_file, dirpath, missing_subs_langs, internal_threads, logger):
    filename = input_file
    mkv_base, _, mkv_extension = input_file.rpartition('.')
    mkv_base_simple, _, a = filename.rpartition('.')
    extra_pattern = r"S000E\d{3}"
    tags_pattern = r"(" + "|".join(re.escape(tag) for tag in extras_definitions) + r")$"
    is_extra = bool(re.search(extra_pattern, filename) or re.search(tags_pattern, mkv_base))

    file_info = reformat_filename(filename, True, False, False, logger)
    media_type = file_info["media_type"]

    downloaded_subs = []
    downloaded_subs_simple = []
    failed_downloads = []
    failed_downloads_simple = []

    if debug:
        print('\n')

    if not media_type == 'other' and not is_extra:
        for index, lang in enumerate(missing_subs_langs):

            command = [
                'subliminal', '--debug', '--config', './subliminal.toml', 'download', '-l', lang, filename
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

            if os.path.exists(os.path.join(dirpath, f"{mkv_base_simple}.{lang}.srt")):
                downloaded_path = os.path.join(dirpath, f"{mkv_base}.sd{index + 1}.srt")
                shutil.move(os.path.join(dirpath, f"{mkv_base_simple}.{lang}.srt"), downloaded_path)
                downloaded_subs.append(SubtitleTrack(path=downloaded_path, track_id=index + 1, language=lang,
                                                     forced=False, name='', extension='srt', source='downloaded'))
                downloaded_subs_simple.append(mkv_base_simple)
            else:
                failed_downloads.append(os.path.join(dirpath, f"{mkv_base}.sd{index + 1}.srt"))
                failed_downloads_simple.append(mkv_base_simple)

    return downloaded_subs, failed_downloads, downloaded_subs_simple, failed_downloads_simple


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
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise
    return all_updated_input_files


def remove_clutter_process_worker(debug, input_file, dirpath):
    input_file_with_path = os.path.join(dirpath, input_file)
    updated_filename = input_file

    file_tag = check_config(config, 'general', 'file_tag')
    remove_all_title_names = check_config(config, 'general', 'remove_all_title_names')

    remove_all_mkv_track_tags(debug, input_file_with_path)
    if remove_all_title_names:
        strip_mkv_title_and_track_names(debug, input_file_with_path)

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
    print_with_progress(logger, 0, total_files, header=header, description=description, disk_paths=dirpath)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(repack_mkv_tracks_process_worker, debug, input_file, dirpath, audio_tracks_list[index],
                            subtitle_tracks_list[index]): index for index, input_file in enumerate(input_files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description,
                                disk_paths=dirpath)
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


def normalize_lang(lang):
    if not lang:
        return None

    lang = lang.lower()

    if len(lang) == 2:
        try:
            return pycountry.languages.get(alpha_2=lang).alpha_3
        except:
            return lang

    if len(lang) == 3:
        return lang

    return lang


def process_external_subs_worker(debug, input_file, dirpath, missing_subs_langs):
    download_missing_subs = check_config(config, 'subtitles', 'download_missing_subs')
    main_audio_language_subs_only = check_config(config, 'subtitles', 'main_audio_language_subs_only')
    pref_subs_langs = check_config(config, 'subtitles', 'pref_subs_langs')

    pattern_season_episode = re.compile(r's(\d{2})e(\d{2})', re.IGNORECASE)
    match = pattern_season_episode.search(input_file)

    if not match:
        return [], missing_subs_langs

    season, episode = match.groups()
    season_episode = f's{season}e{episode}'.lower()

    show_name_raw = input_file[:match.start()]
    show_name = re.sub(r'[._]+', ' ', show_name_raw).strip()
    show_name_normalized = normalize_title(show_name)

    input_file_with_path = os.path.join(dirpath, input_file)
    file_info, _ = get_mkv_info(False, input_file_with_path, False)
    main_audio_track_lang = get_main_audio_track_language_3_letter(file_info)
    main_audio_lang_norm = normalize_lang(main_audio_track_lang)

    base, _ = os.path.splitext(input_file)

    subtitle_files = sorted(
        f for f in os.listdir(dirpath)
        if f.split('.')[-1].lower() in {'srt', 'ass', 'sup', 'sub', 'idx'}
    )

    subtitle_pairs = {}
    for subtitle in subtitle_files:
        sub_base, sub_ext = os.path.splitext(subtitle)
        if sub_ext in ('.idx', '.sub'):
            subtitle_pairs.setdefault(sub_base, {})[sub_ext.lstrip('.')] = subtitle

    processed_subs = set()
    episode_langs = set()
    all_sub_files = []
    num = 1000
    base_to_num = {}

    for subtitle in subtitle_files:
        if subtitle in processed_subs:
            continue

        sub_base, sub_ext = os.path.splitext(subtitle)
        sub_base_lower = sub_base.lower()
        sub_base_normalized = normalize_title(sub_base)

        if season_episode not in sub_base_lower:
            continue

        if show_name_normalized not in sub_base_normalized:
            continue

        lang_match = re.search(r'\.([a-z]{2,3})\.[^.]+$', subtitle, re.IGNORECASE)
        if lang_match:
            lang_code = normalize_lang(lang_match.group(1))
        else:
            lang_code = main_audio_lang_norm or 'eng'

        episode_langs.add(lang_code)

        language = pycountry.languages.get(alpha_3=lang_code)
        language_name = language.name if language else ''

        if sub_ext in ('.idx', '.sub', '.sup'):
            language_name = 'Original'

        # One synthetic id per source file (shared by an .idx/.sub pair). The id
        # only disambiguates working names; metadata lives on the SubtitleTrack.
        if sub_base not in base_to_num:
            base_to_num[sub_base] = num
            num += 1
        assigned_num = base_to_num[sub_base]

        if sub_base in subtitle_pairs:
            for ext in ('idx', 'sub'):
                if ext in subtitle_pairs[sub_base]:
                    orig = subtitle_pairs[sub_base][ext]
                    new_path = os.path.join(dirpath, f"{base}.se{assigned_num}.{ext}")
                    os.rename(os.path.join(dirpath, orig), new_path)
                    all_sub_files.append(SubtitleTrack(path=new_path, track_id=assigned_num, language=lang_code,
                                                       forced=False, name=language_name, extension=ext,
                                                       source='external'))
                    processed_subs.add(orig)
        else:
            new_path = os.path.join(dirpath, f"{base}.se{assigned_num}{sub_ext}")
            os.rename(os.path.join(dirpath, subtitle), new_path)
            all_sub_files.append(SubtitleTrack(path=new_path, track_id=assigned_num, language=lang_code,
                                               forced=False, name=language_name, extension=sub_ext.lstrip('.'),
                                               source='external'))
            processed_subs.add(subtitle)

    updated_missing_subs_langs = []

    for lang in missing_subs_langs:
        lang_norm = normalize_lang(lang)

        if main_audio_language_subs_only:
            if lang_norm == main_audio_lang_norm and lang_norm not in episode_langs:
                updated_missing_subs_langs.append(lang)
        else:
            if lang_norm not in episode_langs:
                updated_missing_subs_langs.append(lang)

    if pref_subs_langs != ['']:
        all_sub_files = [t for t in all_sub_files if t.language in pref_subs_langs]

    if main_audio_language_subs_only:
        all_sub_files = [t for t in all_sub_files if t.language == main_audio_lang_norm]

    if download_missing_subs.lower() == 'always':
        if pref_subs_langs:
            updated_missing_subs_langs = pref_subs_langs
        else:
            file_info = get_mkv_info(False, input_file_with_path, True)
            main_lang = get_main_audio_track_language_3_letter(file_info)
            updated_missing_subs_langs = [main_lang]

    return all_sub_files, updated_missing_subs_langs


def move_files_to_output_process(logger, debug, input_files, dirpath, origins, output_dir, errored,
                                 resolved_targets=None):
    total_files = len(input_files)
    normalize_filenames = check_config(config, 'general', 'normalize_filenames')
    resolved_targets = resolved_targets or {}
    origins = origins or {}
    files = input_files
    files.sort(key=natural_sort_key)

    new_radarr_paths = [None] * total_files
    new_sonarr_paths = [None] * total_files

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    # If filenames are to be fully normalized,
    # limit workers to not hit TVMAZE rate limiting
    if normalize_filenames.lower() in ('full', 'full-jf'):
        num_workers = min(2, max_worker_threads)

    header = "INFO"
    if errored:
        description = f"Move unprocessed {print_multi_or_single(total_files, 'file')} to destination folder"
    else:
        description = f"Move {print_multi_or_single(total_files, 'file')} to destination folder"

    # Usually a cross-filesystem move, i.e. a copy: bytes again, and the
    # destination grows as it is written.
    progress = ByteProgress(total_file_size(dirpath, files))
    estimator = ThroughputEstimator(progress.total_bytes(), progress.done_bytes)

    # Initialize progress
    print_with_progress(logger, 0, total_files, header=header, description=description,
                        disk_paths=(dirpath, output_dir), estimator=estimator)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(move_files_to_output_process_worker, logger, debug, input_file, dirpath, origins.get(input_file),
                                   output_dir, resolved_targets.get(input_file), progress): index for index, input_file in enumerate(files)}

        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description,
                                disk_paths=(dirpath, output_dir), estimator=estimator)
            try:
                index = futures[future]
                new_radarr_path, new_sonarr_path = future.result()
                if new_radarr_path is not None:
                    new_radarr_paths[index] = new_radarr_path
                if new_sonarr_path is not None:
                    new_sonarr_paths[index] = new_sonarr_path
            except Exception as e:
                # Print the error and traceback
                custom_print(logger, f"\n{RED}[ERROR]{RESET} {e}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    print_arr_summary(logger, zip(new_radarr_paths, new_sonarr_paths))


def print_arr_summary(logger, results):
    """Print the Radarr/Sonarr tallies for a list of (radarr_path, sonarr_path).

    Shared by the batch move stage and the encoder's incremental mover, which
    delivers its files one at a time but still reports a single tally at the end.
    Counts truthy entries, so a slot that never produced a path - because its
    file errored, or because no API key is configured - is simply not counted.
    """
    results = list(results)
    new_radarr_paths_len = sum(1 for radarr, _ in results if radarr and radarr.strip())
    new_sonarr_paths_len = sum(1 for _, sonarr in results if sonarr and sonarr.strip())

    print_msg = (f"{GREY}[RADARR]{RESET} Updated {new_radarr_paths_len} "
                 f"{print_multi_or_single(new_radarr_paths_len, 'movie folder')} in Radarr.")
    if new_radarr_paths_len and new_sonarr_paths_len:
        print()
        custom_print(logger, print_msg)
    elif new_radarr_paths_len and not new_sonarr_paths_len:
        print()
        custom_print_no_newline(logger, print_msg)

    print_msg = (f"{GREY}[SONARR]{RESET} Updated {new_sonarr_paths_len} "
                 f"{print_multi_or_single(new_sonarr_paths_len, 'TV folder')} in Sonarr.")
    if new_sonarr_paths_len and not new_radarr_paths_len:
        print()
        custom_print_no_newline(logger, print_msg)
    elif new_sonarr_paths_len and new_radarr_paths_len:
        custom_print_no_newline(logger, print_msg)


def move_files_to_output_process_worker(logger, debug, input_file, dirpath, working_file, output_dir,
                                        resolved_target=None, progress=None):
    input_file_with_path = os.path.join(dirpath, input_file)
    new_radarr_path = ''
    new_sonarr_path = ''
    file_size = os.path.getsize(input_file_with_path) if os.path.exists(input_file_with_path) else 0

    radarr_api_key = check_config(config, 'integrations', 'radarr_api_key')
    sonarr_api_key = check_config(config, 'integrations', 'sonarr_api_key')

    try:
        if resolved_target is not None:
            if progress is not None:
                progress.start(input_file, resolved_target["output_path"])
            output_info = move_resolved_to_output(logger, input_file_with_path, resolved_target)
        else:
            relative_dir = working_file.relative_dir if working_file else ""
            original_name = working_file.original_name if working_file else input_file
            # Without a pre-resolved target the destination path is only known
            # once move_file_to_output() has worked it out, so this file's bytes
            # land in one step at the end rather than growing visibly.
            output_info = move_file_to_output(logger, debug, input_file_with_path, output_dir, relative_dir, original_name)
    finally:
        if progress is not None:
            progress.finish(input_file, file_size)

    file_info = reformat_filename(output_info["filename"], True, False, False, logger)
    media_type = file_info["media_type"]

    if media_type in ['tv_show', 'tv_show_hdr', 'tv_show_4k', 'anime']:
        full_name = file_info["full_name"]
        if sonarr_api_key and file_info["media_name"]:
            new_sonarr_path = update_sonarr_path(logger, full_name, file_info["media_name"])
    elif media_type in ['movie', 'movie_hdr', 'movie_4k']:
        full_name = file_info["full_name"]
        if radarr_api_key and file_info["media_name"]:
            new_radarr_path = update_radarr_path(logger, full_name, file_info["media_name"])

    return new_radarr_path, new_sonarr_path


def strip_audio_tracks_in_mkv(logger, debug, filename, audio_tracks, default_audio_track):
    if debug:
        print(f"{GREY}\n[UTC {get_timestamp()}] [DEBUG]{RESET} strip_audio_tracks_in_mkv:\n")
        print(f"{BLUE}audio tracks to keep{RESET}: {audio_tracks}")
        print(f"{BLUE}default audio track{RESET}: {default_audio_track}")

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

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ["mkvmerge",
               "--output", temp_filename,
               audio, audio_tracks_str,
               audio_default_track, default_audio_track_str] + [filename]
    # Remove empty entries
    command = [arg for arg in command if arg]

    if debug:
        print('')
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}")
        print(f"{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        os.remove(temp_filename)
        result.check_returncode()
        log_debug(logger, result.stderr)

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

    def get_codec_and_channels(filepath):
        cmd = [
            "ffprobe", "-v", "quiet", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,channels",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        lines = res.stdout.strip().splitlines()
        codec = unify_codec(lines[0].lower() if lines else "unknown")
        channels = int(lines[1]) if len(lines) > 1 else 0
        return codec, channels

    def unify_codec(acodec):
        if acodec.startswith("dts"):
            return "dts"
        if acodec.endswith("ac3"):
            return "ac3"
        return acodec

    all_tracks = []
    for name, ext, lang, track_id, track_file in zip(
            audio_tracks['audio_names'],
            audio_tracks['audio_extensions'],
            audio_tracks['audio_langs'],
            audio_tracks['audio_ids'],
            audio_tracks['audio_paths']
    ):
        codec, channels = get_codec_and_channels(track_file)

        is_eos = "even-out-sound" in name.lower()
        is_orig = "original" in name.lower()

        all_tracks.append({
            'name': name,
            'ext': ext,
            'lang': lang,
            'track_id': track_id,
            'path': track_file,
            'codec': codec,
            'channels': channels,
            'is_eos': is_eos,
            'is_orig': is_orig
        })

    # Track (codec, lang, channels) combos with an ORIG
    has_orig = set()
    for t in all_tracks:
        if t['is_orig']:
            has_orig.add((t['codec'], t['lang'], t['channels']))

    filtered_tracks = []
    for t in all_tracks:
        key = (t['codec'], t['lang'], t['channels'])
        if t['is_eos'] or t['is_orig']:
            filtered_tracks.append(t)
        else:
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
    audio_track_paths = [t['path'] for t in filtered_tracks]

    # Unpack subtitle metadata
    sub_filetypes = subtitle_tracks['sub_extensions']
    sub_languages = subtitle_tracks['sub_langs']
    sub_track_ids = subtitle_tracks['sub_ids']
    sub_track_names = subtitle_tracks['sub_names']
    sub_track_forced = subtitle_tracks['sub_forced']
    sub_track_paths = subtitle_tracks['sub_paths']

    sub_files_list = []
    audio_files_list = []

    final_sub_filetypes = []
    final_sub_languages = []
    final_sub_track_ids = []
    final_sub_track_names = []
    final_sub_track_forced = []
    final_sub_paths = []

    final_audio_filetypes = []
    final_audio_languages = []
    final_audio_track_ids = []
    final_audio_track_names = []
    final_audio_paths = []

    # If the first preferred language is found in the audio languages,
    # reorder the list to place the preferred language first
    if audio_languages:
        # Function to get the priority of each language
        def get_priority_langs(lang):
            try:
                return pref_audio_langs.index(lang)
            except ValueError:
                return len(pref_audio_langs)

        paired = zip(audio_languages, audio_filetypes, audio_track_ids, audio_track_names, audio_track_paths)
        sorted_paired = sorted(paired, key=lambda x: get_priority_langs(x[0]))
        sorted_audio_languages, sorted_audio_filetypes, sorted_audio_track_ids, sorted_audio_track_names, sorted_audio_paths = zip(
            *sorted_paired)

        final_audio_languages = list(sorted_audio_languages)
        final_audio_filetypes = list(sorted_audio_filetypes)
        final_audio_track_ids = list(sorted_audio_track_ids)
        final_audio_track_names = list(sorted_audio_track_names)
        final_audio_paths = list(sorted_audio_paths)

    # If the first preferred language is found in the sub languages,
    # reorder the list to place the preferred language first
    if sub_languages:
        def get_priority_sub_langs(lang):
            try:
                return pref_subs_langs.index(lang)
            except ValueError:
                return len(pref_subs_langs)

        paired = zip(sub_languages, sub_filetypes, sub_track_ids, sub_track_names, sub_track_forced, sub_track_paths)
        sorted_paired = sorted(paired, key=lambda x: get_priority_sub_langs(x[0]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names, sorted_sub_track_forced, sorted_sub_paths = zip(
            *sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)
        final_sub_track_forced = list(sorted_sub_track_forced)
        final_sub_paths = list(sorted_sub_paths)

    # Reorder sub filetypes to priority list
    filetype_priority = pref_subs_ext
    if sub_filetypes:
        def get_priority_sub_filetypes(filetype):
            try:
                return filetype_priority.index(filetype)
            except ValueError:
                return len(filetype_priority)  # Default priority for unknown file types

        paired = zip(final_sub_languages, final_sub_filetypes, final_sub_track_ids, final_sub_track_names,
                     final_sub_track_forced, final_sub_paths)
        sorted_paired = sorted(paired, key=lambda x: get_priority_sub_filetypes(x[1]))
        sorted_sub_languages, sorted_sub_filetypes, sorted_sub_track_ids, sorted_sub_track_names, sorted_sub_track_forced, sorted_sub_paths = zip(
            *sorted_paired)

        final_sub_languages = list(sorted_sub_languages)
        final_sub_filetypes = list(sorted_sub_filetypes)
        final_sub_track_ids = list(sorted_sub_track_ids)
        final_sub_track_names = list(sorted_sub_track_names)
        final_sub_track_forced = list(sorted_sub_track_forced)
        final_sub_paths = list(sorted_sub_paths)

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
        filelist_str = final_audio_paths[index]
        audio_files_list += ('--default-track', default_track_str,
                             '--language', lang_str,
                             '--track-name', name_str,
                             filelist_str)

    default_locked = False
    for index, filetype in enumerate(final_sub_filetypes):
        default_track_str = "0:no"
        sub_path = final_sub_paths[index]
        # mkvmerge does not support the .sub file as input,
        # and requires the sibling .idx specified instead
        if filetype == "sub":
            sub_path = os.path.splitext(sub_path)[0] + '.idx'
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
        sub_files_list += ('--default-track', default_track_str,
                           '--language', lang_str,
                           '--track-name', name_str,
                           '--forced-display-flag', forced_str,
                           sub_path)

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

    # Audio files cleanup
    if audio_filetypes:
        for audio_path in audio_tracks['audio_paths']:
            if os.path.exists(audio_path):
                os.remove(audio_path)
    # Subtitle files cleanup (also remove the sibling .idx for any VobSub)
    if sub_filetypes:
        for sub_path in final_sub_paths:
            for path in (sub_path, os.path.splitext(sub_path)[0] + '.idx'):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass
