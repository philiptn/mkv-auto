import subprocess
import os
import concurrent.futures
from datetime import datetime

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