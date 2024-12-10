from subtitle_filter import Subtitles
import asstosrt
import os
import subprocess
import pysrt
import shutil
from datetime import datetime
import time
import csv
import re
import concurrent.futures
import random
from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException
import pycountry
import concurrent.futures
import xml.etree.ElementTree as ET
import concurrent.futures
import threading
import tempfile
from collections import Counter
import concurrent.futures
from tqdm import tqdm
import base64
import signal

from scripts.misc import *

# Define a XML lock
xml_file_lock = threading.Lock()
# Create an X11 server lock
x11_lock = threading.Lock()
reserved_displays = set()


def clean_invalid_utf8(input_file, output_file):
    # Read the file, replacing invalid characters with '�'
    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Write the cleaned content back
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)


def is_valid_srt(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read().strip()
            if not content:
                return False

            # Define regex pattern to match a valid SRT timestamp only
            pattern = re.compile(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}")

            # Check if there is at least one valid timestamp
            return bool(pattern.search(content))

    except:
        return False


def find_and_replace(input_file, replacement_file, output_file):
    # Read the input file content
    with open(input_file, 'r', encoding='utf-8') as file:
        data = file.read()

    changes = []  # List to hold before/after strings for each replacement
    with open(replacement_file, 'r', encoding='utf-8') as file:
        reader = csv.reader(file)
        replacements = list(reader)

    # Apply each replacement in the file data
    for find, replace in replacements:
        start = 0
        while (pos := data.find(find, start)) != -1:
            changes.append(f"{GREY}found{RESET}: '{RED}{find}{RESET}', "
                           f"{GREY}replaced with{RESET}: '{GREEN}{replace}{RESET}'")
            data = data[:pos] + replace + data[pos + len(find):]
            start = pos + len(replace)

    # Write the modified content to the output file
    with open(output_file, 'w', encoding='utf-8') as file:
        file.write(data)

    return changes


def get_active_xvfb_displays():
    active_displays = set()

    try:
        # Run the command and capture the output
        command = "pgrep Xvfb | xargs -I{} ps -p {} -o args | grep -oP '(?<=:)\d+'"
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, text=True)

        # Process each line of output, converting it to an integer and adding to the set
        for line in result.stdout.splitlines():
            try:
                display_number = int(line)
                active_displays.add(display_number)
            except ValueError:
                continue
    except Exception as e:
        print(f"Error while checking active Xvfb displays: {e}")

    return active_displays


def find_available_display():
    while True:
        with x11_lock:
            # Get currently active Xvfb displays
            active_displays = get_active_xvfb_displays()

            # Generate a random display number and check if it's available
            display_number = random.randint(50, 9000)
            if display_number not in reserved_displays and display_number not in active_displays:
                reserved_displays.add(display_number)
                return display_number


def release_display(display_number):
    with x11_lock:
        # Remove the display number from the reserved set
        reserved_displays.remove(display_number)


def run_with_xvfb(command):
    time.sleep(random.uniform(0.5, 1.5))
    display_number = find_available_display()

    xvfb_process = None
    command_process = None

    try:
        # Start Xvfb in the background with a new process group
        xvfb_cmd = ["Xvfb", f":{display_number}", "-screen", "0", "1024x768x24",
                    "-ac", "-nolisten", "tcp", "-nolisten", "unix"]
        xvfb_process = subprocess.Popen(xvfb_cmd, preexec_fn=os.setsid)

        # Set the DISPLAY environment variable
        env = os.environ.copy()
        env['DISPLAY'] = f":{display_number}"

        # Start the main command in the same new process group
        command_process = subprocess.Popen(command, env=env, preexec_fn=os.setsid,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Wait for the command to complete and capture output
        stdout, stderr = command_process.communicate()
        return_code = command_process.returncode

        # Terminate Xvfb process after the command completes
        os.killpg(os.getpgid(xvfb_process.pid), signal.SIGTERM)
        xvfb_process.wait()

        return return_code
    except:
        # If any exception occurs, kill both processes to prevent them from lingering
        if xvfb_process and xvfb_process.poll() is None:
            os.killpg(os.getpgid(xvfb_process.pid), signal.SIGTERM)
        if command_process and command_process.poll() is None:
            os.killpg(os.getpgid(command_process.pid), signal.SIGTERM)
        return -1
    finally:
        release_display(display_number)


def detect_language_of_subtitle(subtitle_path):
    try:
        with open(subtitle_path, 'r', encoding='utf-8') as file:
            subtitle_content = file.read()
            # Detect language of the subtitle content
            language_code = detect(subtitle_content)
            # Get the full language name
            language = pycountry.languages.get(alpha_2=language_code)
            return language_code, language.name if language else "Unknown language"
    except LangDetectException:
        return "Language detection failed"
    except FileNotFoundError:
        return "File not found"


def remove_sdh_worker(debug, input_file, remove_music, subtitleedit):
    base_and_lang_with_id, _, original_extension = input_file.rpartition('.')
    base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
    base, _, track_id = base_with_id.rpartition('.')
    replacements = []

    redo_casing = check_config(config, 'subtitles', 'redo_casing')

    if redo_casing:
        command = ["mono", subtitleedit, "/convert", input_file,
                   "srt", "/SplitLongLines", "/encoding:utf-8", "/RemoveTextForHI", "/RedoCasing",
                   f"/outputfilename:{input_file}_tmp.srt"]
    else:
        command = ["mono", subtitleedit, "/convert", input_file,
                   "srt", "/SplitLongLines", "/encoding:utf-8", "/RemoveTextForHI",
                   f"/outputfilename:{input_file}_tmp.srt"]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    run_with_xvfb(command)
    os.remove(input_file)
    shutil.move(f"{input_file}_tmp.srt", input_file)

    if remove_music:
        clean_invalid_utf8(input_file, f'{input_file}.tmp.srt')
        os.remove(input_file)
        shutil.move(f'{input_file}.tmp.srt', input_file)

        subs = pysrt.open(input_file)
        # Filter the subtitles in place, removing entries with '♪' in their text
        subs = pysrt.SubRipFile([sub for sub in subs if '♪' not in sub.text])
        subs.save(f"{input_file}.tmp.srt", encoding='utf-8')
        shutil.move(f"{input_file}.tmp.srt", input_file)

        subs = Subtitles(input_file)
        subs.filter(
            rm_fonts=False,
            rm_ast=False,
            rm_music=True,
            rm_effects=False,
            rm_names=False,
            rm_author=False,
        )
        subs.save()

        clean_invalid_utf8(input_file, f'{input_file}.tmp.srt')
        shutil.move(f"{input_file}.tmp.srt", input_file)

        subs = pysrt.open(input_file)
        subs = pysrt.SubRipFile([sub for sub in subs if not sub.text.isupper()])
        subs.save(f"{input_file}.tmp.srt", encoding='utf-8')
        shutil.move(f"{input_file}.tmp.srt", input_file)

    subtitle_tmp = f"{input_file}_tmp.srt"
    if lang == 'en':
        current_replacements = find_and_replace(input_file, 'scripts/replacements_srt_eng_only.csv', subtitle_tmp)
        replacements = replacements + current_replacements
        current_replacements = find_and_replace(subtitle_tmp, 'scripts/replacements_srt_only.csv', input_file)
        os.remove(subtitle_tmp)
        replacements = replacements + current_replacements
    else:
        current_replacements = find_and_replace(input_file, 'scripts/replacements_srt_only.csv', subtitle_tmp)
        os.rename(subtitle_tmp, input_file)
        replacements = replacements + current_replacements

    return replacements


def remove_sdh(max_threads, debug, input_files, remove_music, track_names, external_sub):
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    all_replacements = []
    cleaned_track_names = []

    if debug:
        print('\n')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        tasks = [executor.submit(remove_sdh_worker, debug, input_file, remove_music, subtitleedit)
                 for i, input_file in enumerate(input_files)]
        concurrent.futures.wait(tasks)  # Wait for all tasks to complete

    for future in concurrent.futures.as_completed(tasks):
        replacements = future.result()
        all_replacements = all_replacements + replacements

    if track_names:
        cleaned_track_names = [track.replace("SDH", "").replace("sdh", "")
                               .replace("()", "").strip() for track in track_names]
    if debug:
        print('')

    if all_replacements and debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} During processing, the following words were replaced:")
        print('')
        replacements_counter = Counter(all_replacements)
        for replacement, count in replacements_counter.items():
            if count > 1:
                print(f"{replacement} {GREY}({count} times){RESET}")
            else:
                print(replacement)
        print('')

    return cleaned_track_names, all_replacements


def convert_ass_to_srt(subtitle_files, main_audio_track_lang):
    output_subtitles = []
    keep_original_subtitles = check_config(config, 'subtitles', 'keep_original_subtitles')

    for index, file in enumerate(subtitle_files):
        if file.endswith('.ass'):
            base_lang_id_name_forced, _, original_extension = file.rpartition('.')
            base_id_name_forced, _, language = base_lang_id_name_forced.rpartition('_')
            base_name_forced, _, track_id = base_id_name_forced.rpartition('_')
            base_forced, _, name_encoded = base_name_forced.rpartition('_')
            name_encoded = name_encoded.strip("'") if name_encoded.startswith("'") and name_encoded.endswith("'") else name_encoded
            name = base64.b64decode(name_encoded).decode("utf-8")
            base, _, forced = base_forced.rpartition('_')

            if name:
                original_name_b64 = base64.b64encode(name.encode("utf-8")).decode("utf-8")
            else:
                original_name_b64 = base64.b64encode('Original'.encode("utf-8")).decode("utf-8")

            if forced != '0' and bool(forced):
                output_name = f'non-{main_audio_track_lang} dialogue'
                output_name_b64 = base64.b64encode(output_name.encode("utf-8")).decode("utf-8")
                original_subtitle = f"{base}_0_'{original_name_b64}'_{track_id}_{language}.{original_extension}"
                final_subtitle = f"{base}_{forced}_'{output_name_b64}'_{track_id}_{language}.srt"
            else:
                output_name = ''
                output_name_b64 = base64.b64encode(output_name.encode("utf-8")).decode("utf-8")
                original_subtitle = f"{base}_{forced}_'{original_name_b64}'_{track_id}_{language}.{original_extension}"
                final_subtitle = f"{base}_{forced}_'{output_name_b64}'_{track_id}_{language}.srt"

            os.rename(file, original_subtitle)
            ass_file = open(original_subtitle)
            srt_output = asstosrt.convert(ass_file)
            with open(final_subtitle, "w") as srt_file:
                srt_file.write(srt_output)

            if keep_original_subtitles:
                output_subtitles = output_subtitles + [final_subtitle, original_subtitle]
            else:
                output_subtitles = output_subtitles + [final_subtitle]
        else:
            output_subtitles = output_subtitles + [file]

    return output_subtitles


def resync_srt_subs(max_threads, debug, input_file, subtitle_files):

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Create a list of tasks for each subtitle file
        tasks = [executor.submit(resync_srt_subs_worker, debug, input_file, subfile, max_retries=3, retry_delay=2)
                 for subfile in subtitle_files]
        # Wait for all tasks to complete
        for task in concurrent.futures.as_completed(tasks):
            try:
                task.result()  # This will re-raise any exception from the thread
            except Exception as e:
                print(f"\n{RED}[ERROR]{RESET} {e}")
                traceback.print_tb(e.__traceback__)
                raise
    if debug:
        print('')


def resync_srt_subs_worker(debug, input_file, subtitle_filename, max_retries, retry_delay):
    base_lang_id_name_forced, _, original_extension = subtitle_filename.rpartition('.')
    base_id_name_forced, _, language = base_lang_id_name_forced.rpartition('_')
    try:
        full_language = pycountry.languages.get(alpha_3=language).name
    except:
        full_language = ''
    base_name_forced, _, track_id = base_id_name_forced.rpartition('_')
    base_forced, _, name = base_name_forced.rpartition('_')
    # Remove starting and ending single-quotes
    name_encoded = name[1:-1] if name.startswith("'") and name.endswith("'") else name
    name = base64.b64decode(name_encoded).decode("utf-8")
    base, _, forced = base_forced.rpartition('_')

    temp_filename = f"{base}_{forced}_'{name_encoded}'_{track_id}_{language}_tmp.srt"

    # If the subtitle track is a forced track,
    # skip resyncing as these have tendency to get out of sync
    if forced != '0' and bool(forced) or f'non- Dialogue' in name:
        return

    command = ["ffs", input_file, "--max-offset-seconds", "10",
               "-i", subtitle_filename, "-o", temp_filename]

    retries = 0
    while retries < max_retries:
        if debug:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        return_code = process.returncode

        if debug:
            print(f"\n{GREY}[UTC {get_timestamp()}]{RESET} {YELLOW}{stderr.decode('utf-8')}{RESET}")

        if return_code == 0:
            # Success, move the file and exit the loop
            os.remove(subtitle_filename)
            shutil.move(temp_filename, subtitle_filename)
            break
        else:
            retries += 1
            if retries >= max_retries:
                # Exceeded the maximum number of retries, raise an exception
                raise Exception(f"Error executing FFsubsync command: {stderr}")
            time.sleep(retry_delay)  # Wait before retrying


def extract_subs_in_mkv(max_threads, debug, filename, track_numbers, output_filetypes, subs_languages, subs_forced, subs_names):

    results = [None] * len(track_numbers)  # Pre-allocate a list for the results in order
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Create a dictionary to store futures with their respective indices
        future_to_index = {
            executor.submit(extract_subtitle, debug, filename, track, filetype, language, forced, name): i
            for i, (track, filetype, language, forced, name) in
            enumerate(zip(track_numbers, output_filetypes, subs_languages, subs_forced, subs_names))
        }

        # As each future completes, place the result in the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()  # Store the result at the correct index

    return results


def extract_subtitle(debug, filename, track, output_filetype, language, forced, name):
    if output_filetype in ('sup', 'sub', 'ass'):
        if not name:
            cleartext_name = 'Original'
        else:
            cleartext_name = name
    else:
        cleartext_name = name
    base, _, _ = filename.rpartition('.')
    b64_name = base64.b64encode(cleartext_name.encode("utf-8")).decode("utf-8")

    subtitle_filename = f"{base}_{forced}_'{b64_name}'_{track}_{language}.{output_filetype}"
    command = ["mkvextract", filename, "tracks", f"{track}:{subtitle_filename}"]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    result.check_returncode()

    return subtitle_filename


def get_output_subtitle_string(filename, track_numbers, output_filetypes, subs_languages):
    subtitle_filenames = []

    for index, output_filetype in enumerate(output_filetypes):
        base, _, _ = filename.rpartition('.')
        subtitle_filename = f"{base}.{track_numbers[index]}.{subs_languages[index][:-1]}.{output_filetype}"
        subtitle_filenames.append(subtitle_filename)

    return subtitle_filenames


def ocr_subtitles(max_threads, debug, subtitle_files, main_audio_track_lang):
    subtitleedit_dir = 'utilities/SubtitleEdit'
    all_replacements = []
    keep_original_subtitles = check_config(config, 'subtitles', 'keep_original_subtitles')

    if debug:
        print('\n')

    # Prepare to track the results in the order they were submitted
    results = [None] * len(subtitle_files)  # Placeholder list for results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Submit all tasks and store futures in a dictionary with their index
        future_to_index = {
            executor.submit(ocr_subtitle_worker, debug, subtitle_files[i], main_audio_track_lang, subtitleedit_dir): i
            for i in range(len(subtitle_files))
        }

        # As each future completes, store the result at the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()

    # Initialize lists for the structured results
    output_subtitles = []
    updated_subtitle_languages = []
    all_track_ids = []
    all_track_names = []
    all_track_forced = []
    updated_sub_filetypes = []
    errored_ocr = []
    missing_subs_langs = []

    # Process each result and organize the outputs
    for original_file, output_subtitle, language, track_id, name, forced, replacements, original_extension in results:
        all_replacements = replacements + all_replacements
        if output_subtitle and output_subtitle != 'ERROR':
            if keep_original_subtitles:
                updated_sub_filetypes = updated_sub_filetypes + ['srt', original_extension]
                output_subtitles = output_subtitles + [output_subtitle]
                updated_subtitle_languages = updated_subtitle_languages + [language, language]
                all_track_ids = all_track_ids + [track_id, track_id]
                if 'forced' in name.lower() or (forced != '0' and bool(forced)):
                    all_track_names = all_track_names + [f'non-{main_audio_track_lang} dialogue',
                                                         name if name else "Original"]
                    # Enable forced only for the generated file, not original
                    all_track_forced = all_track_forced + [1, 0]
                else:
                    all_track_names = all_track_names + ['', name if name else "Original"]
                    all_track_forced = all_track_forced + [forced, forced]
            else:
                updated_sub_filetypes = updated_sub_filetypes + ['srt']
                updated_subtitle_languages = updated_subtitle_languages + [language]
                all_track_ids = all_track_ids + [track_id]
                if 'forced' in name.lower() or (forced != '0' and bool(forced)):
                    all_track_names = all_track_names + [f'non-{main_audio_track_lang} dialogue']
                    all_track_forced = all_track_forced + [1]
                else:
                    all_track_names = all_track_names + ['']
                    all_track_forced = all_track_forced + [forced]
        else:
            if output_subtitle == 'ERROR':
                errored_ocr.append(original_file)
                if not language == 'ERROR':
                    missing_subs_langs.append(language)
            if not name == 'ERROR':
                output_subtitles.append(original_file)
                if 'forced' in name.lower() or (forced != '0' and bool(forced)):
                    all_track_names.append(f'non-{main_audio_track_lang} dialogue')
                else:
                    all_track_names.append(name if name else '')
            if not original_extension == 'ERROR':
                updated_sub_filetypes.append(original_extension)
            if not language == 'ERROR':
                updated_subtitle_languages.append(language)
            if not track_id == 'ERROR':
                all_track_ids.append(track_id)
            if not forced == 'ERROR':
                all_track_forced.append(forced)

    if debug and all_replacements:
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} During OCR, the following words were fixed:\n")
        replacements_counter = Counter(all_replacements)
        for replacement, count in replacements_counter.items():
            if count > 1:
                print(f"{replacement} {GREY}({count} times){RESET}")
            else:
                print(replacement)
        print('')

    return (output_subtitles, updated_subtitle_languages, all_track_ids, all_track_names,
            all_track_forced, updated_sub_filetypes, all_replacements, errored_ocr, missing_subs_langs)


def ocr_subtitle_worker(debug, file, main_audio_track_lang, subtitleedit_dir):
    limit_ocr_languages = check_config(config, 'subtitles', 'limit_ocr_languages')
    replacements = []
    # Create a temporary directory for this thread's SubtitleEdit instance
    temp_dir = tempfile.mkdtemp(prefix='SubtitleEdit_')
    try:
        # Copy the SubtitleEdit directory to the temporary directory
        local_subtitleedit_dir = os.path.join(temp_dir, 'SubtitleEdit')
        shutil.copytree(subtitleedit_dir, local_subtitleedit_dir)

        subtitleedit_exe = os.path.join(local_subtitleedit_dir, 'SubtitleEdit.exe')
        subtitleedit_settings = os.path.join(local_subtitleedit_dir, 'Settings.xml')

        base_lang_id_name_forced, _, original_extension = file.rpartition('.')
        base_id_name_forced, _, language = base_lang_id_name_forced.rpartition('_')
        base_name_forced, _, track_id = base_id_name_forced.rpartition('_')
        base_forced, _, name_encoded = base_name_forced.rpartition('_')
        name_encoded = name_encoded.strip("'") if name_encoded.startswith("'") and name_encoded.endswith("'") else name_encoded
        name = base64.b64decode(name_encoded).decode("utf-8")
        base, _, forced = base_forced.rpartition('_')

        if limit_ocr_languages[0].lower() != 'none':
            if language not in limit_ocr_languages:
                final_subtitle = ''
                original_subtitle = file
                return original_subtitle, final_subtitle, language, track_id, name, forced, replacements, original_extension

        if file.endswith('.sup') or file.endswith('.sub'):
            update_tesseract_lang_xml(language, subtitleedit_settings)

            command = ["mono", subtitleedit_exe, "/convert", file, "srt", "/SplitLongLines", "/encoding:utf-8"]

            if debug:
                print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

            result_code = run_with_xvfb(command)

            output_subtitle = f"{base}_{forced}_'{name_encoded}'_{track_id}_{language}.srt"
            subtitle_tmp = f"{base}_{forced}_'{name_encoded}'_{track_id}_{language}_tmp.srt"

            if name:
                original_name_b64 = name_encoded
            else:
                original_name_b64 = base64.b64encode('Original'.encode("utf-8")).decode("utf-8")
                name = 'Original'

            if forced != '0' and bool(forced):
                output_name = f'non-{main_audio_track_lang} dialogue'
                output_name_b64 = base64.b64encode(output_name.encode("utf-8")).decode("utf-8")
                original_subtitle = f"{base}_0_'{original_name_b64}'_{track_id}_{language}.{original_extension}"
                final_subtitle = f"{base}_{forced}_'{output_name_b64}'_{track_id}_{language}.srt"
            else:
                output_name = ''
                output_name_b64 = base64.b64encode(output_name.encode("utf-8")).decode("utf-8")
                original_subtitle = f"{base}_{forced}_'{original_name_b64}'_{track_id}_{language}.{original_extension}"
                final_subtitle = f"{base}_{forced}_'{output_name_b64}'_{track_id}_{language}.srt"

            os.rename(file, original_subtitle)
            if os.path.exists(output_subtitle):
                os.rename(output_subtitle, final_subtitle)

            if result_code != 0:
                final_subtitle = 'ERROR'
                name = 'ERROR'
            if not is_valid_srt(final_subtitle) and result_code == 0:
                final_subtitle = 'ERROR'
                original_subtitle = 'ERROR'
                language = 'ERROR'
                name = 'ERROR'
                forced = 'ERROR'
                track_id = 'ERROR'
                original_extension = 'ERROR'

            if final_subtitle != 'ERROR':
                if language == 'eng':
                    current_replacements = find_and_replace(final_subtitle, 'scripts/replacements_eng_only.csv', subtitle_tmp)
                    replacements = replacements + current_replacements
                    current_replacements = find_and_replace(subtitle_tmp, 'scripts/replacements.csv', final_subtitle)
                    os.remove(subtitle_tmp)
                    replacements = replacements + current_replacements
                elif language == 'nor':
                    current_replacements = find_and_replace(final_subtitle, 'scripts/replacements_nor_only.csv', subtitle_tmp)
                    replacements = replacements + current_replacements
                    current_replacements = find_and_replace(subtitle_tmp, 'scripts/replacements.csv', final_subtitle)
                    os.remove(subtitle_tmp)
                    replacements = replacements + current_replacements
                else:
                    current_replacements = find_and_replace(final_subtitle, 'scripts/replacements.csv', subtitle_tmp)
                    os.rename(subtitle_tmp, final_subtitle)
                    replacements = replacements + current_replacements

            # Also rename .idx file if processing VobSub subtitles
            if file.endswith('.sub'):
                if forced != '0' and bool(forced):
                    os.rename(f"{base}_{forced}_'{name_encoded}'_{track_id}_{language}.idx",
                              f"{base}_0_'{original_name_b64}'_{track_id}_{language}.idx")
                else:
                    os.rename(f"{base}_{forced}_'{name_encoded}'_{track_id}_{language}.idx",
                              f"{base}_{forced}_'{original_name_b64}'_{track_id}_{language}.idx")
        else:
            final_subtitle = ''
            original_subtitle = file
    finally:
        # Clean up the temporary directory
        shutil.rmtree(temp_dir)

    return original_subtitle, final_subtitle, language, track_id, name, forced, replacements, original_extension


def get_subtitle_tracks_metadata_lists(subtitle_files, max_threads):

    # Prepare to track the results in the order they were submitted
    results = [None] * len(subtitle_files)  # Placeholder list for results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Submit all tasks and store futures in a dictionary with their index
        future_to_index = {
            executor.submit(get_subtitle_tracks_metadata_lists_worker, subtitle_files[i]): i
            for i in range(len(subtitle_files))
        }

        # As each future completes, store the result at the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()

    # Initialize lists for the structured results
    updated_subtitle_languages = []
    all_track_ids = []
    all_track_names = []
    all_track_forced = []
    updated_sub_filetypes = []

    # Process each result and organize the outputs
    for language, track_id, name, forced, original_extension in results:
        updated_sub_filetypes.append(original_extension)
        updated_subtitle_languages.append(language)
        all_track_ids.append(track_id)
        all_track_names.append(name)
        all_track_forced.append(forced)

    return (updated_subtitle_languages, all_track_ids, all_track_names,
            all_track_forced, updated_sub_filetypes)


def get_subtitle_tracks_metadata_lists_worker(file):
    base_lang_id_name_forced, _, original_extension = file.rpartition('.')
    base_id_name_forced, _, language = base_lang_id_name_forced.rpartition('_')
    base_name_forced, _, track_id = base_id_name_forced.rpartition('_')
    base_forced, _, name_encoded = base_name_forced.rpartition('_')
    name_encoded = name_encoded.strip("'") if name_encoded.startswith("'") and name_encoded.endswith("'") else name_encoded
    name = base64.b64decode(name_encoded).decode("utf-8")
    base, _, forced = base_forced.rpartition('_')

    return language, track_id, name, forced, original_extension


def update_tesseract_lang_xml(new_language, settings_file):
    # Parse XML file
    tree = ET.parse(settings_file)
    root = tree.getroot()

    for parent1 in root.findall('VobSubOcr'):
        target_elem = parent1.find('TesseractLastLanguage')
        if target_elem is not None:
            target_elem.text = new_language

    # Write back to file
    tree.write(settings_file)


def get_priority(subs_langs, lang):
    try:
        return subs_langs.index(lang)
    except ValueError:
        return len(subs_langs)


def get_wanted_subtitle_tracks(debug, file_info, pref_langs):
    if debug:
        print(f"\n{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_subtitle_tracks:\n")
        print(f"{BLUE}preferred subtitle languages{RESET}: {pref_langs}")

    remove_all_subtitles = check_config(config, 'subtitles', 'remove_all_subtitles')
    forced_subtitles_priority = check_config(config, 'subtitles', 'forced_subtitles_priority')
    main_audio_language_subs_only = check_config(config, 'subtitles', 'main_audio_language_subs_only')

    total_subs_tracks = 0
    pref_subs_langs = pref_langs

    subs_track_ids = []
    subs_track_languages = []
    subs_track_names = []
    subs_track_forced = []

    unmatched_subs_track_languages = []

    forced_track_ids = []
    forced_track_languages = []
    forced_track_names = []
    forced_sub_filetypes = []
    forced_sub_bool = []

    default_subs_track = -1
    all_sub_filetypes = []
    sub_filetypes = []
    srt_track_ids = []
    ass_track_ids = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False
    srt_ass_track_removed = []
    main_audio_track_lang = None

    missing_subs_langs = []

    # Get all subtitle codecs
    for track in file_info['tracks']:
        if track['type'] == 'subtitles':
            all_sub_filetypes.append(track['codec'])

    # Get the main audio language
    for track in file_info['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    language = pycountry.languages.get(alpha_3=value)
                    if language:
                        main_audio_track_lang = language.name

    # Check for matching subs languages
    for track in file_info["tracks"]:
        if track["type"] == "subtitles":
            track_language = ''
            for key, value in track["properties"].items():
                if key == 'language':
                    track_language = value
            if track_language == 'nob' or track_language == 'nno':
                track_language = 'nor'
            if track_language in pref_subs_langs:
                subs_track_languages.append(track_language)
            else:
                unmatched_subs_track_languages.append(track_language)

    # Check for subs languages that are wanted, but missing in file
    if pref_subs_langs:
        all_sub_langs = []
        for track in file_info["tracks"]:
            if track["type"] == "subtitles":
                track_language = ''
                for key, value in track["properties"].items():
                    if key == 'language':
                        all_sub_langs.append(value)
        for lang in pref_subs_langs:
            if not lang in all_sub_langs:
                if lang == 'nob' and 'nor' in all_sub_langs or lang == 'nor' and 'nob' in all_sub_langs:
                    pass
                else:
                    missing_subs_langs.append(lang)
    # If no sub langs are missing, set to "none", as a value is needed
    if not missing_subs_langs:
        missing_subs_langs = ['none']
    else:
        needs_processing = True

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
            forced_track_val = 0

            for key, value in track["properties"].items():
                if key == 'language':
                    track_language = value
                if key == 'forced_track':
                    forced_track_val = value
                    forced_track_val = 1 if forced_track_val else 0
                if key == 'track_name':
                    track_name = value

            if forced_track_val or "forced" in track_name.lower():
                forced_track = True
                forced_track_val = 1
            else:
                forced_track = False

            if track_language == 'nob' or track_language == 'nno':
                track_language = 'nor'

            if track_language in pref_subs_langs:
                needs_processing = True
                needs_sdh_removal = True

                if forced_track:
                    forced_track_ids.append(track["id"])
                    forced_track_languages.append(track_language)
                    if track["codec"] == "HDMV PGS":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sup')
                        forced_sub_bool.append(forced_track_val)
                    elif track["codec"] == "VobSub":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sub')
                        forced_sub_bool.append(forced_track_val)
                    elif track["codec"] == "SubRip/SRT":
                        forced_track_names.append(f'non-{main_audio_track_lang} dialogue')
                        forced_sub_bool.append(forced_track_val)
                        forced_sub_filetypes.append('srt')
                    elif track["codec"] == "SubStationAlpha":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('ass')
                        forced_sub_bool.append(forced_track_val)

                # If the track language is "und" (undefined), assume english subtitles
                if track_language.lower() == "und":
                    track_language = 'eng'
                    # Remove 'eng' from missing subs lang, as it was
                    # previously set as "und".
                    missing_subs_langs.remove('eng')
                    pref_subs_langs.append('eng')

                if subs_track_languages.count(track_language) == 0 and not forced_track:
                    if track["codec"] == "HDMV PGS":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        subs_track_forced.append(forced_track_val)
                        sub_filetypes.append('sup')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "VobSub":
                        # If VobSub is the only subtitle type in the file (DVD), keep it.
                        # If it is a mix of Vobsub and PGS (BluRay), only the PGS should be kept.
                        if not any(codec == "HDMV PGS" for codec in all_sub_filetypes):
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            subs_track_forced.append(forced_track_val)
                            sub_filetypes.append('sub')
                            needs_convert = True
                            needs_processing = True
                    elif track["codec"] == "SubRip/SRT":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        subs_track_forced.append(forced_track_val)
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        subs_track_forced.append(forced_track_val)
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if track["codec"] != "SubRip/SRT" and subs_track_languages.count(track_language) == 1:
                        if 'srt' in sub_filetypes:
                            for index, lang in enumerate(subs_track_languages):
                                if lang == track_language:
                                    sub_filetypes.pop(index)
                                    subs_track_languages.pop(index)
                                    subs_track_ids.pop(index)
                                    subs_track_names.pop(index)
                                    subs_track_forced.pop(index)
                        if track["codec"] == "HDMV PGS":
                            if sub_filetypes:
                                if sub_filetypes[-1] != 'sup':
                                    subs_track_forced.append(forced_track_val)
                                    sub_filetypes.append('sup')
                                    subs_track_ids.append(track["id"])
                                    subs_track_languages.append(track_language)
                                    subs_track_names.append(track_name)
                            elif not sub_filetypes:
                                subs_track_forced.append(forced_track_val)
                                sub_filetypes.append('sup')
                                subs_track_ids.append(track["id"])
                                subs_track_languages.append(track_language)
                                subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

                        elif track["codec"] == "SubStationAlpha":
                            if sub_filetypes:
                                if sub_filetypes[-1] != 'ass':
                                    subs_track_forced.append(forced_track_val)
                                    sub_filetypes.append('ass')
                                    subs_track_ids.append(track["id"])
                                    subs_track_languages.append(track_language)
                                    subs_track_names.append(track_name)
                            elif not sub_filetypes:
                                subs_track_forced.append(forced_track_val)
                                sub_filetypes.append('ass')
                                subs_track_ids.append(track["id"])
                                subs_track_languages.append(track_language)
                                subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

                        elif track["codec"] == "VobSub":
                            subs_track_forced.append(forced_track_val)
                            sub_filetypes.append('sub')
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

    # Add the forced subtitle tracks
    if forced_subtitles_priority.lower() == 'last':
        subs_track_ids = subs_track_ids + forced_track_ids
        subs_track_languages = subs_track_languages + forced_track_languages
        subs_track_names = subs_track_names + forced_track_names
        sub_filetypes = sub_filetypes + forced_sub_filetypes
        subs_track_forced = subs_track_forced + forced_sub_bool
    else:
        subs_track_ids = forced_track_ids + subs_track_ids
        subs_track_languages = forced_track_languages + subs_track_languages
        subs_track_names = forced_track_names + subs_track_names
        sub_filetypes = forced_sub_filetypes + sub_filetypes
        subs_track_forced = forced_sub_bool + subs_track_forced

    # If none of the subtitles matched, add the forced tracks as a last effort
    if len(subs_track_ids) == 0:
        subs_track_ids = forced_track_ids
        subs_track_languages = forced_track_languages
        subs_track_names = forced_track_names
        sub_filetypes = forced_sub_filetypes
        subs_track_forced = forced_sub_bool

    if subs_track_ids:
        # If subs language prefs have not been set, set the list
        # to the sub languages that have been matched as fallback
        if not pref_subs_langs and subs_track_languages:
            pref_subs_langs = subs_track_languages

        paired = zip(subs_track_languages, sub_filetypes, subs_track_forced, subs_track_ids, subs_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(pref_subs_langs, x[0]))
        sorted_subs_languages, sorted_subs_filetypes, sorted_subs_track_forced, sorted_subs_track_ids, sorted_subs_track_names = zip(*sorted_paired)

        subs_track_languages = list(sorted_subs_languages)
        sub_filetypes = list(sorted_subs_filetypes)
        subs_track_forced = list(sorted_subs_track_forced)
        subs_track_ids = list(sorted_subs_track_ids)
        subs_track_names = list(sorted_subs_track_names)

    # Sets the default subtitle track to first entry in preferences,
    # reverts to any entry if not first
    for track_id, lang in zip(subs_track_ids, subs_track_languages):
        if lang == pref_subs_langs[0]:
            default_subs_track = track_id
            break
        elif lang in pref_subs_langs:
            default_subs_track = track_id
            break

    # Remove any subtitles that do not match the main audio language
    if main_audio_language_subs_only:
        main_audio_track_lang = get_main_audio_track_language_3_letter(file_info)
        # Filter each subtitle list to only include items matching `main_audio_track_lang`
        if subs_track_languages:
            filtered_subs = [
                (lang, filetype, forced, track_id, name)
                for lang, filetype, forced, track_id, name in zip(
                    subs_track_languages, sub_filetypes, subs_track_forced, subs_track_ids, subs_track_names
                ) if lang == main_audio_track_lang
            ]

            if filtered_subs:
                # Unzip the filtered lists back into separate variables
                (subs_track_languages, sub_filetypes, subs_track_forced,
                 subs_track_ids, subs_track_names) = map(list, zip(*filtered_subs))
            else:
                # Clear lists if no subtitles match the main audio language
                subs_track_languages = []
                sub_filetypes = []
                subs_track_forced = []
                subs_track_ids = []
                subs_track_names = []
        # Filter `missing_subs_langs` to only include languages matching `main_audio_track_lang`
        if missing_subs_langs:
            missing_subs_langs = [lang for lang in missing_subs_langs if lang == main_audio_track_lang]

    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True

    if remove_all_subtitles:
        needs_processing = True
        subs_track_ids = []
        default_subs_track = -1
        sub_filetypes = []
        subs_track_languages = []
        subs_track_names = []
        subs_track_forced = []
        missing_subs_langs = ['none']

    if debug:
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"{BLUE}needs SDH removal{RESET}: {needs_sdh_removal}")
        print(f"{BLUE}needs to be converted{RESET}: {needs_convert}")
        print(f"\n{BLUE}all wanted subtitle track ids{RESET}: {subs_track_ids}")
        print(f"{BLUE}missing subtitle langs{RESET}: {missing_subs_langs}")
        print(f"{BLUE}default subtitle track id{RESET}: {default_subs_track}")
        print(f"{BLUE}subtitle tracks to be extracted{RESET}:\n  {BLUE}filetypes{RESET}: {sub_filetypes}, "
              f"{BLUE}langs{RESET}: {subs_track_languages}, {BLUE}names{RESET}: {subs_track_names}, "
              f"{BLUE}forced{RESET}: {subs_track_forced}")

    return (subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert,
            sub_filetypes, subs_track_languages, subs_track_names, needs_processing,
            subs_track_forced, missing_subs_langs)
