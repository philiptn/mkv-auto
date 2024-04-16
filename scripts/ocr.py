import csv
import re
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
import concurrent.futures
import random
import threading
import tempfile
import shutil
from collections import Counter

# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'
RED = '\033[31m'
GREEN = '\033[32m'

max_workers = int(os.cpu_count() * 0.7)  # Use 70% of the CPU cores

# Define a global lock
xml_file_lock = threading.Lock()


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


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
