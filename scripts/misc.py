import os
import sys
from datetime import datetime
from backports import configparser
import re
from collections import defaultdict
import traceback
import shutil
import logging
import sys
import time
import pycountry
import threading


# ANSI color codes
BLUE = '\033[94m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[93m'
RED = '\033[91m'
GREEN = '\033[92m'
CYAN = '\033[96m'
MAGENTA = '\033[95m'
WHITE = '\033[97m'

# Unicode symbols
ACTIVE = RESET
DONE = RESET
CHECK = '✓'
CHECK_BOLD = '✔'
CROSS = '✘'
RIGHT_ARROW = '➝'

custom_date_format = 'UTC %Y-%m-%d %H:%M:%S'


class CorruptedFile(Exception):
    """Custom exception to identify corrupted files."""
    pass


class ContinuousSpinner:
    def __init__(self, interval=0.1, frames=None):
        # Spinners
        # ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        # ["-", "\\", "|", "/"]
        self.frames = frames or ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None
        self._idx = 0
        self._make_line = lambda: ""  # function returning the line text (excluding spinner)

    def set_line_func(self, func):
        # func should be a callable returning the line text (timestamp included, etc.)
        self._make_line = func

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_line=""):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        if final_line:
            sys.stdout.write(f"\r{final_line}\n")
        else:
            sys.stdout.write("\r\n")
        sys.stdout.flush()

    def _spin(self):
        while not self._stop_event.is_set():
            frame = self.frames[self._idx]
            # Call the function that includes real-time UTC
            line_text = self._make_line()
            sys.stdout.write(f"\r{line_text}{ACTIVE}{frame}{RESET} ")
            sys.stdout.flush()
            time.sleep(self.interval)
            self._idx = (self._idx + 1) % len(self.frames)


SPINNER = None

# List of tags to exclude from replacement
# https://support.plex.tv/articles/local-files-for-trailers-and-extras/
excluded_tags = [
    "-behindthescenes", "-deleted", "-featurette",
    "interview", "-scene", "-short", "-trailer", "-other"
]


def process_covers(input_folder):
    # Recursively walk through the directories, skipping those starting with '.'
    for root, dirs, files in os.walk(input_folder):
        # Modify dirs in-place to skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        cover_files = []
        normal_files = []

        for f in files:
            base, ext = os.path.splitext(f)

            if ext.lower() in ['.jpg', '.png']:
                cover_files.append(f)
            else:
                normal_files.append(f)

        # If there are no extras or no normal files in this directory, no action needed
        if not cover_files or not normal_files:
            continue

        identified_media = None
        for nf in normal_files:
            result = reformat_filename(nf, names_only=True)
            if result['media_type'] in ['movie', 'movie_hdr', 'tv_show', 'tv_show_hdr']:
                identified_media = result
                break

        if not identified_media:
            continue

        media_name = identified_media['media_name']

        for cf in cover_files:
            old_full_path = os.path.join(root, cf)

            new_filename = f"{media_name} - {cf}"
            new_full_path = os.path.join(root, new_filename)

            if not os.path.exists(new_full_path):
                os.rename(old_full_path, new_full_path)


def process_extras(input_folder):
    # Recursively walk through the directories, skipping those starting with '.'
    for root, dirs, files in os.walk(input_folder):
        # Modify dirs in-place to skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        extras_files = []
        normal_files = []

        for f in files:
            base, ext = os.path.splitext(f)

            if ext.lower() not in ['.mkv', '.mp4', '.mov', '.avi', '.srt']:
                continue

            # Check if the filename ends with any of the excluded tags
            if any(base.lower().endswith(tag) for tag in excluded_tags):
                extras_files.append(f)
            else:
                normal_files.append(f)

        # If there are no extras or no normal files in this directory, no action needed
        if not extras_files or not normal_files:
            continue

        # We have extras and also recognized normal files;
        # Let's see what kind of media we have in normal_files.
        # We'll try to identify if it's a TV show or a movie by using reformat_filename() in names_only mode.
        # We only need one representative file to determine the type and name.
        identified_media = None
        for nf in normal_files:
            result = reformat_filename(nf, names_only=True)
            if result['media_type'] in ['movie', 'movie_hdr', 'tv_show', 'tv_show_hdr']:
                identified_media = result
                break

        if not identified_media:
            # Couldn't identify any normal file as movie or TV show, skip renaming extras
            continue

        media_type = identified_media['media_type']
        media_name = identified_media['media_name']

        # If it's a TV show, we'll rename extras as S00Exx.
        # If it's a movie, we just put "Movie (Year) - extras name"
        # We'll number the extras for TV shows incrementally.
        extras_counter = 1

        for ef in extras_files:
            old_full_path = os.path.join(root, ef)
            base, ext = os.path.splitext(ef)

            # Extract the extra tag part from the filename to put it into the new name
            matching_tag = None
            for tag in excluded_tags:
                if base.lower().endswith(tag):
                    matching_tag = tag
                    break

            # The portion before the tag:
            extras_title = base
            if matching_tag:
                extras_title = base[: -len(matching_tag)]
            extras_title = extras_title.strip()

            # Convert underscores or dots to spaces
            extras_title = extras_title.replace('.', ' ').replace('_', ' ')
            extras_title = to_sentence_case(extras_title)

            if 'tv_show' in media_type:
                # TV show extras:
                episode_num = f"{extras_counter:03d}"
                new_filename = f"{media_name} - S00E{episode_num} - {extras_title}{matching_tag}{ext}"
                extras_counter += 1
            else:
                # Movie extras:
                new_filename = f"{media_name} - {extras_title}{matching_tag}{ext}"

            new_full_path = os.path.join(root, new_filename)

            # Rename the file
            if not os.path.exists(new_full_path):
                os.rename(old_full_path, new_full_path)


def restore_extras(filenames_mkv_only, dirpath):
    tv_pattern = re.compile(r"^(.*?) - S00E\d+ - (.+)$")
    movie_pattern = re.compile(r"^(.*?) - (.+)$")

    for fname in filenames_mkv_only:
        input_file_with_path = os.path.join(dirpath, fname)
        if not os.path.isfile(input_file_with_path):
            continue

        base, ext = os.path.splitext(fname)
        # Try TV show pattern first
        tv_match = tv_pattern.match(base)
        if tv_match:
            # Group 2 is the original filename part
            original_base = tv_match.group(2)
        else:
            # Try movie pattern
            movie_match = movie_pattern.match(base)
            if movie_match:
                original_base = movie_match.group(2)
            else:
                # Not matching our patterns, skip
                continue

        # Construct the original filename
        original_filename = original_base + ext
        original_path = os.path.join(dirpath, original_filename)

        if not os.path.exists(original_path):
            os.rename(input_file_with_path, original_path)


# Function to remove ANSI color codes
def remove_color_codes(text):
    ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)


def is_non_empty_file(filepath):
    return os.path.isfile(filepath) and os.path.getsize(filepath) > 0


# Function to print dynamic progress, only updating the last line
def print_with_progress(logger, current, total, header, description="Processing"):
    hide_cursor()
    global SPINNER
    if current == 0:
        SPINNER = ContinuousSpinner(interval=0.15)

    def line_func():
        return (
            f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} "
            f"{description} ({current}/{total}) "
        )

    if SPINNER:
        SPINNER.set_line_func(line_func)
        SPINNER.start()

    if current >= total and SPINNER is not None:
        final_line = (
            f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} "
            f"{description} {DONE}{CHECK}{RESET} {' ' * (total + 3)} "
        )
        SPINNER.stop(final_line)
        SPINNER = None
        logger.info(f"[UTC {get_timestamp()}] [{header}] {description} {CHECK}")
        logger.debug(f"[UTC {get_timestamp()}] [{header}] {description} {CHECK}")
        logger.color(f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} {description} {DONE}{CHECK}{RESET}")


def print_with_progress_files(logger, current, total, header, description="Processing"):
    hide_cursor()
    current_print = (current + 1) if current < total else current
    global SPINNER
    if current == 0:
        SPINNER = ContinuousSpinner(interval=0.15)

    def line_func():
        return (
            f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} "
            f"{description} {current_print} of {total} "
        )

    if SPINNER:
        SPINNER.set_line_func(line_func)
        SPINNER.start()


def print_final_spin_files(logger, current, total, header, description="Processing"):
    hide_cursor()
    global SPINNER

    if SPINNER is not None:
        final_line = (
            f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} "
            f"{description} {current} of {total} {DONE}{CHECK}{RESET}"
        )
        SPINNER.stop(final_line)
        SPINNER = None

        logger.info(f"[UTC {get_timestamp()}] [{header}] {description} {current} of {total} {CHECK}")
        logger.debug(f"[UTC {get_timestamp()}] [{header}] {description} {current} of {total} {CHECK}")
        logger.color(
            f"{GREY}[UTC {get_timestamp()}] [{header}]{RESET} "
            f"{description} {current} of {total} {DONE}{CHECK}{RESET}"
        )


def custom_print(logger, message):
    message_with_timestamp = f"{GREY}[UTC {get_timestamp()}]{RESET} {message}"
    # Print the message to the console with color
    sys.stdout.write(message_with_timestamp + "\n")
    # Log the message without color to the plain text log
    plain_message = remove_color_codes(message_with_timestamp)
    logger.info(plain_message)
    logger.debug(plain_message)
    # Log the message with color to the color log
    logger.color(message_with_timestamp)


def log_debug(logger, message):
    message_with_timestamp = f"{GREY}[UTC {get_timestamp()}]{RESET} {message}"
    # Log the message without color to the plain text log
    plain_message = remove_color_codes(message_with_timestamp)
    logger.debug(plain_message)


def print_no_timestamp(logger, message):
    # Print the message to the console with color
    sys.stdout.write(message + "\n")

    # Store the original formatters
    original_formatters = {}
    for handler in logger.handlers:
        original_formatters[handler] = handler.formatter

    # Temporarily remove the timestamp from the formatters
    no_timestamp_formatter = logging.Formatter('%(message)s')
    for handler in logger.handlers:
        handler.setFormatter(no_timestamp_formatter)

    # Log the message without a timestamp, except plaintext
    plain_message = remove_color_codes(message)
    logger.info(f"[UTC {get_timestamp()}] {plain_message}")
    logger.debug(f"[DEBUG] [UTC {get_timestamp()}] {plain_message}")
    logger.color(message)  # Colored logging

    # Restore the original formatters
    for handler, formatter in original_formatters.items():
        handler.setFormatter(formatter)


def print_multi_or_single(amount, string):
    if amount == 1:
        return string
    elif amount > 1:
        return f"{string}s"
    else:
        return string


def format_audio_preferences_print(audio_format_preferences):
    # Define mappings for better readability in the output
    audio_label_map = {
        'EOS': 'Even-Out-Sound',
        'ORIG': 'Original Audio',
        'AC3': 'Dolby Digital',
        'EAC3': 'Dolby Digital Plus',
        'WAV': 'PCM',
    }

    codec_label_map = {
        'EOS': 'Even-Out-Sound',
        'ORIG': 'Original Audio',
        'AC3': 'Dolby Digital',
        'EAC3': 'Dolby Digital Plus',
        'WAV': 'PCM',
    }

    # Initialize an empty list to store the formatted strings
    formatted_preferences = []

    # Iterate through the preferences and format them
    for preference in audio_format_preferences:
        label, codec, channels = preference
        codec_label = codec
        label_text = label

        # Handle the label mapping
        if label in audio_label_map:
            label_text = audio_label_map[label]
        if codec in codec_label_map:
            codec_label = codec_label_map[codec]

        # Handle codec and channel configurations
        if codec and channels:
            if channels == '2.0':
                channel_text = "Stereo"
            else:
                channel_text = f"{channels}"
            if label:
                formatted_preferences.append(f"{label_text} ({codec_label} {channel_text})")
            else:
                if label_text:
                    formatted_preferences.append(f"{label_text} ({channel_text})")
                else:
                    formatted_preferences.append(f"{codec_label} ({channel_text})")
        elif codec == 'ORIG':
            formatted_preferences.append(codec_label)
        elif codec:
            if label_text:
                formatted_preferences.append(f"{label_text} ({codec_label})")
            else:
                formatted_preferences.append(f"{codec_label}")

    # Add numbering to the formatted preferences
    tree_lines = []
    for i, pref in enumerate(formatted_preferences):
        prefix = "├── " if i < len(formatted_preferences) - 1 else "└── "
        tree_lines.append(f"{prefix}{pref}")

    # Ensure a single string output with proper formatting
    return [x for x in tree_lines if x]  # Remove any empty strings


def debug_pause():
    print(f"{GREY}[DEBUG]{RESET} Press Enter to continue or 'q' to quit: ")
    if os.name == 'nt':  # Windows
        import msvcrt
        key = msvcrt.getch()
        if key.lower() == b'q':
            exit()
    else:  # Unix/Linux/MacOS
        import sys, tty, termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            key = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        if key.lower() == 'q':
            exit()
    print('')


def get_main_audio_track_language(file_info):
    main_audio_track_lang = None
    # Get the main audio language
    for track in file_info['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    language = pycountry.languages.get(alpha_3=value)
                    if language:
                        main_audio_track_lang = language.name
                    return main_audio_track_lang


def get_main_audio_track_language_3_letter(file_info):
    # Get the main audio language
    for track in file_info['tracks']:
        if track['type'] == 'audio':
            for key, value in track["properties"].items():
                if key == 'language':
                    # If the language is undetermined, assume English
                    if value == 'und':
                        value = 'eng'
                    return value


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S")


def flatten_directories(directory):
    # Walk through the directory
    for root, dirs, files in os.walk(directory, topdown=False):
        # Skip directories starting with a dot
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        # Skip files starting with a dot
        files = [f for f in files if not f.startswith('.')]

        for name in files:
            # Move each file to the root directory
            source = os.path.join(root, name)
            destination = os.path.join(directory, name)
            if source != destination:  # Avoid moving if source and destination are the same
                shutil.move(source, destination)

        for name in dirs:
            # Remove the empty subdirectories
            os.rmdir(os.path.join(root, name))


def format_time(seconds):
    """Return a formatted string for the given duration in seconds."""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours:
        if hours == 1:
            parts.append(f"{hours} hour,")
        else:
            parts.append(f"{hours} hours,")
    if minutes:
        if minutes == 1:
            parts.append(f"{minutes} minute")
        else:
            parts.append(f"{minutes} minutes")
    if seconds or not parts:  # If it's 0 seconds, we want to include it.
        if seconds == 1:
            parts.append(f"and {seconds} second")
        else:
            parts.append(f"and {seconds} seconds")

    if seconds and (not hours and not minutes):
        if seconds == 1:
            return f"{seconds} second"
        else:
            return f"{seconds} seconds"
    else:
        return " ".join(parts)


def get_config(section, option, default_config):
    """Get value from user.ini, fallback to defaults.ini and warn if using default."""
    if variables_user.has_option(section, option):
        return variables_user.get(section, option)
    else:
        # Print warning and use default if the user setting is missing
        print(f"{YELLOW}WARNING{RESET}: {BLUE}{option}{RESET} is missing from 'user.ini'. Using defaults.")
        return default_config.get(section, option)


def check_config(config, section, option):
    """Check the configuration value from the dictionary."""
    if section in config and option in config[section]:
        return config[section][option]
    else:
        print(f"{YELLOW}WARNING{RESET}: {BLUE}{option}{RESET} not found in section '{section}'.")
        return None


def to_sentence_case(s):
    if s == s.lower():
        return ' '.join(word.capitalize() for word in s.split(' '))
    else:
        return s


def rename_others_file_to_folder(input_dir):
    others_folder = check_config(config, 'general', 'others_folder')

    # Iterate through the input directory recursively
    for root, dirs, files in os.walk(input_dir):
        parent_folder_name = os.path.basename(root)
        a, parent_folder_reformatted = reformat_filename(parent_folder_name + '.mkv', False)

        # If the parent folder does not match any pattern, skip to next
        if parent_folder_reformatted.startswith(others_folder):
            continue

        # Check if the file should be categorized as others
        for filename in files:
            if not filename.endswith('.mkv'):
                continue  # Skip non-mkv files

            a, new_filename = reformat_filename(filename, False)
            if new_filename.startswith(others_folder):
                # Rename the file to match its parent folder
                new_file_path = os.path.join(root, f"{parent_folder_name}.{filename.split('.')[-1]}")
                old_file_path = os.path.join(root, filename)
                shutil.move(old_file_path, new_file_path)


def reformat_filename(filename, names_only):
    movie_folder = check_config(config, 'general', 'movies_folder')
    movie_hdr_folder = check_config(config, 'general', 'movies_hdr_folder')
    tv_folder = check_config(config, 'general', 'tv_shows_folder')
    tv_hdr_folder = check_config(config, 'general', 'tv_shows_hdr_folder')
    others_folder = check_config(config, 'general', 'others_folder')

    # Regular expression to match TV shows with season and episode, with or without year
    tv_show_pattern1 = re.compile(r"^(.*?)([. ]((?:19|20)\d{2}))?[. ]s(\d{2})e(\d{2})", re.IGNORECASE)
    # Regular expression to match TV shows with season range, with or without year
    tv_show_pattern2 = re.compile(r"^(.*?)([. ]((?:19|20)\d{2}))?[. ]s(\d{2})-s(\d{2})", re.IGNORECASE)
    # Regular expression to match movies
    movie_pattern = re.compile(r"^(.*?)[ .]*(?:\((\d{4})\)|(\d{4}))[ .]*(.*\.*)$", re.IGNORECASE)

    # Regular expression to detect 2160p without h264 or x264
    hdr_pattern = re.compile(r"2160p", re.IGNORECASE)
    non_hdr_pattern = re.compile(r"h264|x264", re.IGNORECASE)

    # Regular expression to detect editions: {edition-Director's Cut}, etc.
    edition_pattern = re.compile(r"{edition-(.*?)}", re.IGNORECASE)

    # Check for HDR
    is_hdr = hdr_pattern.search(filename) and not non_hdr_pattern.search(filename)

    # Try to find an edition in the filename
    edition_match = edition_pattern.search(filename)
    edition_name = None
    if edition_match:
        edition_name = edition_match.group(1).strip()

    tv_match1 = tv_show_pattern1.match(filename)
    tv_match2 = tv_show_pattern2.match(filename)
    movie_match = movie_pattern.match(filename)

    if tv_match1:
        # TV show with season and episode
        showname = tv_match1.group(1).replace('. ', '.')
        showname = showname.replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname)  # Transform to sentence case
        year = tv_match1.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder

        media_type = 'tv_show_hdr' if is_hdr else 'tv_show'

        # Build the base media name
        base_name = f"{showname} ({year})" if year else showname
        # Append edition if found
        if edition_name:
            media_name = f"{base_name} ({edition_name})"
        else:
            media_name = base_name

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            return (
                os.path.join(folder, media_name),
                filename
            )

    elif tv_match2:
        # TV show with season range
        showname = tv_match2.group(1).replace('. ', '.')
        showname = showname.replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname)  # Transform to sentence case
        year = tv_match2.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder

        media_type = 'tv_show_hdr' if is_hdr else 'tv_show'

        # Build the base media name
        base_name = f"{showname} ({year})" if year else showname
        # Append edition if found
        if edition_name:
            media_name = f"{base_name} ({edition_name})"
        else:
            media_name = base_name

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            return (
                os.path.join(folder, media_name),
                filename
            )

    elif movie_match:
        # Movie
        title = movie_match.group(1).replace('. ', '.')
        title = title.replace('.', ' ')
        title = title.replace(' -', '')
        title = to_sentence_case(title)
        year = movie_match.group(2) or movie_match.group(3)
        folder = movie_hdr_folder if is_hdr else movie_folder

        media_type = 'movie_hdr' if is_hdr else 'movie'

        # Build the base media name
        if year:
            base_name = f"{title} ({year})"
        else:
            base_name = title

        # Append edition if found
        if edition_name:
            media_name = f"{base_name} ({edition_name})"
        else:
            media_name = base_name

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            return (
                os.path.join(folder, media_name),
                filename
            )
    else:
        media_type = 'other'
        if edition_name:
            name_only, ext = os.path.splitext(filename)
            media_name = f"{name_only} ({edition_name}){ext}"
        else:
            media_name = filename

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            return others_folder, media_name


# Function to hide the cursor
def hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


# Function to show the cursor
def show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def extract_season_episode(filename):
    # Extract single or multi-episode patterns like S01E01 or S01E01-E02
    match = re.search(r'[sS](\d{2})[eE](\d{2})(?:-[eE]?(\d{2}))?', filename)
    if match:
        season = int(match.group(1))
        start_episode = int(match.group(2))
        end_episode = int(match.group(3)) if match.group(3) else start_episode
        return season, range(start_episode, end_episode + 1)
    return None, None


def compact_names_list(names):
    # Return a shortened preview of a list of filenames.
    if len(names) > 5:
        return names[:2] + ["..."] + names[-2:]
    return names


def compact_episode_list(episodes, zfill=False):
    # Summarize consecutive episode numbers as ranges.
    episodes = sorted(episodes)
    ranges = []
    range_start = range_end = episodes[0]

    for episode in episodes[1:]:
        if episode == range_end + 1:
            range_end = episode
        else:
            ranges.append((range_start, range_end))
            range_start = range_end = episode
    ranges.append((range_start, range_end))

    # Determine the padding logic
    def format_episode(num):
        if zfill:
            return f"{num:02}" if num < 100 else f"{num:03}"
        return str(num)

    # Format the ranges with optional zfill
    return ", ".join(
        f"{format_episode(start)}" if start == end else f"{format_episode(start)}-{format_episode(end)}"
        for start, end in ranges
    )


def print_media_info(logger, filenames):
    # Ignore subtitles
    filenames = [f for f in filenames if f.endswith('.mkv')]

    tv_shows = defaultdict(lambda: defaultdict(set))
    tv_shows_extras = defaultdict(list)
    tv_shows_hdr = defaultdict(lambda: defaultdict(set))
    tv_shows_hdr_extras = defaultdict(list)
    movies = []
    movie_extras = defaultdict(list)
    movies_hdr = []
    movie_hdr_extras = defaultdict(list)
    uncategorized = []

    for filename in filenames:
        file_info = reformat_filename(filename, True)
        media_type = file_info["media_type"]
        media_name = file_info["media_name"]
        base, ext = os.path.splitext(filename)

        # Determine if this is an extra by checking trailing excluded tags.
        is_extra = any(base.lower().endswith(tag) for tag in excluded_tags)

        if media_type in ['tv_show', 'tv_show_hdr']:
            season, episodes = extract_season_episode(filename)
            if is_extra:
                if media_type == 'tv_show':
                    tv_shows_extras[media_name].append(filename)
                else:
                    tv_shows_hdr_extras[media_name].append(filename)
            else:
                if season and episodes:
                    if media_type == 'tv_show':
                        tv_shows[media_name][season].update(episodes)
                    else:
                        tv_shows_hdr[media_name][season].update(episodes)
                else:
                    uncategorized.append(media_name)
        elif media_type in ['movie', 'movie_hdr']:
            if is_extra:
                if media_type == 'movie':
                    movie_extras[media_name].append(filename)
                else:
                    movie_hdr_extras[media_name].append(filename)
            else:
                if media_type == 'movie':
                    movies.append(media_name)
                else:
                    movies_hdr.append(media_name)
        else:
            uncategorized.append(media_name)
    print_no_timestamp(logger, '')
    if tv_shows:
        print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(tv_shows)} TV {print_multi_or_single(len(tv_shows), 'Show')}:")
        for show in sorted(tv_shows):
            for season, episodes in sorted(tv_shows[show].items()):
                episode_list = compact_episode_list(episodes)
                print_no_timestamp(logger, f"  {BLUE}{show}{RESET} (Season {season}, Episode {episode_list})")
            if tv_shows_extras[show]:
                print_no_timestamp(logger, f"  {BLUE}{show}{RESET} (+{len(tv_shows_extras[show])} "
                                           f"{print_multi_or_single(len(tv_shows_extras[show]), 'Extra')})")

    if tv_shows_hdr:
        print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(tv_shows_hdr)} HDR TV {print_multi_or_single(len(tv_shows_hdr), 'Show')}:")
        for show in sorted(tv_shows_hdr):
            for season, episodes in sorted(tv_shows_hdr[show].items()):
                episode_list = compact_episode_list(episodes)
                print_no_timestamp(logger, f"  {BLUE}{show}{RESET} (Season {season}, Episode {episode_list})")
            if tv_shows_hdr_extras[show]:
                print_no_timestamp(logger, f"  {BLUE}{show}{RESET} (+{len(tv_shows_hdr_extras[show])} "
                                           f"{print_multi_or_single(len(tv_shows_hdr_extras[show]), 'Extra')})")

    if movies:
        movies.sort()
        print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(movies)} {print_multi_or_single(len(movies), 'Movie')}:")
        for movie in movies:
            if movie_extras[movie]:
                print_no_timestamp(logger, f"  {BLUE}{movie}{RESET} (+{len(movie_extras[movie])} "
                                           f"{print_multi_or_single(len(movie_extras[movie]), 'Extra')})")
            else:
                print_no_timestamp(logger, f"  {BLUE}{movie}{RESET}")

    if movies_hdr:
        movies_hdr.sort()
        print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(movies_hdr)} HDR {print_multi_or_single(len(movies_hdr), 'Movie')}:")
        for movie in movies_hdr:
            if movie_hdr_extras[movie]:
                print_no_timestamp(logger, f"  {BLUE}{movie}{RESET} (+{len(movie_hdr_extras[movie])} "
                                           f"{print_multi_or_single(len(movie_hdr_extras[movie]), 'Extra')})")
            else:
                print_no_timestamp(logger, f"  {BLUE}{movie}{RESET}")

    if uncategorized:
        uncategorized.sort()
        print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(uncategorized)} Unknown Media:")
        for item in uncategorized:
            print_no_timestamp(logger, f"  {BLUE}{item}{RESET}")

    print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(filenames)} {print_multi_or_single(len(filenames), 'file')} in total.")
    print_no_timestamp(logger, '')


# Initialize configparser
variables_user = configparser.ConfigParser()
variables_defaults = configparser.ConfigParser()

# Load default configurations
if os.path.isfile('defaults.ini'):
    variables_defaults.read('defaults.ini')

# Load user-specific configurations if available
if os.path.isfile('user.ini'):
    variables_user.read('user.ini')
elif os.path.isfile('files/user.ini'):
    variables_user.read('files/user.ini')
else:
    variables_user = variables_defaults

config = {
    'general': {
        'input_folder': get_config('general', 'INPUT_FOLDER', variables_defaults),
        'output_folder': get_config('general', 'OUTPUT_FOLDER', variables_defaults),
        'keep_original': get_config('general', 'KEEP_ORIGINAL', variables_defaults).lower() == "true",
        'ini_temp_dir': get_config('general', 'TEMP_DIR', variables_defaults),
        'file_tag': get_config('general', 'FILE_TAG', variables_defaults),
        'normalize_filenames': get_config('general', 'NORMALIZE_FILENAMES', variables_defaults).lower() == "true",
        'remove_samples': get_config('general', 'REMOVE_SAMPLES', variables_defaults).lower() == "true",
        'movies_folder': get_config('general', 'MOVIES_FOLDER', variables_defaults),
        'movies_hdr_folder': get_config('general', 'MOVIES_HDR_FOLDER', variables_defaults),
        'tv_shows_folder': get_config('general', 'TV_SHOWS_FOLDER', variables_defaults),
        'tv_shows_hdr_folder': get_config('general', 'TV_SHOWS_HDR_FOLDER', variables_defaults),
        'others_folder': get_config('general', 'OTHERS_FOLDER', variables_defaults),
        'max_cpu_usage': get_config('general', 'MAX_CPU_USAGE', variables_defaults)
    },
    'audio': {
        'pref_audio_langs': [item.strip() for item in get_config('audio', 'PREFERRED_AUDIO_LANG', variables_defaults).split(',')],
        'pref_audio_formats': get_config('audio', 'PREFERRED_AUDIO_FORMATS', variables_defaults),
        'remove_commentary': get_config('audio', 'REMOVE_COMMENTARY_TRACK', variables_defaults).lower() == "true"
    },
    'subtitles': {
        'pref_subs_langs': [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')],
        'pref_subs_langs_short': [item.strip()[:-1] for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')],
        'pref_subs_ext': [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_EXT', variables_defaults).split(',')],
        'ocr_languages': [item.strip() for item in get_config('subtitles', 'OCR_LANGUAGES', variables_defaults).split(',')],
        'always_enable_subs': get_config('subtitles', 'ALWAYS_ENABLE_SUBS', variables_defaults).lower() == "true",
        'always_remove_sdh': get_config('subtitles', 'REMOVE_SDH', variables_defaults).lower() == "true",
        'remove_music': get_config('subtitles', 'REMOVE_MUSIC', variables_defaults).lower() == "true",
        'resync_subtitles': get_config('subtitles', 'RESYNC_SUBTITLES', variables_defaults).lower() == "true",
        'keep_original_subtitles': get_config('subtitles', 'KEEP_ORIGINAL_SUBTITLES', variables_defaults).lower() == "true",
        'forced_subtitles_priority': get_config('subtitles', 'FORCED_SUBTITLES_PRIORITY', variables_defaults),
        'prioritize_subtitles': get_config('subtitles', 'PRIORITIZE_SUBTITLES', variables_defaults),
        'download_missing_subs': get_config('subtitles', 'DOWNLOAD_MISSING_SUBS', variables_defaults).lower() == "true",
        'remove_all_subtitles': get_config('subtitles', 'REMOVE_ALL_SUBTITLES', variables_defaults).lower() == "true",
        'main_audio_language_subs_only': get_config('subtitles', 'MAIN_AUDIO_LANGUAGE_SUBS_ONLY', variables_defaults).lower() == "true",
        'redo_casing': get_config('subtitles', 'REDO_CASING', variables_defaults).lower() == "true"
    }
}

max_cpu_usage = check_config(config, 'general', 'max_cpu_usage')
max_workers = int(os.cpu_count() * int(max_cpu_usage) / 100)
