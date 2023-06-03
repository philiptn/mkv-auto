import csv
import re
from subtitle_filter import Subtitles
import asstosrt
import os


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


def remove_sdh(input_files):
    print(f"[SRT] Removing SDH in subtitles...")
    for index, input_file in enumerate(input_files):
        subs = Subtitles(input_file)
        subs.filter()
        subs.save()


def convert_ass_to_srt(subtitle_files, languages):
    print(f"[SSA] Converting SSA subtitles to SRT...")
    output_subtitles = []
    updated_subtitle_languages = languages

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')

        ass_file = open(file)
        asstosrt.convert(ass_file)
        os.remove(file)

        output_subtitles.append(f"{base}.srt")
        updated_subtitle_languages.insert(0, languages[index])

    return output_subtitles, updated_subtitle_languages
