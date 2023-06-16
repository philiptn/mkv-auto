import csv
import re
from subtitle_filter import Subtitles
import asstosrt
import autosubsync
import os
import subprocess
import pysrt


# In development, not currently used
def find_and_replace_srt(input_files):
    for index, input_file in enumerate(input_files):
        # Open SRT and replacement files
        with open(input_file, 'r') as file:
            data = file.read()
        with open('replacements.csv', 'r') as file:
            reader = csv.reader(file)
            replacements = list(reader)

        # Perform the find and replace operations
        for find, replace in replacements:
            data = re.sub(find, replace, data)

        # Write the modified content back to the file
        with open(input_file, 'w') as file:
            file.write(data)


def remove_sdh(input_files, quiet):
    if not quiet:
        print(f"[SRT] Removing SDH in subtitles...")
    for index, input_file in enumerate(input_files):
        subs = Subtitles(input_file)
        subs.filter()
        subs.save()

        # Removing any all-uppercase letters from
        # improperly formatted SDH subtitles
        subs = pysrt.open(input_file)
        # Loop through the subtitles
        for sub in subs:
            # Check if the subtitle text is all in uppercase
            if sub.text.isupper():
                # If it's all uppercase, replace the text with an empty line
                sub.text = ''
        # Save the modified subtitles back to an SRT file
        subs.save('.tmp.srt', encoding='utf-8')

        os.remove(input_file)
        os.rename('.tmp.srt', input_file)


def convert_ass_to_srt(subtitle_files, languages):
    print(f"[ASS] Converting ASS subtitles to SRT...")
    output_subtitles = []
    updated_subtitle_languages = languages
    replaced_index = 0
    generated_srt_files = []

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')

        ass_file = open(file)
        srt_output = asstosrt.convert(ass_file)
        with open(f"{base}.srt", "w") as srt_file:
            srt_file.write(srt_output)
        generated_srt_files.append('srt')

        output_subtitles.append(f"{base}.srt")
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    return output_subtitles, updated_subtitle_languages, generated_srt_files


def resync_srt_subs_ai(input_file, subtitle_files, quiet):
    if not quiet:
        print(f"[SRT] Synchronizing subtitles to audio track (ai)...")

    for index, subfile in enumerate(subtitle_files):
        base, _, extension = subfile.rpartition('.')
        base_nolang, _, extension = base.rpartition('.')
        subtitle_filename = subfile
        temp_filename = f"{base_nolang}_tmp.srt"

        autosubsync.synchronize(input_file, subtitle_filename, temp_filename)

        os.remove(subtitle_filename)
        os.rename(temp_filename, subtitle_filename)


def resync_srt_subs_fast(input_file, subtitle_files, quiet):
    if not quiet:
        print(f"[SRT] Synchronizing subtitles to audio track (fast)...")

    for index, subfile in enumerate(subtitle_files):
        base, _, extension = subfile.rpartition('.')
        base_nolang, _, extension = base.rpartition('.')
        subtitle_filename = subfile
        temp_filename = f"{base_nolang}_tmp.srt"

        command = ["ffs", input_file, "-i", subtitle_filename,
                   "-o", temp_filename]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing FFsubsync command: " + result.stderr)

        os.remove(subtitle_filename)
        os.rename(temp_filename, subtitle_filename)
