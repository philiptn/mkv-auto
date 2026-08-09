from subtitle_filter import Subtitles
from dataclasses import replace
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
import psutil
import select
import pathlib
import traceback

from modules.misc import *

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
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] == 'Xvfb' and proc.info['cmdline']:
                for arg in proc.info['cmdline']:
                    match = re.match(r":(\d+)", arg)
                    if match:
                        active_displays.add(int(match.group(1)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return active_displays


def _monitor_memory_usage(xvfb_pid, cmd_pid, limit_bytes):
    """
    Monitors the total RSS (in bytes) of Xvfb and the command process.
    If usage exceeds limit_bytes, the entire process group is killed.
    """
    xvfb_proc = psutil.Process(xvfb_pid)
    cmd_proc = psutil.Process(cmd_pid)

    while True:
        try:
            # If main command is no longer running, stop monitoring
            if not cmd_proc.is_running():
                break

            # Calculate total RSS of Xvfb + main command
            total_rss = xvfb_proc.memory_info().rss + cmd_proc.memory_info().rss

            if total_rss > limit_bytes:
                # Kill the entire process group
                os.killpg(os.getpgid(xvfb_pid), signal.SIGTERM)
                os.killpg(os.getpgid(cmd_pid), signal.SIGTERM)
                break
        except psutil.NoSuchProcess:
            # If either process ended, just exit monitor
            break
        except Exception as e:
            # Catch-all for safety
            print("Error in memory monitoring:", e)
            break

        # Check memory every second
        time.sleep(1)


def run_with_xvfb(command, memory_per_thread):
    """
    Launches Xvfb using -displayfd to auto-pick a free display, then runs `command`
    with DISPLAY set to that value. The `display_number` parameter is ignored for
    the display choice (kept only for signature compatibility).
    """
    xvfb_process = None
    command_process = None
    return_code = -1
    xvfb_cmd = []
    stderr = ''
    picked_display = None  # string like "3"

    try:
        # Start Xvfb: let it choose a free display and write it to stdout
        xvfb_cmd = [
            "Xvfb",
            "-displayfd", "1",               # write chosen number to stdout
            "-screen", "0", "1024x768x24",
            "-ac",
            "-nolisten", "tcp",
        ]
        xvfb_process = subprocess.Popen(
            xvfb_cmd,
            start_new_session=True,
            stdout=subprocess.PIPE,          # we read the chosen display from here
            stderr=subprocess.PIPE,          # capture for diagnostics
            text=True
        )

        # Read the chosen display number from stdout with a timeout
        deadline = time.time() + 5.0  # seconds
        line = None
        while time.time() < deadline and line is None:
            rlist, _, _ = select.select([xvfb_process.stdout], [], [], 0.1)
            if rlist:
                line = xvfb_process.stdout.readline()
                break
            # abort early if Xvfb died
            if xvfb_process.poll() is not None:
                break

        if not line:
            # pull stderr for context
            try:
                _, xvfb_err = xvfb_process.communicate(timeout=0.2)
            except Exception:
                xvfb_err = ''
            raise RuntimeError(f"Xvfb did not report a display number via -displayfd. stderr: {xvfb_err.strip()}")

        picked_display = line.strip()
        if not picked_display.isdigit():
            raise RuntimeError(f"Unexpected -displayfd output: {picked_display!r}")

        # Wait for the UNIX socket to appear so clients can connect
        sock_path = pathlib.Path(f"/tmp/.X11-unix/X{picked_display}")
        for _ in range(100):  # up to ~5s
            if sock_path.exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"Xvfb started but socket {sock_path} did not appear")

        # Set DISPLAY for the child command
        env = os.environ.copy()
        env["DISPLAY"] = f":{picked_display}"

        # Launch the main command
        command_process = subprocess.Popen(
            command,
            env=env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Monitor memory
        monitor_thread = threading.Thread(
            target=_monitor_memory_usage,
            args=(xvfb_process.pid, command_process.pid, memory_per_thread * 1024 ** 3),
            daemon=True
        )
        monitor_thread.start()

        stdout, stderr = command_process.communicate()
        return_code = command_process.returncode

    except Exception as e:
        traceback.print_exc()
        return_code = -1
        stderr = f"Exception: {str(e)}"

    finally:
        # Clean up both processes reliably
        for proc in [command_process, xvfb_process]:
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    time.sleep(1)
                    if proc.poll() is None:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait()
                except Exception:
                    traceback.print_exc()

    if return_code != 0:
        active = get_active_xvfb_displays()
        return (f"{GREEN}XVFB COMMAND:{RESET} {YELLOW}'{xvfb_cmd}'{RESET}, "
                f"{GREEN}ACTIVE DISPLAYS:{RESET} {YELLOW}'{active}'{RESET}, "
                f"{GREEN}PICKED DISPLAY:{RESET} {YELLOW}':{picked_display}'{RESET}, "
                f"{GREEN}MAIN COMMAND:{RESET} {YELLOW}'{command}'{RESET}"
                f", {RED}ERROR: '{stderr}'{RESET}")
    return return_code


def remove_sdh_worker(logger, debug, subtitle_track, remove_music, subtitleedit, memory_per_thread):
    input_file = subtitle_track.path
    language = subtitle_track.language
    replacements = []

    redo_casing = check_config(config, 'subtitles', 'redo_casing')
    normalize_position = check_config(config, 'subtitles', 'normalize_position')

    with open(input_file, 'r', encoding='utf-8') as file:
        content = file.read()
    # Remove html font tags
    content = re.sub(r'<font[^>]*>|</font>', '', content)
    # Normalize subtitle positions (remove ASS alignment tags like {\an8})
    if normalize_position:
        content = re.sub(r'\{\\an[1-9]\}\s*', '', content)
    with open(f"{input_file}_tmp.srt", 'w', encoding='utf-8') as file:
        file.write(content)
    os.remove(input_file)
    shutil.move(f"{input_file}_tmp.srt", input_file)
    subtitle_tmp = f"{input_file}_tmp.srt"

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

    if language == 'eng':
        current_replacements = find_and_replace(input_file, 'ocr-replacements/replacements_srt_eng_only.csv', subtitle_tmp)
        replacements = replacements + current_replacements
        current_replacements = find_and_replace(subtitle_tmp, 'ocr-replacements/replacements_srt_only.csv', input_file)
        os.remove(subtitle_tmp)
        replacements = replacements + current_replacements
    elif language == 'nor':
        current_replacements = find_and_replace(input_file, 'ocr-replacements/replacements_srt_nor_only.csv', subtitle_tmp)
        replacements = replacements + current_replacements
        current_replacements = find_and_replace(subtitle_tmp, 'ocr-replacements/replacements_srt_only.csv', input_file)
        os.remove(subtitle_tmp)
        replacements = replacements + current_replacements
    else:
        current_replacements = find_and_replace(input_file, 'ocr-replacements/replacements_srt_only.csv', subtitle_tmp)
        os.rename(subtitle_tmp, input_file)
        replacements = replacements + current_replacements

    result = run_with_xvfb(command, memory_per_thread)
    if result != 0:
        custom_print(logger, result)
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

        # Remove text between * ... * in subtitles
        subs = pysrt.open(input_file)
        for sub in subs:
            sub.text = re.sub(r'\s*\*[^*]+\*\s*', ' ', sub.text)
            sub.text = re.sub(r'\s{2,}', ' ', sub.text)  # clean up double spaces
            sub.text = sub.text.strip()
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

        # Remove lines that only contain "**"
        subs = pysrt.open(input_file)
        subs[:] = [s for s in subs if s.text and not re.fullmatch(r'\*+', s.text)]
        subs.save(f"{input_file}.tmp.srt", encoding='utf-8')
        shutil.move(f"{input_file}.tmp.srt", input_file)

        clean_invalid_utf8(input_file, f'{input_file}.tmp.srt')
        shutil.move(f"{input_file}.tmp.srt", input_file)

        subs = pysrt.open(input_file)
        subs = pysrt.SubRipFile([sub for sub in subs if not sub.text.isupper()])
        subs.save(f"{input_file}.tmp.srt", encoding='utf-8')
        shutil.move(f"{input_file}.tmp.srt", input_file)

    if debug:
        print(f'\n{GREY}[UTC {get_timestamp()}] [SDH DEBUG]{GREEN} Current language is set to "{language}"{RESET}')

    return replacements


def remove_sdh(max_threads, logger, debug, input_files, remove_music, track_names, external_sub, memory_per_thread):
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    all_replacements = []
    cleaned_track_names = []

    if debug:
        print('\n')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        tasks = [executor.submit(remove_sdh_worker, logger, debug, input_file, remove_music,
                                 subtitleedit, memory_per_thread)
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


def convert_ass_to_srt(subtitle_tracks, main_audio_track_lang):
    output_subtitles = []
    errored_ass_subs = []
    missing_subs_langs = []
    keep_original_subtitles = check_config(config, 'subtitles', 'keep_original_subtitles')
    remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')

    for track in subtitle_tracks:
        if track.extension == 'ass':
            language = track.language
            name = track.name or ''

            if track.forced:
                output_name = f'non-{main_audio_track_lang} dialogue'
            else:
                full_language = pycountry.languages.get(alpha_3=language)
                if full_language:
                    output_name = name if name and name != 'Original' else full_language.name
                else:
                    output_name = name if name and name != 'Original' else ''
                if remove_sdh:
                    output_name = remove_sdh_cc_text(output_name)
                    if 'SDH' in name.upper() or 'CC' in name.upper():
                        if "(from " not in output_name:
                            output_name = "{} (from {})".format(output_name, re.sub(r'[\[\]\(\)]', '', name))

            srt_path = os.path.splitext(track.path)[0] + '.srt'
            with open(track.path) as ass_file:
                srt_output = asstosrt.convert(ass_file)
            with open(srt_path, "w") as srt_file:
                srt_file.write(srt_output)

            if is_valid_srt(srt_path):
                srt_track = SubtitleTrack(path=srt_path, track_id=track.track_id, language=language,
                                          forced=track.forced, name=output_name, extension='srt',
                                          source=track.source)
                if keep_original_subtitles:
                    # The kept original is no longer the forced/dialogue track
                    original_track = replace(track, forced=False)
                    output_subtitles += [srt_track, original_track]
                else:
                    output_subtitles += [srt_track]
            else:
                errored_ass_subs.append(track)
                missing_subs_langs.append(language)
                if keep_original_subtitles:
                    output_subtitles += [track]

        else:
            output_subtitles.append(track)

    return output_subtitles, errored_ass_subs, missing_subs_langs


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


def resync_srt_subs_worker(debug, input_file, subtitle_track, max_retries, retry_delay):
    subtitle_filename = subtitle_track.path
    temp_filename = f"{subtitle_filename}.tmp.srt"

    # If the subtitle track is a forced track,
    # skip resyncing as these have tendency to get out of sync
    if subtitle_track.forced or 'non- Dialogue' in (subtitle_track.name or ''):
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


def merge_subtitles_with_priority(built_in, external):
    """Pick one subtitle track per language from a single file's two sources.

    Operates on one file's tracks. It used to walk two whole-batch lists in
    lockstep by index, which meant it could only be called with both lists in
    exactly the same file order - a constraint nothing enforced.
    """
    prioritize_subtitles = check_config(config, 'subtitles', 'prioritize_subtitles').lower()

    def group_subs(subs):
        """Groups SubtitleTracks by language, keeping .sub/.idx pairs together."""
        sub_dict = {}
        for track in subs or []:
            if track is None:
                continue
            lang = track.language
            if track.extension in {"sub", "idx"}:
                sub_dict.setdefault(lang, {}).update({track.extension: track})
            else:
                sub_dict[lang] = {"file": track}
        return sub_dict

    built_in_dict = group_subs(built_in)
    external_dict = group_subs(external)

    final_dict = {}
    for lang in set(built_in_dict.keys()) | set(external_dict.keys()):
        if prioritize_subtitles == "external":
            # prefer external, fall back to built-in if external missing
            final_dict[lang] = external_dict.get(lang) or built_in_dict.get(lang)
        else:
            # prefer built-in, fall back to external if built-in missing
            final_dict[lang] = built_in_dict.get(lang) or external_dict.get(lang)

    # Convert dictionary back to a flat list of SubtitleTracks
    final_list = []
    for subs in final_dict.values():
        if "file" in subs:
            final_list.append(subs["file"])
        elif "sub" in subs and "idx" in subs:
            final_list.extend([subs["idx"], subs["sub"]])
        else:
            final_list.extend(subs.values())

    return final_list


def extract_subs_in_mkv(logger, max_threads, debug, filename, track_numbers, output_filetypes, subs_languages, subs_forced,
                        subs_names):
    if debug:
        print('\n')

    results = [None] * len(track_numbers)  # Pre-allocate a list for the results in order
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Create a dictionary to store futures with their respective indices
        future_to_index = {
            executor.submit(extract_subtitle, logger, debug, filename, track, filetype, language, forced, name): i
            for i, (track, filetype, language, forced, name) in
            enumerate(zip(track_numbers, output_filetypes, subs_languages, subs_forced, subs_names))
        }

        # As each future completes, place the result in the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()  # Store the result at the correct index

    # Drop tracks that failed extraction / validation (returned None)
    return [track for track in results if track is not None]


def extract_subtitle(logger, debug, filename, track, output_filetype, language, forced, name):
    base, _, _ = filename.rpartition('.')
    # Plain working name; metadata travels on the returned SubtitleTrack, not in
    # the filename. The track id only keeps sibling files unique.
    subtitle_filename = f"{base}.si{track}.{output_filetype}"

    command = ["mkvextract", filename, "tracks", f"{track}:{subtitle_filename}"]

    if debug:
        print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        log_debug(logger, f"{GREY}[UTC {get_timestamp()}]{RESET} {RED}[ERROR]{RESET} {result.stdout}")
    result.check_returncode()

    if output_filetype == 'srt' and not is_valid_srt(subtitle_filename):
        return

    return SubtitleTrack(path=subtitle_filename, track_id=int(track), language=language,
                         forced=bool(forced), name=name or '', extension=output_filetype,
                         source='internal')


def ocr_subtitles(logger, max_threads, memory_per_thread, debug, subtitle_tracks, main_audio_track_lang):
    subtitleedit_dir = 'utilities/SubtitleEdit'
    all_replacements = []

    if debug:
        print('\n')

    # Prepare to track the results in the order they were submitted
    results = [None] * len(subtitle_tracks)  # Placeholder list for results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Submit all tasks and store futures in a dictionary with their index
        future_to_index = {
            executor.submit(ocr_subtitle_worker, logger, memory_per_thread, debug, subtitle_tracks[i],
                            main_audio_track_lang, subtitleedit_dir): i
            for i in range(len(subtitle_tracks))
        }

        # As each future completes, store the result at the corresponding index
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()

    # Aggregate the per-track results.
    #   subtitles_all    = every track that should be muxed (converted + kept originals)
    #   output_subtitles = the tracks that still need SDH/resync processing
    output_subtitles = []
    subtitles_all = []
    errored_ocr = []
    missing_subs_langs = []

    for res in results:
        all_replacements = res['replacements'] + all_replacements
        subtitles_all += res['tracks']
        output_subtitles += res['processed']
        errored_ocr += res['errored']
        missing_subs_langs += res['missing']

    return output_subtitles, subtitles_all, all_replacements, errored_ocr, missing_subs_langs


def ocr_subtitle_worker(logger, memory_per_thread, debug, track, main_audio_track_lang, subtitleedit_dir):
    ocr_languages = check_config(config, 'subtitles', 'ocr_languages')
    always_remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')
    replacements = []

    file = track.path
    language = track.language
    forced = track.forced

    def display_name():
        if forced:
            return f'non-{main_audio_track_lang} dialogue'
        if track.name and track.name != 'Original':
            name = track.name
        else:
            # Derive the language's full name (handles both alpha-2 and alpha-3 codes)
            try:
                if len(language) > 2:
                    name = pycountry.languages.get(alpha_3=language).name
                else:
                    name = pycountry.languages.get(alpha_2=language).name
            except Exception:
                name = ''
        if always_remove_sdh and name:
            name = remove_sdh_cc_text(name)
            if track.name and ('SDH' in track.name.upper() or 'CC' in track.name.upper()):
                if "(from " not in name:
                    name = "{} (from {})".format(name, re.sub(r'[\[\]\(\)]', '', track.name))
        return name

    if track.extension in ('sup', 'sub'):
        # Image-based subtitles that require OCR.
        # Each gets a fresh SubtitleEdit instance in its own temp directory.
        temp_dir = tempfile.mkdtemp(prefix='SubtitleEdit_')
        try:
            local_subtitleedit_dir = os.path.join(temp_dir, 'SubtitleEdit')
            shutil.copytree(subtitleedit_dir, local_subtitleedit_dir)
            subtitleedit_exe = os.path.join(local_subtitleedit_dir, 'SubtitleEdit.exe')
            subtitleedit_settings = os.path.join(local_subtitleedit_dir, 'Settings.xml')

            if ocr_languages[0].lower() != 'all' and language not in ocr_languages:
                if is_non_empty_file(file):
                    # OCR not requested for this language: keep the original as-is
                    return {'tracks': [track], 'processed': [track], 'errored': [], 'missing': [], 'replacements': []}
                return {'tracks': [], 'processed': [], 'errored': [], 'missing': [], 'replacements': []}

            update_tesseract_lang_xml(debug, language, subtitleedit_settings)
            command = ["mono", subtitleedit_exe, "/convert", file, "srt", "/SplitLongLines", "/encoding:utf-8"]
            if debug:
                print(f"{GREY}[UTC {get_timestamp()}] {YELLOW}{' '.join(command)}{RESET}")

            result = run_with_xvfb(command, memory_per_thread)
            # SubtitleEdit writes the .srt next to the source, sharing its stem.
            srt_path = os.path.splitext(file)[0] + '.srt'

            if result != 0 or not is_valid_srt(srt_path):
                log_debug(logger, result)
                # Keep the (unconverted) original muxable and flag it for retry.
                return {'tracks': [track], 'processed': [track], 'errored': [track],
                        'missing': [language], 'replacements': []}

            subtitle_tmp = srt_path + '.tmp.srt'
            if language == 'eng':
                replacements += find_and_replace(srt_path, 'ocr-replacements/replacements_eng_only.csv', subtitle_tmp)
                replacements += find_and_replace(subtitle_tmp, 'ocr-replacements/replacements.csv', srt_path)
                os.remove(subtitle_tmp)
            elif language == 'nor':
                replacements += find_and_replace(srt_path, 'ocr-replacements/replacements_nor_only.csv', subtitle_tmp)
                replacements += find_and_replace(subtitle_tmp, 'ocr-replacements/replacements.csv', srt_path)
                os.remove(subtitle_tmp)
            else:
                replacements += find_and_replace(srt_path, 'ocr-replacements/replacements.csv', subtitle_tmp)
                os.rename(subtitle_tmp, srt_path)

            srt_track = SubtitleTrack(path=srt_path, track_id=track.track_id, language=language,
                                      forced=forced, name=display_name(), extension='srt', source=track.source)
            tracks = [srt_track]
            if check_config(config, 'subtitles', 'keep_original_subtitles'):
                # The kept image-based original is never the forced/dialogue track.
                original_name = track.name if track.name else 'Original'
                tracks.append(replace(track, name=original_name, forced=False))
            return {'tracks': tracks, 'processed': [srt_track], 'errored': [], 'missing': [],
                    'replacements': replacements}
        finally:
            shutil.rmtree(temp_dir)
    else:
        # Already text-based (srt / leftover ass): just assign the display name.
        new_track = replace(track, name=display_name())
        return {'tracks': [new_track], 'processed': [new_track], 'errored': [], 'missing': [],
                'replacements': []}


def update_tesseract_lang_xml(debug, new_language, settings_file):
    if debug:
        print(
            f"{GREY}[UTC {get_timestamp()}] [OCR DEBUG] {GREEN}Updated SubtitleEdit OCR language to '{new_language}'\n")
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
    always_remove_sdh = check_config(config, 'subtitles', 'always_remove_sdh')
    only_keep_one_matching_subtitle = check_config(config, 'subtitles', 'only_keep_one_matching_subtitle')
    remove_commentary_track = check_config(config, 'audio', 'remove_commentary')

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
    missing_subs_langs = []

    # Get all subtitle codecs
    for track in file_info['tracks']:
        if track['type'] == 'subtitles':
            all_sub_filetypes.append(track['codec'])

    # Get main audio track language
    main_audio_track_lang_name = get_main_audio_track_language(file_info)
    try:
        main_audio_track_lang = pycountry.languages.get(name=main_audio_track_lang_name).alpha_3
    except:
        main_audio_track_lang = None

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

    # If none of the subs track matches the language preference,
    # set the preferred sub languages to the ones found, and run the detection
    # using that as the reference. Unless 'main_audio_language_subs_only' is enabled,
    # then it should use that as the wanted subtitle language.
    if not subs_track_languages and not main_audio_language_subs_only:
        pref_subs_langs = unmatched_subs_track_languages
    if main_audio_language_subs_only:
        pref_subs_langs = [main_audio_track_lang]

    # Check for subs languages that are wanted, but missing in file
    if pref_subs_langs:
        all_sub_langs = []
        for track in file_info["tracks"]:
            if track["type"] == "subtitles":
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
                    # If the track language is "und", then it is probably English.
                    if track_language == 'und':
                        track_language = 'eng'
                if key == 'forced_track':
                    forced_track_val = value
                    forced_track_val = 1 if forced_track_val else 0
                if key == 'track_name':
                    track_name = value

            if forced_track_val or "forced" in track_name.lower() or track_name == f'non-{main_audio_track_lang_name} dialogue':
                forced_track = True
                forced_track_val = 1
                if "forced" not in track_name.lower():
                    if track_name and not track_name.endswith(" (Forced)"):
                        track_name = f"{track_name} (Forced)"
                    else:
                        track_name = "Forced"
            else:
                forced_track = False

            if track_language == 'nob' or track_language == 'nno':
                track_language = 'nor'

            if track_language in pref_subs_langs:
                needs_processing = True
                needs_sdh_removal = True
                add_track = False

                if forced_track:
                    forced_track_ids.append(track["id"])
                    forced_track_languages.append(track_language)
                    if track["codec"] == "HDMV PGS":
                        if 'srt' in forced_sub_filetypes:
                            for index, lang in enumerate(forced_track_languages):
                                if lang == track_language and forced_sub_filetypes[index] == 'srt':
                                    forced_sub_filetypes.pop(index)
                                    forced_track_languages.pop(index)
                                    forced_track_ids.pop(index)
                                    forced_track_names.pop(index)
                                    forced_sub_bool.pop(index)
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sup')
                        forced_sub_bool.append(forced_track_val)
                    elif track["codec"] == "VobSub":
                        forced_track_names.append(track_name)
                        forced_sub_filetypes.append('sub')
                        forced_sub_bool.append(forced_track_val)
                    elif track["codec"] == "SubRip/SRT" and forced_sub_filetypes not in ('sup', 'sub', 'ass'):
                        forced_track_names.append(f'non-{main_audio_track_lang_name} dialogue')
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
                    if 'eng' in missing_subs_langs:
                        missing_subs_langs.remove('eng')
                    pref_subs_langs.append('eng')

                if subs_track_languages.count(track_language) == 0:
                    add_track = True
                elif subs_track_languages.count(track_language) > 0 and not only_keep_one_matching_subtitle:
                    add_track = True
                if 'commentary' in track_name.lower() and remove_commentary_track:
                    add_track = False

                if not forced_track and add_track:
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
                        if always_remove_sdh and ('sdh' in track_name.lower() or track_name.lower() == 'cc'):
                            subs_track_names.append('')
                        else:
                            subs_track_names.append(track_name)
                        subs_track_forced.append(forced_track_val)
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        subs_track_ids.append(track["id"])
                        subs_track_languages.append(track_language)
                        if always_remove_sdh and ('sdh' in track_name.lower() or track_name.lower() == 'cc'):
                            subs_track_names.append('')
                        else:
                            subs_track_names.append(track_name)
                        subs_track_forced.append(forced_track_val)
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if track["codec"] != "SubRip/SRT" and not forced_track:
                        add_track = True
                        if only_keep_one_matching_subtitle and sub_filetypes in ("sup", "sub", "ass"):
                            add_track = False
                        if add_track:
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
    elif forced_subtitles_priority.lower() == 'first':
        subs_track_ids = forced_track_ids + subs_track_ids
        subs_track_languages = forced_track_languages + subs_track_languages
        subs_track_names = forced_track_names + subs_track_names
        sub_filetypes = forced_sub_filetypes + sub_filetypes
        subs_track_forced = forced_sub_bool + subs_track_forced
    else:
        subs_track_ids = subs_track_ids
        subs_track_languages = subs_track_languages
        subs_track_names = subs_track_names
        sub_filetypes = sub_filetypes
        subs_track_forced = subs_track_forced

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
        sorted_subs_languages, sorted_subs_filetypes, sorted_subs_track_forced, sorted_subs_track_ids, sorted_subs_track_names = zip(
            *sorted_paired)

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
