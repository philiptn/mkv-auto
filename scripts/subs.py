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
    xvfb_cmd = ["Xvfb", f":{display_number}", "-screen", "0", "1024x768x24"
                "-ac", "-nolisten", "tcp", "-nolisten", "unix"]

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
    if all_replacements and not debug:
        if len(input_files) > 0:
            track_str = "tracks"
        else:
            track_str = "track"
        print(f"{GREY}[UTC {get_timestamp()}] {subs_print}{RESET} Fixed {len(all_replacements)} words in subtitle {track_str}.")

    return cleaned_track_names


def convert_ass_to_srt(subtitle_files, languages, names, main_audio_track_lang):
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
            updated_sub_filetypes = updated_sub_filetypes + ['srt', original_extension]
            all_track_ids = all_track_ids + [track_id, track_id]
            if 'forced' in names[index].lower():
                all_track_names = all_track_names + [f'non-{main_audio_track_lang} dialogue',
                                   names[index] if names[index] else '']
            else:
                all_track_names = all_track_names + ['', names[index] if names[index] else '']
            updated_subtitle_languages = updated_subtitle_languages + [languages[index], languages[index]]
            output_subtitles = output_subtitles + [f"{base}.{track_id}.{lang}.srt"]
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

    results = [None] * len(track_numbers)  # Pre-allocate a list for the results in order
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Create a dictionary to store futures with their respective indices
        future_to_index = {
            executor.submit(extract_subtitle, debug, filename, track, filetype, language): i
            for i, (track, filetype, language) in enumerate(zip(track_numbers, output_filetypes, subs_languages))
        }

        # As each future completes, place the result in the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()  # Store the result at the correct index

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


def ocr_subtitles(debug, subtitle_files, languages, names, main_audio_track_lang):
    print(f"{GREY}[UTC {get_timestamp()}] [OCR]{RESET} Converting picture-based subtitles to SRT...")

    subtitleedit_dir = 'utilities/SubtitleEdit'
    all_replacements = []

    if debug:
        print('')

    # Prepare to track the results in the order they were submitted
    results = [None] * len(subtitle_files)  # Placeholder list for results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks and store futures in a dictionary with their index
        future_to_index = {
            executor.submit(ocr_subtitle_worker, debug, subtitle_files[i], languages[i], names[i],
                            subtitleedit_dir): i
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
    updated_sub_filetypes = []

    # Process each result and organize the outputs
    for original_file, output_subtitle, language, track_id, name, replacements, original_extension in results:
        all_replacements = replacements + all_replacements
        if output_subtitle:
            updated_sub_filetypes = updated_sub_filetypes + ['srt', original_extension]
            # Add both original and generated subtitles to the output list
            output_subtitles = output_subtitles + [output_subtitle]
            # Repeat language and track ID for both original and generated files
            updated_subtitle_languages = updated_subtitle_languages + [language, language]
            all_track_ids = all_track_ids + [track_id, track_id]
            if 'forced' in name.lower():
                all_track_names = all_track_names + [f'non-{main_audio_track_lang} dialogue',
                                                     name if name else "Original"]
            else:
                all_track_names = all_track_names + ['', name if name else "Original"]
        else:
            updated_sub_filetypes.append(original_extension)
            output_subtitles.append(original_file)
            updated_subtitle_languages.append(language)
            all_track_ids.append(track_id)
            all_track_names.append(name if name else '')

    if debug and all_replacements:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} During OCR, the following words were fixed:\n")
        replacements_counter = Counter(all_replacements)
        for replacement, count in replacements_counter.items():
            if count > 1:
                print(f"{replacement} {GREY}({count} times){RESET}")
            else:
                print(replacement)
        print('')
    elif all_replacements and not debug:
        if len(subtitle_files) > 0:
            track_str = "tracks"
        else:
            track_str = "track"
        print(f"{GREY}[UTC {get_timestamp()}] [OCR]{RESET} Fixed {len(all_replacements)} OCR errors in subtitle {track_str}.")

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


def get_priority(subs_langs, lang):
    try:
        return subs_langs.index(lang)
    except ValueError:
        return len(subs_langs)



def get_wanted_subtitle_tracks(debug, file_info, pref_langs):
    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] [DEBUG]{RESET} get_wanted_subtitle_tracks:\n")
        print(f"{BLUE}preferred subtitle languages{RESET}: {pref_langs}")

    total_subs_tracks = 0
    pref_subs_langs = pref_langs

    subs_track_ids = []
    subs_track_languages = []
    subs_track_names = []

    unmatched_subs_track_languages = []

    forced_track_ids = []
    forced_track_languages = []
    forced_track_names = []
    forced_sub_filetypes = []

    default_subs_track = ''
    all_sub_filetypes = []
    sub_filetypes = []
    srt_track_ids = []
    ass_track_ids = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False
    srt_ass_track_removed = []
    main_audio_track_lang = None

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
            if track_language in pref_subs_langs:
                subs_track_languages.append(track_language)
            else:
                unmatched_subs_track_languages.append(track_language)
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
            forced_track_val = ''

            for key, value in track["properties"].items():
                if key == 'language':
                    track_language = value
                if key == 'forced_track':
                    forced_track_val = value
                if key == 'track_name':
                    track_name = value

            if forced_track_val or "forced" in track_name.lower():
                forced_track = True
            else:
                forced_track = False

            if track_language in pref_subs_langs:
                needs_processing = True
                needs_sdh_removal = True

                if forced_track:
                    forced_track_ids.append(track["id"])
                    forced_track_languages.append(track_language)
                    if track["codec"] == "HDMV PGS":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sup')
                    elif track["codec"] == "VobSub":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sub')
                    elif track["codec"] == "SubRip/SRT":
                        forced_track_names.append(f'non-{main_audio_track_lang} dialogue')
                        forced_sub_filetypes.append('srt')
                    elif track["codec"] == "SubStationAlpha":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('ass')

                # If the track language is "und" (undefined), assume english subtitles
                if track_language.lower() == "und":
                    track_language = 'eng'
                    pref_subs_langs.append('eng')

                if subs_track_languages.count(track_language) == 0 and not forced_track:
                    if track["codec"] == "HDMV PGS":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('sup')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "VobSub":
                        # If VobSub is the only subtitle type in the file (DVD), keep it.
                        # If it is a mix of Vobsub and PGS (BluRay), only the PGS should be kept.
                        if all(codec == "VobSub" for codec in all_sub_filetypes):
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            sub_filetypes.append('sub')
                            needs_convert = True
                            needs_processing = True
                    elif track["codec"] == "SubRip/SRT":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        subs_track_names.append(track_name)
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if ((track["codec"] != "SubRip/SRT" and track["codec"] != "SubStationAlpha")
                            and subs_track_languages.count(track_language) == 1 and
                            track_language not in srt_ass_track_removed):

                        if 'srt' in sub_filetypes:
                            sub_filetypes.remove('srt')
                            subs_track_languages.remove(track_language)
                            subs_track_names.pop()
                            srt_ass_track_removed.append(track_language)

                        if 'ass' in sub_filetypes:
                            sub_filetypes.remove('ass')
                            subs_track_languages.remove(track_language)
                            subs_track_names.pop()
                            srt_ass_track_removed.append(track_language)

                        if track["codec"] == "HDMV PGS":
                            if sub_filetypes:
                                if sub_filetypes[-1] != 'sup':
                                    sub_filetypes.append('sup')
                                    subs_track_ids.append(track["id"])
                                    subs_track_languages.append(track_language)
                                    subs_track_names.append(track_name)
                            elif not sub_filetypes:
                                sub_filetypes.append('sup')
                                subs_track_ids.append(track["id"])
                                subs_track_languages.append(track_language)
                                subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

                        elif track["codec"] == "VobSub":
                            sub_filetypes.append('sub')
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            subs_track_names.append(track_name)
                            needs_convert = True
                            needs_processing = True

                        subs_tracks_ids_no_srt = [x for x in subs_track_ids if x not in srt_track_ids]
                        subs_tracks_ids_no_ass = [x for x in subs_tracks_ids_no_srt if x not in ass_track_ids]
                        subs_track_ids = subs_tracks_ids_no_ass

    # Add the forced audio tracks
    subs_track_ids = subs_track_ids + forced_track_ids
    subs_track_languages = subs_track_languages + forced_track_languages
    subs_track_names = subs_track_names + forced_track_names
    sub_filetypes = sub_filetypes + forced_sub_filetypes

    # If none of the subtitles matched, add the forced tracks as a last effort
    if len(subs_track_ids) == 0:
        subs_track_ids = forced_track_ids
        subs_track_languages = forced_track_languages
        subs_track_names = forced_track_names
        sub_filetypes = forced_sub_filetypes

    if subs_track_ids:
        # If subs language prefs have not been set, set the list
        # to the sub languages that have been matched as fallback
        if not pref_subs_langs and subs_track_languages:
            pref_subs_langs = subs_track_languages

        paired = zip(subs_track_languages, sub_filetypes, subs_track_ids, subs_track_names)
        sorted_paired = sorted(paired, key=lambda x: get_priority(pref_subs_langs, x[0]))
        sorted_subs_languages, sorted_subs_filetypes, sorted_subs_track_ids, sorted_subs_track_names = zip(*sorted_paired)

        subs_track_languages = list(sorted_subs_languages)
        sub_filetypes = list(sorted_subs_filetypes)
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

    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True

    if debug:
        print(f"{BLUE}needs processing{RESET}: {needs_processing}")
        print(f"{BLUE}needs SDH removal{RESET}: {needs_sdh_removal}")
        print(f"{BLUE}needs to be converted{RESET}: {needs_convert}")
        print(f"\n{BLUE}all wanted subtitle track ids{RESET}: {subs_track_ids}")
        print(f"{BLUE}default subtitle track id{RESET}: {default_subs_track}")
        print(f"{BLUE}subtitle tracks to be extracted{RESET}:\n  {BLUE}filetypes{RESET}: {sub_filetypes}, "
              f"{BLUE}langs{RESET}: {subs_track_languages}, {BLUE}names{RESET}: {subs_track_names}\n")

    return subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert, \
        sub_filetypes, subs_track_languages, subs_track_names, needs_processing
