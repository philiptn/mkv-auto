import csv
import re
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def run_with_xvfb(command):
    xvfb_cmd = ["Xvfb", ":99", "-screen", "0", "1024x768x24"]

    # Start Xvfb in the background
    xvfb_process = subprocess.Popen(xvfb_cmd)
    # Wait for the Xvfb process to initialize
    time.sleep(1)

    env = os.environ.copy()
    env['DISPLAY'] = ':99'

    result = subprocess.run(command, env=env, capture_output=True, text=True)

    # Kill the Xvfb process after we're done
    xvfb_process.terminate()

    if result.returncode != 0:
        raise Exception("Error executing command: " + result.stderr)
    return result


def find_and_replace(input_files):
    # For quick reference:
    # Special Regex Characters: These characters have special
    # meaning in regex: ., +, *, ?, ^, $, (, ), [, ], {, }, |, \.
    for index, input_file in enumerate(input_files):
        # Open SRT and replacement files
        with open(input_file, 'r') as file:
            data = file.read()
        with open('scripts/replacements.csv', 'r') as file:
            reader = csv.reader(file)
            replacements = list(reader)

        # Perform the find and replace operations
        for find, replace in replacements:
            data = re.sub(find, replace, data)

        # Write the modified content back to the file
        with open(input_file, 'w') as file:
            file.write(data)


##############################################
# Deprecated due to OCR problems with pgsrip #
##############################################
def ocr_pgs_subtitles(subtitle_files, languages):
    print(f"[UTC {get_timestamp()}] [OCR] Performing OCR on PGS subtitles...")
    output_subtitles = []
    generated_srt_files = []
    replaced_index = 0
    updated_subtitle_languages = languages

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')
        env = os.environ.copy()
        env['TESSDATA_PREFIX'] = os.path.expanduser('~/.mkv-auto/tessdata')

        command = ["pgsrip", "--debug", "--tag", "ocr", "--language",
                   languages[index + replaced_index], file]

        # result = subprocess.run(command, capture_output=True, text=True, env=env)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing pgsrip command: " + result.stdout)

        output_subtitles.append(f"{base}.srt")
        generated_srt_files.append('srt')
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    # Fix common OCR errors
    find_and_replace(output_subtitles)

    return output_subtitles, updated_subtitle_languages, generated_srt_files


def ocr_subtitles(subtitle_files, languages):
    print(f"[UTC {get_timestamp()}] [OCR] Performing OCR on subtitles...")

    tessdata_location = '~/.mkv-auto/'
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    output_subtitles = []
    generated_srt_files = []
    replaced_index = 0
    updated_subtitle_languages = languages

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')

        update_tesseract_lang_xml(languages[index + replaced_index])
        command = ["mono", subtitleedit, "/convert", file,
                   "srt", "/FixCommonErrors", "/encoding:utf-8"]
        run_with_xvfb(command)

        output_subtitles.append(f"{base}.srt")
        generated_srt_files.append('srt')
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    # Fix common OCR errors
    find_and_replace(output_subtitles)

    return output_subtitles, updated_subtitle_languages, generated_srt_files


def update_tesseract_lang_xml(new_language):
    se_settings = 'utilities/SubtitleEdit/Settings.xml'
    # Parse XML file
    tree = ET.parse(se_settings)
    root = tree.getroot()

    for parent1 in root.findall('VobSubOcr'):
        target_elem = parent1.find('TesseractLastLanguage')
        if target_elem is not None:
            target_elem.text = new_language

    # Write back to file
    tree.write(se_settings)
