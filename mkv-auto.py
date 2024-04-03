import configparser
import sys
import traceback
import argparse
from configparser import ConfigParser
from itertools import groupby

from scripts.file_operations import *
from scripts.mkv import *
from scripts.ocr import *
from scripts.srt import *

# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'
RED = '\033[31m'
GREEN = '\033[32m'

# Get user preferences
variables = ConfigParser()

# If user-specific config file has been created, load it
# else, load defaults from preferences.ini
if os.path.isfile('user.ini'):
    variables.read('user.ini')
elif os.path.isfile('files/user.ini'):
    variables.read('files/user.ini')
else:
    variables.read('defaults.ini')

try:
    # General
    input_folder = variables.get('general', 'INPUT_FOLDER')
    output_folder = variables.get('general', 'OUTPUT_FOLDER')
    keep_original = True if variables.get('general', 'KEEP_ORIGINAL').lower() == "true" else False
    ini_temp_dir = variables.get('general', 'TEMP_DIR')
    file_tag = variables.get('general', 'FILE_TAG')
    flatten_directories = True if variables.get('general', 'FLATTEN_DIRECTORIES').lower() == "true" else False
    remove_samples = True if variables.get('general', 'REMOVE_SAMPLES').lower() == "true" else False
    movies_folder = variables.get('general', 'MOVIES_FOLDER')
    movies_hdr_folder = variables.get('general', 'MOVIES_HDR_FOLDER')
    tv_shows_folder = variables.get('general', 'TV_SHOWS_FOLDER')
    tv_shows_hdr_folder = variables.get('general', 'TV_SHOWS_HDR_FOLDER')
    others_folder = variables.get('general', 'OTHERS_FOLDER')

    # Audio
    pref_audio_langs = [item.strip() for item in variables.get('audio', 'PREFERRED_AUDIO_LANG').split(',')]
    pref_audio_codec = variables.get('audio', 'PREFERRED_AUDIO_CODEC')
    remove_commentary = True if variables.get('audio', 'REMOVE_COMMENTARY_TRACK').lower() == "true" else False

    # Subtitles
    pref_subs_langs = [item.strip() for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
    pref_subs_langs_short = [item.strip()[:-1] for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
    always_enable_subs = True if variables.get('subtitles', 'ALWAYS_ENABLE_SUBS').lower() == "true" else False
    always_remove_sdh = True if variables.get('subtitles', 'REMOVE_SDH').lower() == "true" else False
    remove_music = True if variables.get('subtitles', 'REMOVE_MUSIC').lower() == "true" else False
    resync_subtitles = True if variables.get('subtitles', 'RESYNC_SUBTITLES').lower() == "true" else False
except configparser.NoOptionError:
    print("Error: Some fields are missing from 'user.ini'. Check 'defaults.ini' for reference.\n")
    exit(1)


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


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


def mkv_auto(args):
    input_dir = input_folder
    output_dir = output_folder

    if keep_original:
        notemp = False
    else:
        notemp = True
    if args.notemp:
        notemp = True

    if args.docker:
        if not input_dir:
            input_dir = 'files/input'
        if not output_dir:
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

    if not notemp:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.mkdir(temp_dir)

    total_files = count_files(input_dir)
    total_bytes = count_bytes(input_dir)

    # print('')
    if not args.silent:
        # Hide the cursor
        sys.stdout.write('\033[?25l')
        sys.stdout.flush()

    if not notemp:
        with tqdm(total=total_bytes, unit='B', unit_scale=True, unit_divisor=1024,
                  bar_format='\r{desc}{bar:10} {percentage:3.0f}%', leave=False, disable=args.silent) as pbar:
            pbar.set_description(f"{GREY}[INFO]{RESET} Copying file 1 of {total_files}")
            copy_directory_contents(input_dir, temp_dir, pbar, total_files=total_files)
        input_dir = temp_dir

    convert_all_videos_to_mkv(debug, input_dir, args.silent)
    rename_others_file_to_folder(input_dir, movies_folder, tv_shows_folder, movies_hdr_folder, tv_shows_hdr_folder,
                                 others_folder)

    if not args.silent:
        # Show the cursor
        sys.stdout.write('\033[?25h')
        sys.stdout.flush()

    extract_archives(input_dir)

    if remove_samples:
        remove_sample_files_and_dirs(input_dir)

    if flatten_directories:
        flatten_dirs(input_dir)

    fix_episodes_naming(input_dir)
    remove_ds_store(input_dir)

    total_files = get_total_mkv_files(input_dir)
    file_index = 1

    if total_files == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not args.silent:
            # Show the cursor
            sys.stdout.write('\033[?25h')
            sys.stdout.flush()
            print(f"No mkv files found in input directory.\n")
        exit(0)

    errored_file_names = []
    dirpaths = []
    total_processing_time = 0
    filenames = []
    mkv_files_list = []
    flat_all_dirnames = []

    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames.sort(key=str.lower)  # sort directories in-place in case-insensitive manner

        # Skip directories or files starting with '.'
        if '/.' in dirpath or dirpath.startswith('./.'):
            continue

        if not dirpath == 'input/':
            dirpaths.append(dirpath)

        structure = os.path.join(output_dir, os.path.relpath(dirpath, input_dir))

        input_file_mkv = ''
        input_file_mkv_nopath = ''
        input_file = ''
        file_name_printed = False
        external_subs_print = True
        quiet = False

        def split_filename(filename):
            match = re.match(r'^(.*?\d+)\.(.*)(\.\w{2,3})$', filename)
            if match:
                base_name, rest, extension = match.groups()
                lang_code = rest.split('.')[-1] if extension == '.srt' else ''
                return base_name, extension_priority(extension), lang_code, rest
            else:
                return filename, 3, '', ''

        def extension_priority(extension):
            if extension == ".srt":
                return 0
            elif extension == ".mkv":
                return 2
            else:
                return 1

        # Ignore files that start with a dot
        filenames = [f for f in filenames if not f.startswith('.')]

        # Sort filenames using the custom sort function
        filenames.sort(key=split_filename)

        # Group the filenames by base_name
        for base_name, group in groupby(filenames, key=lambda x: split_filename(x)[0]):
            grouped_files = list(group)
            # Within each group, sort the files first by extension priority and then by language code
            grouped_files.sort(key=lambda x: (split_filename(x)[1], split_filename(x)[2]))

        mkv_file_found = False
        needs_tag_rename = False

        for index, file_name in enumerate(filenames):
            all_dirnames = []
            try:
                start_time = time.time()

                if file_name.startswith('.'):
                    continue

                input_file = os.path.join(dirpath, file_name)
                output_file = os.path.join(structure, file_name)

                needs_tag_rename = True

                parts = file_name.split('.')
                language_prefix = parts[-2]  # The language prefix is always the second to last part

                if file_name.endswith('.srt'):
                    # If the SRT file does not have any language prefix, assume it is 'eng'
                    if len(language_prefix) > 3:
                        language_prefix = 'eng'
                    if language_prefix in pref_subs_langs_short or language_prefix in pref_subs_langs:
                        if not mkv_file_found:
                            last_processed_mkv = ''
                            try:
                                with open(".last_processed_mkv.txt", "r") as f:
                                    last_processed_mkv = f.read().strip()
                            except FileNotFoundError:
                                pass

                            mkv_files = [file for file in filenames if
                                         file.endswith('.mkv') and file != last_processed_mkv]
                            if mkv_files:
                                file_name = mkv_files[0]
                                input_file_mkv = os.path.join(dirpath, str(file_name))
                                input_file_mkv_nopath = str(file_name)
                                with open(".last_processed_mkv.txt", "w") as f:
                                    f.write(file_name)
                                mkv_file_found = True

                        if not file_name_printed:
                            print(f"{GREY}[INFO]{RESET} Processing file {file_index} of {total_files}:\n")
                            print(f"{GREY}[UTC {get_timestamp()}] [FILE]{RESET} '{input_file_mkv_nopath}'")
                            file_name_printed = True
                        if external_subs_print:
                            quiet = True
                        input_files = [input_file]
                        if always_remove_sdh or remove_music:
                            if external_subs_print:
                                print(f"{GREY}[UTC {get_timestamp()}] [SRT_EXT]{RESET} Removing SDH in external subtitles...")
                            remove_sdh(debug, input_files, quiet, remove_music)
                        if resync_subtitles:
                            if external_subs_print:
                                print(
                                    f"{GREY}[UTC {get_timestamp()}] [SRT_EXT]{RESET} Synchronizing external subtitles to audio track...")
                            resync_srt_subs(debug, input_file_mkv, input_files, quiet)
                        external_subs_print = False

                        if needs_tag_rename:
                            if file_tag != "default":
                                updated_filename = replace_tags_in_file(input_file, file_tag)
                                file_name = updated_filename

                                input_file = os.path.join(dirpath, file_name)
                                output_file = os.path.join(structure, file_name)

                        move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
                                            movies_hdr_folder, tv_shows_hdr_folder, others_folder, all_dirnames,
                                            flatten_directories)
                        continue
                    else:
                        os.remove(input_file)
                        continue

                elif file_name.endswith('.mkv'):
                    mkv_files_list.append(file_name)
                    mkv_file_found = False
                    pref_audio_codec_found = False
                    track_ids_to_be_converted = []
                    track_langs_to_be_converted = []
                    other_track_ids = []
                    other_track_langs = []
                    extracted_other_audio_files = []
                    extracted_other_audio_langs = []
                    extracted_audio_extensions = []
                    ready_audio_extensions = []
                    ready_audio_langs = []
                    ready_track_ids = []
                    keep_original_audio = True
                    all_subs_track_ids = []

                    # Construct the full path to the .mkv file
                    full_path = os.path.join(dirpath, file_name)
                    # Extract the directory path relative to the input directory
                    relative_dir_path = os.path.relpath(dirpath, input_dir)
                    # Split the relative path into individual directories
                    all_dirnames = relative_dir_path.split(os.sep)
                    # Reset dirnames to avoid traversing further as we already found an .mkv file
                    dirnames[:] = []

                    external_subs_print = True
                    quiet = False
                    output_file_mkv = output_file

                    if not file_name_printed:
                        print(f"{GREY}[INFO]{RESET} Processing file {file_index} of {total_files}:\n")
                        print(f"{GREY}[UTC {get_timestamp()}] [FILE]{RESET} '{file_name}'")
                        file_name_printed = True

                    # Get file info using mkvinfo
                    file_info, pretty_file_info = get_mkv_info(debug, input_file, args.silent)
                    # Get video codec
                    mkv_video_codec = get_mkv_video_codec(input_file)

                    wanted_audio_tracks, default_audio_track, needs_processing_audio, \
                        pref_audio_codec_found, track_ids_to_be_converted, \
                        track_langs_to_be_converted, other_track_ids, other_track_langs = get_wanted_audio_tracks(
                        debug, file_info, pref_audio_langs, remove_commentary, pref_audio_codec)

                    wanted_subs_tracks, default_subs_track, \
                        needs_sdh_removal, needs_convert, a, b, needs_processing_subs = get_wanted_subtitle_tracks(
                        debug, file_info, pref_subs_langs)

                    if needs_processing_audio:
                        strip_tracks_in_mkv(debug, input_file, wanted_audio_tracks, default_audio_track,
                                            wanted_subs_tracks, default_subs_track, always_enable_subs)
                    else:
                        print(f"{GREY}[UTC {get_timestamp()}] [MKVMERGE]{RESET} No audio track filtering needed.")

                    # If the preferred audio codec is set to AAC or OPUS, the purpose is probably to save on storage space.
                    # Force-enabling the encoding regardless of the audio track already found, as well as removing
                    # the original audio track.
                    if pref_audio_codec.lower() == 'aac' or pref_audio_codec.lower() == 'opus':
                        pref_audio_codec_found = False
                        keep_original_audio = False

                    if not needs_processing_audio:
                        after_reduction_debug = False
                    else:
                        after_reduction_debug = debug

                    # Get updated file info after mkv tracks reduction
                    file_info, pretty_file_info = get_mkv_info(after_reduction_debug, input_file, args.silent)

                    wanted_audio_tracks, default_audio_track, needs_processing_audio, \
                        pref_audio_codec_found, track_ids_to_be_converted, \
                        track_langs_to_be_converted, other_track_ids, other_track_langs = get_wanted_audio_tracks(
                        debug, file_info, pref_audio_langs, remove_commentary, pref_audio_codec)

                    # Generating audio tracks if preferred codec not found in all audio tracks
                    if needs_processing_audio:
                        print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Extracting audio...")

                        if other_track_ids:
                            extracted_other_audio_files, extracted_other_audio_langs, \
                                extracted_audio_extensions = extract_audio_tracks_in_mkv(debug, input_file,
                                                                                         other_track_ids,
                                                                                         other_track_langs)

                        if track_langs_to_be_converted:
                            extracted_for_convert_audio_files, extracted_for_convert_audio_langs, \
                                extracted_audio_extensions = extract_audio_tracks_in_mkv(debug, input_file,
                                                                                         track_ids_to_be_converted,
                                                                                         track_langs_to_be_converted)

                            ready_audio_extensions, ready_audio_langs, ready_track_ids = encode_audio_tracks(
                                debug, extracted_for_convert_audio_files, extracted_for_convert_audio_langs,
                                pref_audio_codec, extracted_other_audio_files, extracted_other_audio_langs,
                                keep_original_audio, other_track_ids)
                        else:
                            ready_audio_extensions = extracted_audio_extensions
                            ready_audio_langs = extracted_other_audio_langs
                            ready_track_ids = other_track_ids

                        # Set to true regardless, to invoke processing pipeline
                        needs_processing_subs = True

                    if needs_processing_subs:
                        subtitle_files = []
                        # Get updated file info after mkv tracks reduction
                        file_info, pretty_file_info = get_mkv_info(False, input_file, args.silent)
                        wanted_subs_tracks, a, b, needs_convert, \
                            sub_filetypes, subs_track_languages, e = get_wanted_subtitle_tracks(debug, file_info,
                                                                                                pref_subs_langs)

                        updated_subtitle_languages = subs_track_languages
                        all_subs_track_ids = wanted_subs_tracks

                        # Check if any of the subtitle tracks needs to be converted using OCR
                        if needs_convert:
                            print(f"{GREY}[UTC {get_timestamp()}] [MKVEXTRACT]{RESET} Extracting subtitles for OCR...")
                            output_subtitles = []
                            generated_srt_files = []

                            if "sub" in sub_filetypes:
                                subtitle_files = extract_subs_in_mkv(debug, input_file, wanted_subs_tracks,
                                                                     sub_filetypes, subs_track_languages)

                                output_subtitles, updated_subtitle_languages, generated_srt_files, all_subs_track_ids = ocr_subtitles(
                                    debug, subtitle_files, subs_track_languages)

                            elif "sup" in sub_filetypes:
                                subtitle_files = extract_subs_in_mkv(debug, input_file, wanted_subs_tracks,
                                                                     sub_filetypes, subs_track_languages)

                                output_subtitles, updated_subtitle_languages, generated_srt_files, all_subs_track_ids = ocr_subtitles(
                                    debug, subtitle_files, subs_track_languages)

                            elif "ass" in sub_filetypes:
                                subtitle_files = extract_subs_in_mkv(debug, input_file, wanted_subs_tracks,
                                                                     sub_filetypes, subs_track_languages)

                                output_subtitles, updated_subtitle_languages, generated_srt_files, all_subs_track_ids = convert_ass_to_srt(
                                    subtitle_files, subs_track_languages)

                            if always_remove_sdh:
                                remove_sdh(debug, output_subtitles, quiet, remove_music)
                                needs_sdh_removal = False

                            if resync_subtitles:
                                resync_srt_subs(debug, input_file, output_subtitles, quiet)

                            replaced_index = 0
                            for i, ext in enumerate(generated_srt_files):
                                sub_filetypes.insert(i + replaced_index, ext)
                                replaced_index += 1

                            if has_closed_captions(input_file):
                                # Will remove hidden CC data as long as
                                # video codec is not MPEG2 (DVD)
                                if mkv_video_codec != 'MPEG-1/2':
                                    remove_cc_hidden_in_file(debug, input_file)

                            repack_tracks_in_mkv(debug, input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs,
                                                 ready_audio_extensions, ready_audio_langs, pref_audio_langs,
                                                 ready_track_ids, all_subs_track_ids)

                        elif not needs_convert:
                            if needs_sdh_removal and always_remove_sdh or resync_subtitles != 'false':
                                subtitle_files = extract_subs_in_mkv(debug, input_file, wanted_subs_tracks,
                                                                     sub_filetypes, subs_track_languages)

                            if needs_sdh_removal and (always_remove_sdh or remove_music) and subtitle_files:
                                remove_sdh(debug, subtitle_files, quiet, remove_music)

                            if resync_subtitles != 'false' and subtitle_files:
                                if resync_subtitles:
                                    resync_srt_subs(debug, input_file, subtitle_files, quiet)

                            if has_closed_captions(input_file):
                                if mkv_video_codec != 'MPEG-1/2':
                                    remove_cc_hidden_in_file(debug, input_file)

                            repack_tracks_in_mkv(debug, input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs,
                                                 ready_audio_extensions, ready_audio_langs, pref_audio_langs,
                                                 ready_track_ids, all_subs_track_ids)

                    if needs_processing_subs:
                        remove_all_mkv_track_tags(debug, input_file)

                    if needs_tag_rename:
                        if file_tag != "default":
                            updated_filename = replace_tags_in_file(input_file, file_tag)
                            file_name = updated_filename

                            input_file = os.path.join(dirpath, file_name)

                    end_time = time.time()
                    processing_time = end_time - start_time
                    total_processing_time += processing_time
                    print(f"{GREY}[UTC {get_timestamp()}] [INFO]{RESET} Processing time: {format_time(int(processing_time))}")

                    if debug:
                        # Print final mkv structure
                        get_mkv_info(debug, input_file, args.silent)

                    print(f"{GREY}[UTC {get_timestamp()}] [INFO]{RESET} Moving file to destination folder...")
                    move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
                                        movies_hdr_folder, tv_shows_hdr_folder, others_folder, all_dirnames,
                                        flatten_directories)
                    file_index += 1
                    file_name_printed = False

                    print('')
                else:
                    continue
            except Exception as e:
                traceback.print_tb(e.__traceback__)
                # If some of the functions were to fail, move the file unprocessed instead
                if not args.silent:
                    # Show the cursor
                    sys.stdout.write('\033[?25h')
                    sys.stdout.flush()
                print(
                    f"{GREY}[UTC {get_timestamp()}]{RESET} {RED}[ERROR]{RESET} "
                    f"An unknown error occured. Skipping processing...\n---\n{e}\n---\n")
                errored_file_names.append(file_name)

                move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
                                    movies_hdr_folder, tv_shows_hdr_folder, others_folder, all_dirnames,
                                    flatten_directories)

                file_index += 1
                file_name_printed = False
                print('')

                continue

    if len(errored_file_names) == 0:
        # Sorting the dirpaths such that entries with
        # the longest subdirectories are removed first
        base_depth = input_dir.count('/')
        dirpaths.sort(key=lambda path: path.count('/'), reverse=True)
        for dirpath in dirpaths:
            if dirpath.count('/') > base_depth:  # ensure we're not deleting directories at or above base_depth
                safe_delete_dir(dirpath)

        if os.path.exists('.last_processed_mkv.txt'):
            os.remove('.last_processed_mkv.txt')

        # Calculate average (using float division)
        average_time = total_processing_time / len(mkv_files_list)

        print(f"{GREY}[INFO]{RESET} All files successfully processed.")
        print(f"{GREY}[INFO]{RESET} Processing took {format_time(int(total_processing_time))} to complete.")
        print(f"{GREY}[INFO]{RESET} The average file took {format_time(int(average_time))} to process.\n")

        if not notemp:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    else:
        if os.path.exists('.last_processed_mkv.txt'):
            os.remove('.last_processed_mkv.txt')
        if len(errored_file_names) > 1:
            error_str = 'errors'
            files_str = 'files'
        else:
            error_str = 'error'
            files_str = 'file'
        print(f"{GREY}[INFO]{RESET} During processing {len(errored_file_names)} {error_str} occurred in {files_str}:")
        for file in errored_file_names:
            print(f"'{file}'")
        print('')

    exit(0)


def main():
    # Create the main parser
    parser = argparse.ArgumentParser(description="A tool that aims to remove necessary clutter from Matroska (.mkv) files by"
                                                 "removing and/or converting any audio or subtitle tracks from the source file(s).")
    parser.add_argument("--input_folder", "-if", dest="input_dir", type=str, required=False,
                        help="input folder path (default: 'input/')")
    parser.add_argument("--output_folder", "-of", dest="output_dir", type=str, required=False,
                        help="output folder path (default: 'output/')")
    parser.add_argument("--silent", action="store_true", default=False, required=False,
                        help="supress visual elements like progress bars (default: False)")
    parser.add_argument("--notemp", action="store_true", default=False, required=False,
                        help="process files directly without using temp dir (default: False)")
    parser.add_argument("--docker", action="store_true", default=False, required=False,
                        help="use docker-specific default directories from 'files/' (default: False)")
    parser.add_argument("--debug", action="store_true", default=False, required=False,
                        help="print debugging information such as track selection, codecs, prefs etc.")

    parser.set_defaults(func=mkv_auto)
    args = parser.parse_args()

    # Call the function associated with the active sub-parser
    args.func(args)

    # Run mkv_auto function if no argument is given
    if len(sys.argv) < 2:
        mkv_auto(args)


# Call the main() function if this file is directly executed
if __name__ == '__main__':
    main()
