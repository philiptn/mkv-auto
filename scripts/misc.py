import os
import sys
from datetime import datetime
from backports import configparser
import re
from collections import defaultdict
import traceback
import shutil


# ANSI color codes
BLUE = '\033[94m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[93m'
RED = '\033[91m'
GREEN = '\033[92m'


def print_multi_or_single(amount, string):
    if amount == 1:
        return string
    elif amount > 1:
        return f"{string}s"
    else:
        return string


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


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def flatten_directories(directory):
    # Walk through the directory
    for root, dirs, files in os.walk(directory, topdown=False):
        # Skip directories starting with a dot
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        # Skip files starting with a dot
        files = [f for f in files if not f.startswith('.')]

        if any(part.startswith('.') for part in root.split(os.sep)):
            continue

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
        print(f"WARNING: {option} not found in section {section}.")
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
    movie_pattern = re.compile(r"^(.*?)[ .]*(?:\((\d{4})\)|(\d{4}))[ .]*(.*\.(mkv|srt))$", re.IGNORECASE)

    # Regular expression to detect 2160p without h264 or x264
    hdr_pattern = re.compile(r"2160p", re.IGNORECASE)
    non_hdr_pattern = re.compile(r"h264|x264", re.IGNORECASE)

    is_hdr = hdr_pattern.search(filename) and not non_hdr_pattern.search(filename)

    tv_match1 = tv_show_pattern1.match(filename)
    tv_match2 = tv_show_pattern2.match(filename)
    movie_match = movie_pattern.match(filename)

    if tv_match1:
        # TV show with season and episode
        showname = tv_match1.group(1).replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname)  # Transform to sentence case
        year = tv_match1.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder

        media_type = 'tv_show_hdr' if is_hdr else 'tv_show'
        media_name = f"{showname} ({year})" if year else showname

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            # Format the filename
            return os.path.join(folder, f"{showname} ({year})") if year else os.path.join(folder, showname), filename
    elif tv_match2:
        # TV show with season range
        showname = tv_match2.group(1).replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname)  # Transform to sentence case
        year = tv_match2.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder

        media_type = 'tv_show_hdr' if is_hdr else 'tv_show'
        media_name = f"{showname} ({year})" if year else showname

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            # Format the filename
            return os.path.join(folder, f"{showname} ({year})") if year else os.path.join(folder, showname), filename
    elif movie_match:
        # Movie
        title = movie_match.group(1).replace('.', ' ')
        title = title.replace(' -', '')
        title = to_sentence_case(title)
        year = movie_match.group(2) or movie_match.group(3)
        folder = movie_hdr_folder if is_hdr else movie_folder

        media_type = 'movie_hdr' if is_hdr else 'movie'
        media_name = f"{title} ({year})" if year else title

        if names_only:
            return {
                'media_type': media_type,
                'media_name': media_name
            }
        else:
            # Format the filename
            return os.path.join(folder, f"{title} ({year})") if year else os.path.join(folder, title), filename
    else:
        if names_only:
            return {
                'media_type': 'other',
                'media_name': filename
            }
        else:
            # Unidentified file
            return os.path.join(others_folder, filename), 'None'


# Function to hide the cursor
def hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

# Function to show the cursor
def show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()
    os.system('stty sane')


def extract_season_episode(filename):
    # Extracts season and episode number from filename
    match = re.search(r'[sS](\d{2})[eE](\d{2})', filename)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def compact_names_list(names):
    # Generates a preview of the filenames
    if len(names) > 5:
        return ames[:2] + ["..."] + names[-2:]
    return names


def compact_episode_list(episodes):
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

    return ", ".join(f"{start}" if start == end else f"{start}-{end}" for start, end in ranges)


def print_media_info(filenames):
    # Remove SRT subtitles from count list
    filenames = [f for f in filenames if not f.endswith('.srt')]

    total_files = len(filenames)
    tv_shows = defaultdict(lambda: defaultdict(set))
    movies = []
    uncategorized = []

    for filename in filenames:
        file_info = reformat_filename(filename, True)
        if 'tv_show' in file_info["media_type"]:
            season, episode = extract_season_episode(filename)
            if season and episode:
                show_name = file_info["media_name"]
                tv_shows[show_name][season].add(episode)
        elif 'movie' in file_info["media_type"]:
            movies.append(file_info["media_name"])
        else:
            uncategorized.append(file_info["media_name"])

    if tv_shows:
        print(f"{GREY}[INFO]{RESET} {len(tv_shows)} TV {print_multi_or_single(len(tv_shows), 'Show')}:")
        for show, seasons in tv_shows.items():
            for season, episodes in sorted(seasons.items()):
                episode_list = compact_episode_list(episodes)
                print(f"  {BLUE}{show}{RESET} (Season {season}, Episode {episode_list})")
    if movies:
        print(f"{GREY}[INFO]{RESET} {len(movies)} {print_multi_or_single(len(movies), 'Movie')}:")
        movies = compact_names_list(movies)
        for movie in movies:
            print(f"  {BLUE}{movie}{RESET}")
    if uncategorized:
        print(f"{GREY}[INFO]{RESET} {len(uncategorized)} Unknown Media:")
        uncategorized = compact_names_list(uncategorized)
        for uncategorized_item in uncategorized:
            print(f"  {BLUE}{uncategorized_item}{RESET}")
    print(f"{GREY}[INFO]{RESET} {len(filenames)} {print_multi_or_single(len(filenames), 'file')} in total.")
    print('')


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
        'pref_audio_codec': get_config('audio', 'PREFERRED_AUDIO_CODEC', variables_defaults),
        'remove_commentary': get_config('audio', 'REMOVE_COMMENTARY_TRACK', variables_defaults).lower() == "true"
    },
    'subtitles': {
        'pref_subs_langs': [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')],
        'pref_subs_langs_short': [item.strip()[:-1] for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')],
        'pref_subs_ext': [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_EXT', variables_defaults).split(',')],
        'always_enable_subs': get_config('subtitles', 'ALWAYS_ENABLE_SUBS', variables_defaults).lower() == "true",
        'always_remove_sdh': get_config('subtitles', 'REMOVE_SDH', variables_defaults).lower() == "true",
        'remove_music': get_config('subtitles', 'REMOVE_MUSIC', variables_defaults).lower() == "true",
        'resync_subtitles': get_config('subtitles', 'RESYNC_SUBTITLES', variables_defaults).lower() == "true",
        'download_missing_subs': get_config('subtitles', 'DOWNLOAD_MISSING_SUBS', variables_defaults).lower() == "true"
    }
}

# Calculate max_workers as 80% of the available logical cores
max_cpu_usage = check_config(config, 'general', 'max_cpu_usage')
max_workers = int(os.cpu_count() * int(max_cpu_usage) / 100)