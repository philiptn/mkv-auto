import configparser
import sys
import traceback
import argparse
from itertools import groupby

from scripts.file_operations import *
from scripts.mkv import *
from scripts.subs import *
from scripts.audio import *
from scripts.misc import *

# General
input_folder = get_config('general', 'INPUT_FOLDER', variables_defaults)
output_folder = get_config('general', 'OUTPUT_FOLDER', variables_defaults)
keep_original = True if get_config('general', 'KEEP_ORIGINAL', variables_defaults).lower() == "true" else False
ini_temp_dir = get_config('general', 'TEMP_DIR', variables_defaults)
file_tag = get_config('general', 'FILE_TAG', variables_defaults)
remove_samples = True if get_config('general', 'REMOVE_SAMPLES', variables_defaults).lower() == "true" else False
movies_folder = get_config('general', 'MOVIES_FOLDER', variables_defaults)
movies_hdr_folder = get_config('general', 'MOVIES_HDR_FOLDER', variables_defaults)
tv_shows_folder = get_config('general', 'TV_SHOWS_FOLDER', variables_defaults)
tv_shows_hdr_folder = get_config('general', 'TV_SHOWS_HDR_FOLDER', variables_defaults)
others_folder = get_config('general', 'OTHERS_FOLDER', variables_defaults)

# Audio
pref_audio_langs = [item.strip() for item in get_config('audio', 'PREFERRED_AUDIO_LANG', variables_defaults).split(',')]
pref_audio_codec = get_config('audio', 'PREFERRED_AUDIO_CODEC', variables_defaults)
remove_commentary = True if get_config('audio', 'REMOVE_COMMENTARY_TRACK', variables_defaults).lower() == "true" else False

# Subtitles
pref_subs_langs = [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')]
pref_subs_langs_short = [item.strip()[:-1] for item in get_config('subtitles', 'PREFERRED_SUBS_LANG', variables_defaults).split(',')]
pref_subs_ext = [item.strip() for item in get_config('subtitles', 'PREFERRED_SUBS_EXT', variables_defaults).split(',')]
always_enable_subs = True if get_config('subtitles', 'ALWAYS_ENABLE_SUBS', variables_defaults).lower() == "true" else False
always_remove_sdh = True if get_config('subtitles', 'REMOVE_SDH', variables_defaults).lower() == "true" else False
remove_music = True if get_config('subtitles', 'REMOVE_MUSIC', variables_defaults).lower() == "true" else False
resync_subtitles = True if get_config('subtitles', 'RESYNC_SUBTITLES', variables_defaults).lower() == "true" else False


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


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


def format_time(seconds):
    """Return a formatted string for the given duration in seconds."""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours} {print_multi_or_single(hours, 'hour')}")
    if minutes:
        parts.append(f"{minutes} {print_multi_or_single(minutes, 'minute')}")
    if seconds or not parts:  # If it's 0 seconds, we want to include it.
        parts.append(f"{seconds} {print_multi_or_single(seconds, 'second')}")

    if seconds and (not hours and not minutes):
        return f"{seconds} {print_multi_or_single(seconds, 'second')}"
    else:
        return " ".join(parts)


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


def mt_mkv_auto(args):
    input_dir = check_config(config, 'general', 'input_folder')
    output_dir = check_config(config, 'general', 'output_folder')

    if keep_original:
        move_files = False
    else:
        move_files = True
    if args.move:
        move_files = True

    if args.docker:
        input_dir = 'files/input'
        output_dir = 'files/output'
    if args.input_dir:
        input_dir = args.input_dir
    if args.output_dir:
        output_dir = args.output_dir

    if args.debug:
        debug = True
    else:
        debug = False

    # If the temp dir location is unchanged from default and
    # set to run in Docker, set default to inside 'files/' folder
    if ini_temp_dir == '.tmp/' and args.docker:
        temp_dir = 'files/tmp/'
    else:
        temp_dir = ini_temp_dir
    if args.temp_dir:
        temp_dir = args.temp_dir

    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

    if not move_files:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.mkdir(temp_dir)

    total_files = count_files(input_dir)
    total_bytes = count_bytes(input_dir)

    if total_files == 0:
        if not args.silent:
            print(f"No mkv files found in input directory.\n")
        exit(0)

    if not args.silent:
        hide_cursor()

    if not move_files:
        with tqdm(total=total_bytes, unit='B', unit_scale=True, unit_divisor=1024,
                  bar_format='\r{desc}{bar:10} {percentage:3.0f}%', leave=False, disable=args.silent) as pbar:
            pbar.set_description(f"{GREY}[INFO]{RESET} Copying file 1 of {total_files}")
            copy_directory_contents(input_dir, temp_dir, pbar, total_files=total_files)
        input_dir = temp_dir
    if move_files and not debug or move_files and args.service:
        with tqdm(total=total_bytes, unit='B', unit_scale=True, unit_divisor=1024,
                  bar_format='\r{desc}{bar:10} {percentage:3.0f}%', leave=False, disable=args.silent) as pbar:
            pbar.set_description(f"{GREY}[INFO]{RESET} Moving file 1 of {total_files}")
            move_directory_contents(input_dir, temp_dir, pbar, total_files=total_files)
        input_dir = temp_dir

    if not args.silent:
        show_cursor()

    convert_all_videos_to_mkv(debug, input_dir, args.silent)
    rename_others_file_to_folder(input_dir)

    if not args.silent:
        # Show the cursor
        sys.stdout.write('\033[?25h')
        sys.stdout.flush()

    extract_archives(input_dir)

    if remove_samples:
        remove_sample_files_and_dirs(input_dir)

    fix_episodes_naming(input_dir)
    remove_ds_store(input_dir)
    remove_wsl_identifiers(input_dir)

    total_files = get_total_mkv_files(input_dir)
    if total_files == 0:
        if not args.silent:
            print(f"No mkv files found in input directory.\n")
        exit(0)

    dirpaths = []
    print('')

    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames.sort(key=str.lower)  # sort directories in-place in case-insensitive manner

        # Skip directories or files starting with '.'
        if '/.' in dirpath or dirpath.startswith('./.'):
            continue

        if not dirpath == 'input/':
            dirpaths.append(dirpath)

        audio_tracks_to_be_merged = []
        subtitle_tracks_to_be_merged = []

        # Ignore files that start with a dot
        filenames = [f for f in filenames if not f.startswith('.')]

        # Extract the directory path relative to the input directory
        relative_dir_path = os.path.relpath(dirpath, input_dir)
        # Split the relative path into individual directories
        all_dirnames = relative_dir_path.split(os.sep)

        """
        Main loop
        """

        # Remove all filenames that are not mkv or srt
        filenames = [f for f in filenames if f.endswith('.mkv') or f.endswith('.srt')]
        # Remove all filenames that are not mkv
        filenames_mkv_only = [f for f in filenames if f.endswith('.mkv')]

        print_media_info(filenames)

        print(f"{GREY}[UTC {get_timestamp()}] [INFO]{RESET} Using {max_workers} CPU threads for processing.")
        start_time = time.time()

        need_processing_audio, need_processing_subs = trim_audio_and_subtitles_in_mkv_files(debug, max_workers, filenames_mkv_only, dirpath)
        audio_tracks_to_be_merged, subtitle_tracks_to_be_merged = generate_audio_tracks_in_mkv_files(debug, max_workers, filenames_mkv_only, dirpath)

        if any(need_processing_subs):
            check_needed_processing_subs(debug, max_workers, filenames_mkv_only, dirpath)

            subtitle_tracks_to_be_merged, subtitle_files_to_process = convert_picture_based_to_srt(debug, max_workers, filenames_mkv_only, dirpath)

            if subtitle_files_to_process:
                remove_sdh_process(debug, max_workers, subtitle_files_to_process)
                resync_sub_process(debug, max_workers, filenames_mkv_only, dirpath, subtitle_files_to_process)

        if any(need_processing_audio) or any(need_processing_subs):
            print(filenames_mkv_only)
            print(audio_tracks_to_be_merged)
            print(subtitle_tracks_to_be_merged)
            exit(0)
            repack_mkv_tracks_process(debug, max_workers, filenames_mkv_only, dirpath, audio_tracks_to_be_merged, subtitle_tracks_to_be_merged)

        move_files_to_output_process(debug, max_workers, filenames_mkv_only, dirpath, all_dirnames, output_dir)

        #if any(file.endswith('.srt') for file in filenames):
        #    process_external_subs(debug, max_workers, dirpath, dirnames, filenames, output_dir, all_dirnames)

        end_time = time.time()
        processing_time = end_time - start_time

        print(f"\n{GREY}[INFO]{RESET} All files successfully processed.")
        print(f"{GREY}[INFO]{RESET} Processing took {format_time(int(processing_time))} to complete.\n")

    exit(0)


def main():
    # Create the main parser
    parser = argparse.ArgumentParser(description="A tool that aims to remove unnecessary clutter "
                                                 "from Matroska (.mkv) files by "
                                                 "removing and/or converting any audio or "
                                                 "subtitle tracks from the source video.")
    parser.add_argument("--input_folder", "-if", dest="input_dir", type=str, required=False,
                        help="input folder path (default: 'input/')")
    parser.add_argument("--output_folder", "-of", dest="output_dir", type=str, required=False,
                        help="output folder path (default: 'output/')")
    parser.add_argument("--temp_folder", "-tf", dest="temp_dir", type=str, required=False,
                        help="temp folder path (default: '.tmp/')")
    parser.add_argument("--silent", action="store_true", default=False, required=False,
                        help="supress visual elements like progress bars (default: False)")
    parser.add_argument("--move", action="store_true", default=False, required=False,
                        help="process files directly by moving them, no copying (default: False)")
    parser.add_argument("--docker", action="store_true", default=False, required=False,
                        help="use docker-specific default directories from 'files/' (default: False)")
    parser.add_argument("--debug", action="store_true", default=False, required=False,
                        help="print debugging information such as track selection, codecs, prefs etc. (default: False)")
    parser.add_argument("--service", action="store_true", default=False, required=False,
                        help="disables debug pause if enabled (default: False)")

    parser.set_defaults(func=mt_mkv_auto)
    args = parser.parse_args()

    # Call the function associated with the active sub-parser
    args.func(args)

    # Run mkv_auto function if no argument is given
    if len(sys.argv) < 2:
        mt_mkv_auto(args)


# Call the main() function if this file is directly executed
if __name__ == '__main__':
    main()
