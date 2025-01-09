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
from scripts.logger import *


def mkv_auto(args):
    input_dir = check_config(config, 'general', 'input_folder')
    output_dir = check_config(config, 'general', 'output_folder')
    keep_original = check_config(config, 'general', 'keep_original')
    ini_temp_dir = check_config(config, 'general', 'ini_temp_dir')
    remove_samples = check_config(config, 'general', 'remove_samples')

    # Create the logger
    logger = setup_logger(args.log_file)

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

    clear_temp_txt_file = os.path.join(temp_dir, '.clear-temp.txt')
    if os.path.exists(temp_dir) and os.path.exists(clear_temp_txt_file):
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

    if not move_files:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.mkdir(temp_dir)

    total_files = count_files(input_dir)

    if total_files == 0:
        if not args.silent:
            print_no_timestamp(logger, f"No mkv files found in input directory.\n")
            show_cursor()
        exit(0)

    hide_cursor()

    if move_files:
        print_with_progress_files(logger, 0, total_files, header='INFO', description='Moving file')
    else:
        print_with_progress_files(logger, 0, total_files, header='INFO', description='Copying file')

    done_info = {'skipped_files': 0}
    if move_files:
        remaining_files = wait_for_stable_files(input_dir)
        while remaining_files:
            files_in_temp = count_files(temp_dir)
            all_files = remaining_files + files_in_temp
            done_info = move_directory_contents(logger, input_dir, temp_dir, total_files=all_files)
            remaining_files = wait_for_stable_files(input_dir)
            if done_info['skipped_files'] > 0:
                break
    else:
        remaining_files = wait_for_stable_files(input_dir)
        done_info = copy_directory_contents(logger, input_dir, temp_dir, total_files=remaining_files)

    total_files = count_files(temp_dir)

    if move_files:
        print_final_spin_files(logger, total_files, header='INFO', description='Moving file')
    else:
        print_final_spin_files(logger, total_files, header='INFO', description='Copying file')

    method = 'moved' if move_files else 'copied'
    if done_info['skipped_files'] == 0:
        custom_print(logger, f"{GREY}[INFO]{RESET} "
                             f"Successfully {method} {done_info['actual_file_sizes_gb']:.2f} GB to TEMP.")
    elif done_info['skipped_files'] > 0:
        custom_print(logger, f"{GREY}[INFO]{RESET} "
                             f"Successfully {method} {done_info['actual_file_sizes_gb']:.2f} GB) to TEMP.")
        custom_print(logger,
                     f"{GREY}[INFO]{RESET} {done_info['skipped_files']} {print_multi_or_single(done_info['skipped_files'], 'file')} "
                     f"had to be skipped due to insufficient storage capacity.")
        custom_print(logger,
                     f"{GREY}[INFO]{RESET} {done_info['required_space_gib']:.2f} GB needed in total (350% of {done_info['actual_file_sizes_gb']:.2f} GB, "
                     f"{total_files} {print_multi_or_single(total_files, 'file')}), "
                     f"only {done_info['available_space_gib']:.2f} GB is available in TEMP.")

    extract_archives(logger, temp_dir)
    process_extras(temp_dir)
    flatten_directories(temp_dir)

    convert_all_videos_to_mkv(debug, temp_dir, args.silent)
    rename_others_file_to_folder(temp_dir)

    if remove_samples:
        remove_sample_files_and_dirs(temp_dir)

    fix_episodes_naming(temp_dir)
    remove_ds_store(temp_dir)
    remove_wsl_identifiers(temp_dir)

    if total_files == 0:
        if not args.silent:
            print_no_timestamp(logger, f"No mkv files found in input directory.\n")
            show_cursor()
        exit(0)

    dirpaths = []
    for dirpath, dirnames, filenames in os.walk(temp_dir):
        dirnames.sort(key=str.lower)  # sort directories in-place in case-insensitive manner

        # Skip directories or files starting with '.'
        if '/.' in dirpath or dirpath.startswith('./.'):
            continue

        if not dirpath == 'input/':
            dirpaths.append(dirpath)

        """
        Main loop
        """

        # Ignore files that start with a dot
        filenames = [f for f in filenames if not f.startswith('.')]
        # Extract the directory path relative to the input directory
        relative_dir_path = os.path.relpath(dirpath, temp_dir)
        # Split the relative path into individual directories
        all_dirnames = relative_dir_path.split(os.sep)
        total_external_subs = []
        # Remove all filenames that are not mkv or srt
        filenames = [f for f in filenames if f.endswith('.mkv') or f.endswith('.srt')]
        # Remove all filenames that are not mkv
        filenames_mkv_only = [f for f in filenames if f.endswith('.mkv')]
        filenames_before_retag = filenames_mkv_only

        download_missing_subs = check_config(config, 'subtitles', 'download_missing_subs')
        remove_all_subtitles = check_config(config, 'subtitles', 'remove_all_subtitles')

        if not filenames:
            exit(0)

        print_media_info(logger, filenames)

        for file in filenames_mkv_only:
            if not mkv_contains_video(file, dirpath):
                custom_print(logger, f"{RED}[ERROR]{RESET} File '{file}' does not contain a video stream.")
                if args.service:
                    custom_print(logger, f"{RED}[ERROR]{RESET} Service mode detected. Deleting file and continuing...\n")
                    os.remove(os.path.join(dirpath, file))
                    filenames_mkv_only.remove(file)
                    filenames.remove(file)
                else:
                    custom_print(logger, f"{RED}[ERROR]{RESET} Remove this file from the input folder and try again.\n")
                    exit(1)

        custom_print(logger, f"{GREY}[INFO]{RESET} Using {max_workers} CPU threads for processing.")
        start_time = time.time()

        try:
            errored_ocr_list = []
            all_downloaded_subs = []

            need_processing_audio, need_processing_subs, all_missing_subs_langs = trim_audio_and_subtitles_in_mkv_files(logger, debug, max_workers, filenames_mkv_only, dirpath)
            filenames_mkv_only = remove_clutter_process(logger, debug, max_workers, filenames_mkv_only, dirpath)
            audio_tracks_to_be_merged, subtitle_tracks_to_be_merged = generate_audio_tracks_in_mkv_files(logger, debug, max_workers, filenames_mkv_only, dirpath, need_processing_audio)

            if any(need_processing_subs):
                all_subtitle_files = extract_subs_in_mkv_process(logger, debug, max_workers, filenames_mkv_only, dirpath)

                if not all(sub == ['none'] or sub == [''] or sub == [] for sub in all_missing_subs_langs):
                    if any(file.endswith('.srt') for file in filenames):
                        total_external_subs, all_missing_subs_langs = process_external_subs(
                            logger, debug, max_workers, dirpath, filenames_before_retag, all_missing_subs_langs
                        )
                        if not all(sub is None for sub in total_external_subs):
                            all_subtitle_files = merge_subtitles_with_priority(all_subtitle_files, total_external_subs)

                if not all(sub == ['none'] or sub == [''] or sub == [] for sub in all_missing_subs_langs) and download_missing_subs:
                    all_downloaded_subs = fetch_missing_subtitles_process(logger, debug, max_workers, filenames_mkv_only, dirpath, total_external_subs,
                                                    all_missing_subs_langs)
                    all_subtitle_files = [[*a, *b] for a, b in zip(all_subtitle_files, all_downloaded_subs)]

                if total_external_subs or all_downloaded_subs and any(sub for sub in all_subtitle_files):
                    # Filter the nested lists to only include .srt files
                    subtitle_files = [[f for f in sublist if f.endswith('.srt')] for sublist in all_subtitle_files]
                    if any(sub for sub in subtitle_files):
                        resync_sub_process(logger, debug, max_workers, filenames_mkv_only, dirpath, subtitle_files)

                (subtitle_tracks_to_be_merged, subtitle_files_to_process,
                 all_missing_subs_langs, errored_ocr_list, main_audio_track_langs) = convert_to_srt_process(logger, debug, max_workers, filenames_mkv_only, dirpath, all_subtitle_files)

                if (not all(sub == ['none'] or sub == [''] or sub == [] for sub in all_missing_subs_langs)
                        and any(sub for sub in errored_ocr_list) and download_missing_subs):
                    all_downloaded_subs = fetch_missing_subtitles_process(logger, debug, max_workers,
                                                                          filenames_mkv_only, dirpath,
                                                                          total_external_subs,
                                                                          all_missing_subs_langs)

                    subtitle_files_to_process = [[*a, *b] for a, b in zip(subtitle_files_to_process, all_downloaded_subs)]
                    all_subtitle_files = [[*a, *b] for a, b in zip(all_subtitle_files, subtitle_files_to_process)]

                    if subtitle_files_to_process and any(sub for sub in subtitle_files_to_process):
                        # Filter the nested lists to only include .srt files
                        subtitle_files = [[f for f in sublist if f.endswith('.srt')] for sublist in subtitle_files_to_process]
                        if any(sub for sub in subtitle_files):
                            resync_sub_process(logger, debug, max_workers, filenames_mkv_only, dirpath, subtitle_files)

                    subtitle_tracks_to_be_merged = get_subtitle_tracks_metadata_for_repack(logger, all_subtitle_files, max_workers)

                if subtitle_files_to_process and any(sub for sub in subtitle_files_to_process):
                    remove_sdh_process(logger, debug, max_workers, subtitle_files_to_process)

            if (any(any(value for value in d.values()) for d in audio_tracks_to_be_merged) or
                    any(any(value for value in d.values()) for d in subtitle_tracks_to_be_merged) or
                    remove_all_subtitles):
                repack_mkv_tracks_process(logger, debug, max_workers, filenames_mkv_only, dirpath, audio_tracks_to_be_merged, subtitle_tracks_to_be_merged)

            move_files_to_output_process(logger, debug, max_workers, filenames_mkv_only, dirpath, all_dirnames, output_dir)

            end_time = time.time()
            processing_time = end_time - start_time

            print_no_timestamp(logger, '')
            print_no_timestamp(logger, f"{GREY}[INFO]{RESET} {len(filenames_mkv_only)} {print_multi_or_single(len(filenames_mkv_only), 'file')} "
                                       f"{'successfully ' if not any(sub for sub in errored_ocr_list) else ''}processed.")
            print_no_timestamp(logger, f"{GREY}[INFO]{RESET} Processing took {format_time(int(processing_time))} to complete.\n")
            if not args.service:
                show_cursor()

            if not os.path.exists(clear_temp_txt_file):
                with open(clear_temp_txt_file, "w") as file:
                    file.write(str(filenames_mkv_only))

        except Exception as e:
            if isinstance(e, CorruptedFile):
                if os.path.exists(clear_temp_txt_file):
                    os.remove(clear_temp_txt_file)
                partial_str = 'copied' if not move_files else 'moved'
                print_no_timestamp(logger, '')
                print_no_timestamp(logger, f"{RED}[ERROR]{RESET} Partially {partial_str} "
                                     f"{print_multi_or_single(len(filenames_mkv_only), 'file')} detected. Retrying...")
                total_files_input = wait_for_stable_files(input_dir)
                if not total_files_input:
                    for file in filenames_mkv_only:
                        shutil.move(os.path.join(dirpath, file), temp_dir)
            else:
                # If anything were to fail, move files to output folder
                custom_print(logger, f"{RED}[ERROR]{RESET} An unknown error occured. Moving "
                                     f"{print_multi_or_single(len(filenames_mkv_only), 'file')} to destination folder...\n{e}")
                custom_print(logger, traceback.print_tb(e.__traceback__))
                move_files_to_output_process(logger, debug, max_workers, filenames_mkv_only, dirpath, all_dirnames, output_dir)

            print_no_timestamp(logger, '')
            if not args.service:
                show_cursor()
            exit(1)
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
    parser.add_argument("--log_file", dest="log_file", type=str, required=False, default='mkv-auto.log',
                        help="log file location (default: './mkv-auto.log')")

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
