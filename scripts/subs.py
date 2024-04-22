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

# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'
RED = '\033[31m'
GREEN = '\033[32m'

max_workers = int(os.cpu_count() * 0.8)  # Use 80% of the CPU cores

# Define a global lock
xml_file_lock = threading.Lock()


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


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def clean_invalid_utf8(input_file, output_file):
    # Read the file, replacing invalid characters with '�'
    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Write the cleaned content back
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)


def find_and_replace(input_file, replacement_file):
    with open(input_file, 'r') as file:
        data = file.read()

    changes = []  # List to hold before/after strings for each replacement
    with open(replacement_file, 'r') as file:
        reader = csv.reader(file)
        replacements = list(reader)

    # Apply each replacement in the file data
    for find, replace in replacements:
        # This regex will find the word with some context
        pattern = re.compile(r'(\b.{0,30}\b)?(' + re.escape(find) + r')(\b.{0,30}\b)?')
        for match in pattern.finditer(data):
            before_context = match.group(1) or ''  # context before the match
            matched_text = match.group(2)  # the actual text that will be replaced
            after_context = match.group(3) or ''  # context after the match

            before_snippet = f"{before_context}{matched_text}{after_context}"
            after_snippet = f"{before_context}{replace}{after_context}"

            changes.append(f"{GREY}found{RESET}: '{RED}{before_snippet}{RESET}', "
                           f"{GREY}replaced{RESET}: '{GREEN}{after_snippet}{RESET}'")

        # Replace in data to keep it updated
        data = pattern.sub(f'\g<1>{replace}\g<3>', data)

    # Write the modified content back to the file
    with open(input_file, 'w') as file:
        file.write(data)

    return changes


def find_available_display():
    while True:
        display_number = random.randint(100, 1000)  # Adjust the range as necessary
        lock_file = f"/tmp/.X11-unix/X{display_number}"
        if not os.path.exists(lock_file):
            return display_number


def run_with_xvfb(command):
    display_number = find_available_display()
    xvfb_cmd = ["Xvfb", f":{display_number}", "-screen", "0", "1024x768x24"]

    # Start Xvfb in the background
    xvfb_process = subprocess.Popen(xvfb_cmd)
    time.sleep(2)  # Allow time for Xvfb to start

    env = os.environ.copy()
    env['DISPLAY'] = f":{display_number}"
    result = subprocess.run(command, env=env, capture_output=True, text=True)

    xvfb_process.terminate()
    xvfb_process.wait()

    if result.returncode != 0:
        raise Exception(f"Error executing command: {result.stderr}")

    return result


def remove_sdh_worker(debug, input_file, remove_music, subtitleedit):
    command = ["mono", subtitleedit, "/convert", input_file,
               "srt", "/SplitLongLines", "/encoding:utf-8", "/RemoveTextForHI",
               f"/outputfilename:{input_file}_tmp.srt"]

    replacements = []

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

        replacements = replacements + find_and_replace(input_file, 'scripts/replacements_srt_only.csv')

    return replacements


def remove_sdh(debug, input_files, quiet, remove_music, track_names, external_sub):
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    all_replacements = []
    cleaned_track_names = []
    subs_print = "[SRT_EXT]" if external_sub else "[SUBTITLES]"

    if not quiet:
        print(f"{GREY}[UTC {get_timestamp()}] {subs_print}{RESET} Removing SDH in SRT subtitles...")

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
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
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} During processing, the following words were fixed:")
        print('')
        for replacement in all_replacements:
            print(replacement)
        print('')

    return cleaned_track_names


def convert_ass_to_srt(subtitle_files, languages, names):
    print(f"{GREY}[UTC {get_timestamp()}] [ASS]{RESET} Converting ASS subtitles to SRT...")
    output_subtitles = []
    updated_subtitle_languages = []
    updated_sub_filetypes = []
    all_track_ids = []
    all_track_names = []

    for index, file in enumerate(subtitle_files):
        base_and_lang_with_id, _, original_extension = file.rpartition('.')
        base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
        base, _, track_id = base_with_id.rpartition('.')

        if "ass" in file:
            ass_file = open(file)
            srt_output = asstosrt.convert(ass_file)
            with open(f"{base}.{track_id}.{lang}.srt", "w") as srt_file:
                srt_file.write(srt_output)
            updated_sub_filetypes = ['srt', original_extension] + updated_sub_filetypes
            all_track_ids = [track_id, track_id] + all_track_ids
            all_track_names = ['', names[index] if names[index] else "Original"] + all_track_names
            updated_subtitle_languages = [languages[index], languages[index]] + updated_subtitle_languages
            output_subtitles = [f"{base}.{track_id}.{lang}.srt"] + output_subtitles
        else:
            updated_sub_filetypes.append(original_extension)
            output_subtitles.append(file)
            updated_subtitle_languages.append(languages[index])
            all_track_ids.append(track_id)
            all_track_names.append(names[index] if names[index] else "Original")

    return output_subtitles, updated_subtitle_languages, all_track_ids, all_track_names, updated_sub_filetypes


def resync_srt_subs(debug, input_file, subtitle_files, quiet, external_sub):
    sync_print = "[SRT_EXT]" if external_sub else "[FFSUBSYNC]"

    if not quiet:
        print(f"{GREY}[UTC {get_timestamp()}] {sync_print}{RESET} Synchronizing subtitles to audio track...")

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list of tasks for each subtitle file
        tasks = [executor.submit(resync_srt_subs_worker, debug, input_file, subfile, quiet, max_retries=3, retry_delay=2)
                 for subfile in subtitle_files]
        # Wait for all tasks to complete
        for task in concurrent.futures.as_completed(tasks):
            try:
                task.result()  # This will re-raise any exception from the thread
            except Exception as e:
                print(f"Error processing subtitle: {e}")
    if debug:
        print('')


def resync_srt_subs_worker(debug, input_file, subtitle_filename, quiet, max_retries, retry_delay):
    base, _, _ = subtitle_filename.rpartition('.')
    base_nolang, _, _ = base.rpartition('.')
    temp_filename = f"{base_nolang}_tmp.srt"

    command = ["ffs", input_file, "--vad", "webrtc", "-i", subtitle_filename, "-o", temp_filename]

    retries = 0
    while retries < max_retries:
        if debug and not quiet:
            print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            # Success, move the file and exit the loop
            os.remove(subtitle_filename)
            shutil.move(temp_filename, subtitle_filename)
            break
        else:
            retries += 1
            if retries >= max_retries:
                # Exceeded the maximum number of retries, raise an exception
                raise Exception(f"Error executing FFsubsync command: {result.stderr}")
            time.sleep(retry_delay)  # Wait before retrying


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


def ocr_subtitle_worker(debug, file, language, name, subtitleedit_dir):
    replacements = []
    # Create a temporary directory for this thread's SubtitleEdit instance
    temp_dir = tempfile.mkdtemp(prefix='SubtitleEdit_')
    try:
        # Copy the SubtitleEdit directory to the temporary directory
        local_subtitleedit_dir = os.path.join(temp_dir, 'SubtitleEdit')
        shutil.copytree(subtitleedit_dir, local_subtitleedit_dir)

        subtitleedit_exe = os.path.join(local_subtitleedit_dir, 'SubtitleEdit.exe')
        subtitleedit_settings = os.path.join(local_subtitleedit_dir, 'Settings.xml')

        base_and_lang_with_id, _, original_extension = file.rpartition('.')
        base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
        base, _, track_id = base_with_id.rpartition('.')

        if "sup" in file or "sub" in file:
            update_tesseract_lang_xml(language, subtitleedit_settings)

            command = ["mono", subtitleedit_exe, "/convert", file, "srt", "/SplitLongLines", "/encoding:utf-8"]

            if debug:
                print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

            run_with_xvfb(command)

            output_subtitle = f"{base}.{track_id}.{lang}.srt"

            if language == 'eng':
                replacements = replacements + find_and_replace(output_subtitle, 'scripts/replacements_eng_only.csv')
            replacements = replacements + find_and_replace(output_subtitle, 'scripts/replacements.csv')
        else:
            output_subtitle = ''
    finally:
        # Clean up the temporary directory
        shutil.rmtree(temp_dir)

    return file, output_subtitle, language, track_id, name, replacements, original_extension


def ocr_subtitles(debug, subtitle_files, languages, names):
    print(f"{GREY}[UTC {get_timestamp()}] [OCR]{RESET} Converting picture-based subtitles to SRT...")

    subtitleedit_dir = 'utilities/SubtitleEdit'
    all_replacements = []

    if debug:
        print('')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(ocr_subtitle_worker, debug, file, languages[index], names[index],
                                          subtitleedit_dir): index
                          for index, file in enumerate(subtitle_files)}

        output_subtitles = []
        updated_subtitle_languages = []
        all_track_ids = []
        all_track_names = []
        updated_sub_filetypes = []

        for future in concurrent.futures.as_completed(future_to_file):
            original_file, output_subtitle, language, track_id, name, replacements, original_extension = future.result()
            all_replacements = all_replacements + replacements
            if output_subtitle:
                updated_sub_filetypes = ['srt', original_extension] + updated_sub_filetypes
                # Add both original and generated subtitles to the output list
                output_subtitles = [output_subtitle] + output_subtitles
                # Repeat language and track ID for both original and generated files
                updated_subtitle_languages = [language, language] + updated_subtitle_languages
                all_track_ids = [track_id, track_id] + all_track_ids
                all_track_names = ['', name if name else "Original"] + all_track_names
            else:
                updated_sub_filetypes.append(original_extension)
                output_subtitles.append(original_file)
                updated_subtitle_languages.append(language)
                all_track_ids.append(track_id)
                all_track_names.append(name if name else '')

    if debug:
        print('')

    if all_replacements and debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} During OCR, the following words were fixed:\n")

        replacements_counter = Counter(all_replacements)

        for replacement, count in replacements_counter.items():
            if count > 1:
                print(f"{replacement} {GREY}({count} times){RESET}")
            else:
                print(replacement)
        print('')

    return output_subtitles, updated_subtitle_languages, all_track_ids, all_track_names, updated_sub_filetypes


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
