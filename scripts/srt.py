from subtitle_filter import Subtitles
import asstosrt
import autosubsync
import os
import subprocess
import pysrt
from datetime import datetime


def get_timestamp():
	"""Return the current UTC timestamp in the desired format."""
	current_time = datetime.utcnow()
	return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def remove_sdh(input_files, quiet, remove_music):
    if not quiet:
        print(f"[UTC {get_timestamp()}] [SRT] Removing SDH in subtitles...")
    for index, input_file in enumerate(input_files):

        if remove_music:
            # Remove all music lines completely
            subs = pysrt.open(input_file)
            for sub in subs:
                if '♪' in sub.text:
                    sub.text = ''
            subs.save('.tmp.srt', encoding='utf-8')
            os.remove(input_file)
            os.rename('.tmp.srt', input_file)

        subs = Subtitles(input_file)
        subs.filter(rm_music=remove_music)
        subs.save()

        # Removing any all-uppercase letters from
        # improperly formatted SDH subtitles
        subs = pysrt.open(input_file)
        for sub in subs:
            if sub.text.isupper():
                sub.text = ''
        subs.save('.tmp.srt', encoding='utf-8')
        os.remove(input_file)
        os.rename('.tmp.srt', input_file)


def convert_ass_to_srt(subtitle_files, languages):
    print(f"[UTC {get_timestamp()}] [ASS] Converting ASS subtitles to SRT...")
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
        print(f"[UTC {get_timestamp()}] [SRT] Synchronizing subtitles to audio track (ai)...")

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
        print(f"[UTC {get_timestamp()}] [SRT] Synchronizing subtitles to audio track (fast)...")

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
