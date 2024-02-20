from subtitle_filter import Subtitles
import asstosrt
import autosubsync
import os
import subprocess
import pysrt
import shutil
from datetime import datetime
import time
import csv
import re


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
    # For quick reference:
    # Special Regex Characters: These characters have special
    # meaning in regex: ., +, *, ?, ^, $, (, ), [, ], {, }, |, \.

    # Open SRT and replacement files
    with open(input_file, 'r') as file:
        data = file.read()
    with open(replacement_file, 'r') as file:
        reader = csv.reader(file)
        replacements = list(reader)

    # Perform the find and replace operations
    for find, replace in replacements:
        data = re.sub(find, replace, data)

    # Write the modified content back to the file
    with open(input_file, 'w') as file:
        file.write(data)


def run_with_xvfb(command):
    xvfb_cmd = ["Xvfb", ":99", "-screen", "0", "1024x768x24"]

    # Start Xvfb in the background
    xvfb_process = subprocess.Popen(xvfb_cmd)
    # Wait for the Xvfb process to initialize
    time.sleep(2)

    env = os.environ.copy()
    env['DISPLAY'] = ':99'

    result = subprocess.run(command, env=env, capture_output=True, text=True)

    # Kill the Xvfb process after we're done
    xvfb_process.terminate()
    time.sleep(2)

    if result.returncode != 0:
        raise Exception("Error executing command: " + result.stderr)
    return result


def remove_sdh(input_files, quiet, remove_music):
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    if not quiet:
        print(f"[UTC {get_timestamp()}] [SUBTITLES] Removing SDH in SRT subtitles...")
    for index, input_file in enumerate(input_files):

        command = ["mono", subtitleedit, "/convert", input_file,
                   "srt", "/FixCommonErrors", "/encoding:utf-8",
                   "/BalanceLines", "/RemoveTextForHI",
                   f"/outputfilename:{input_file}_tmp.srt"]
        run_with_xvfb(command)
        os.remove(input_file)
        shutil.move(f"{input_file}_tmp.srt", input_file)

        if remove_music:
            # Fix any encoding errors
            clean_invalid_utf8(input_file, '.tmp.srt')
            os.remove(input_file)
            shutil.move('.tmp.srt', input_file)

            # Remove all music lines completely
            subs = pysrt.open(input_file)
            for sub in subs:
                if '♪' in sub.text:
                    sub.text = ''
            subs.save('.tmp.srt', encoding='utf-8')
            os.remove(input_file)
            shutil.move('.tmp.srt', input_file)

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

        # Fix any encoding errors
        clean_invalid_utf8(input_file, '.tmp.srt')
        os.remove(input_file)
        shutil.move('.tmp.srt', input_file)

        subs = pysrt.open(input_file)
        for sub in subs:
            if sub.text.isupper():
                sub.text = ''
        subs.save('.tmp.srt', encoding='utf-8')
        os.remove(input_file)
        shutil.move('.tmp.srt', input_file)

        # Replace unwanted characters or existing OCR errors
        find_and_replace(input_file, 'scripts/replacements.csv')


def convert_ass_to_srt(subtitle_files, languages):
    print(f"[UTC {get_timestamp()}] [ASS] Converting ASS subtitles to SRT...")
    output_subtitles = []
    updated_subtitle_languages = languages
    replaced_index = 0
    generated_srt_files = []
    all_track_ids = []

    for index, file in enumerate(subtitle_files):
        base_and_lang_with_id, _, extension = file.rpartition('.')
        base_with_id, _, lang = base_and_lang_with_id.rpartition('.')
        base, _, track_id = base_with_id.rpartition('.')
        all_track_ids.append(track_id)

        ass_file = open(file)
        srt_output = asstosrt.convert(ass_file)
        with open(f"{base}.{track_id}.{lang}.srt", "w") as srt_file:
            srt_file.write(srt_output)
        generated_srt_files.append('srt')

        output_subtitles.append(f"{base}.{track_id}.{lang}.srt")
        all_track_ids.append(track_id)
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    return output_subtitles, updated_subtitle_languages, generated_srt_files, all_track_ids


def resync_srt_subs_ai(input_file, subtitle_files, quiet):
    if not quiet:
        print(f"[UTC {get_timestamp()}] [AUTOSUBSYNC] Synchronizing subtitles to audio track (ai)...")

    for index, subfile in enumerate(subtitle_files):
        base, _, extension = subfile.rpartition('.')
        base_nolang, _, extension = base.rpartition('.')
        subtitle_filename = subfile
        temp_filename = f"{base_nolang}_tmp.srt"

        autosubsync.synchronize(input_file, subtitle_filename, temp_filename)

        os.remove(subtitle_filename)
        shutil.move(temp_filename, subtitle_filename)


def resync_srt_subs_fast(input_file, subtitle_files, quiet):
    if not quiet:
        print(f"[UTC {get_timestamp()}] [FFSUBSYNC] Synchronizing subtitles to audio track (fast)...")

    for index, subfile in enumerate(subtitle_files):
        base, _, extension = subfile.rpartition('.')
        base_nolang, _, extension = base.rpartition('.')
        subtitle_filename = subfile
        temp_filename = f"{base_nolang}_tmp.srt"

        command = ["ffs", input_file, "--vad", "webrtc",
                   "-i", subtitle_filename, "-o", temp_filename]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing FFsubsync command: " + result.stderr)

        os.remove(subtitle_filename)
        shutil.move(temp_filename, subtitle_filename)
