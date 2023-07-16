import sys
import argparse
from configparser import ConfigParser
from itertools import zip_longest

from scripts.file_operations import *
from scripts.mkv import *
from scripts.ocr import *
from scripts.srt import *

# Get user preferences
variables = ConfigParser()
# If user-specific config file has been created, load it
# else, load defaults from preferences.ini
if os.path.isfile('user.ini'):
    variables.read('user.ini')
else:
    variables.read('defaults.ini')
# General
file_tag = variables.get('general', 'FILE_TAG')
flatten_directories = True if variables.get('general', 'FLATTEN_DIRECTORIES').lower() == "true" else False
remove_samples = True if variables.get('general', 'REMOVE_SAMPLES').lower() == "true" else False
movies_folder = variables.get('general', 'MOVIES_FOLDER')
tv_shows_folder = variables.get('general', 'TV_SHOWS_FOLDER')
others_folder = variables.get('general', 'OTHERS_FOLDER')
# Audio
pref_audio_langs = [item.strip() for item in variables.get('audio', 'PREFERRED_AUDIO_LANG').split(',')]
remove_commentary = True if variables.get('audio', 'REMOVE_COMMENTARY_TRACK').lower() == "true" else False
# Subtitles
pref_subs_langs = [item.strip() for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
always_enable_subs = True if variables.get('subtitles', 'ALWAYS_ENABLE_SUBS').lower() == "true" else False
always_remove_sdh = True if variables.get('subtitles', 'REMOVE_SDH').lower() == "true" else False
remove_music = True if variables.get('subtitles', 'REMOVE_MUSIC').lower() == "true" else False
resync_subtitles = variables.get('subtitles', 'RESYNC_SUBTITLES').lower()


def mkv_auto(args):

    if args.temp_dir:
        temp_dir = args.temp_dir
    else:
        temp_dir = '.tmp/'

    if args.input_file:
        needs_copy = True

    elif args.input_dir:
        input_dir = args.input_dir
        needs_copy = True
    else:
        input_dir = "input/"
        needs_copy = False

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = "output/"

    if needs_copy:
        os.mkdir(temp_dir)
        if args.input_dir:
            total_files = count_files(input_dir)
            total_bytes = count_bytes(input_dir)
            print('')
            with tqdm(total=total_bytes, unit='B', unit_scale=True, unit_divisor=1024,
                      bar_format='{desc}{bar:10} {percentage:3.0f}%', leave=False) as pbar:
                pbar.set_description(f"[INFO] Copying file 1 of {total_files}:")
                copy_directory_contents(input_dir, temp_dir, pbar, total_files=total_files)
        else:
            print("[INFO] Copying file...")
            copy_file(args.input_file, temp_dir)
        input_dir = temp_dir

    if remove_samples:
        remove_sample_files_and_dirs(input_dir)

    if flatten_directories:
        flatten_dirs(input_dir)

    fix_episodes_naming(input_dir)
    remove_ds_store(input_dir)

    total_files = get_total_mkv_files(input_dir)
    file_index = 1

    if total_files == 0 and not args.input_file:
        print(f"[INFO] No files found in input directory.")
        exit(0)

    dirpaths = []
    for dirpath, dirnames, filenames in os.walk(input_dir):

        dirnames.sort(key=str.lower)  # sort directories in-place in case-insensitive manner

        # Skip directories or files starting with '.'
        if '/.' in dirpath or dirpath.startswith('./.'):
            continue

        if not dirpath == 'input/':
            dirpaths.append(dirpath)

        structure = os.path.join(output_dir, os.path.relpath(dirpath, input_dir))
        #if not os.path.isdir(structure):
        #    os.mkdir(structure)  # creates the directory structure

        input_file_mkv = ''
        output_file_mkv = ''
        mkv_dirpath = ''
        file_names = []
        file_name_printed = False
        external_subs_print = True
        quiet = False

        # Separate .srt and .mkv files
        srt_files = sorted(f for f in filenames if f.lower().endswith('.srt'))
        mkv_files = sorted(f for f in filenames if f.lower().endswith('.mkv'))

        # Merge .srt and .mkv files
        sorted_files = []
        for srt, mkv in zip_longest(srt_files, mkv_files):
            if srt is not None:
                sorted_files.append(srt)
            if mkv is not None:
                sorted_files.append(mkv)
        filenames = sorted_files

        #if args.input_file:
        #    filenames = [os.path.basename(args.input_file)]
        #    total_files += 1

        for index, file_name in enumerate(filenames):

            if file_name.startswith('.'):
                continue

            input_file = os.path.join(dirpath, file_name)
            if args.output_file:
                output_file = args.output_file
            else:
                output_file = os.path.join(structure, file_name)

            needs_tag_rename = True

            if file_name.endswith('.srt'):
                input_file_mkv = os.path.join(dirpath, str(filenames[index + 1]))
                if not file_name_printed:
                    print(f"[INFO] Processing file {file_index} of {total_files}:\n")
                    print(f"[FILE] '{filenames[index + 1]}'")
                    file_name_printed = True
                if external_subs_print:
                    quiet = True
                input_files = [input_file]
                if always_remove_sdh or remove_music:
                    if external_subs_print:
                        print("[SRT_EXT] Removing SDH in external subtitles...")
                    remove_sdh(input_files, quiet, remove_music)
                if resync_subtitles == 'fast':
                    if external_subs_print:
                        print("[SRT_EXT] Synchronizing external subtitles to audio track (fast)...")
                    resync_srt_subs_fast(input_file_mkv, input_files, quiet)
                elif resync_subtitles == 'ai':
                    if external_subs_print:
                        print("[SRT_EXT] Synchronizing external subtitles to audio track (ai)...")
                    resync_srt_subs_ai(input_file_mkv, input_files, quiet)
                external_subs_print = False
                move_file(input_file, output_file)

            elif file_name.endswith('.mkv'):
                if not file_name_printed:
                    print(f"[INFO] Processing file {file_index} of {total_files}:\n")
                    print(f"[FILE] '{file_name}'")
                    file_name_printed = True

                external_subs_print = True
                quiet = False
                output_file_mkv = output_file
                # Get file info using mkvinfo
                file_info, pretty_file_info = get_mkv_info(input_file)

                wanted_audio_tracks, \
                    default_audio_track, needs_processing_audio = get_wanted_audio_tracks(file_info, pref_audio_langs, remove_commentary)
                wanted_subs_tracks, default_subs_track, \
                    needs_sdh_removal, needs_convert, a, b, needs_processing_subs = get_wanted_subtitle_tracks(file_info, pref_subs_langs)
                print_track_audio_str = 'tracks' if len(wanted_audio_tracks) != 1 else 'track'
                print_track_subs_str = 'tracks' if len(wanted_subs_tracks) != 1 else 'track'

                if needs_processing_audio or needs_processing_subs or needs_sdh_removal or needs_convert:
                    strip_tracks_in_mkv(input_file, wanted_audio_tracks, default_audio_track,
                                        wanted_subs_tracks, default_subs_track, always_enable_subs)
                else:
                    print(f"[MKVMERGE] No track filtering needed.")
                    needs_tag_rename = False

                if needs_processing_subs:
                    subtitle_files = []
                    # Get updated file info after mkv tracks reduction
                    file_info, pretty_file_info = get_mkv_info(input_file)
                    wanted_subs_tracks, a, b, needs_convert, \
                        sub_filetypes, subs_track_languages, e = get_wanted_subtitle_tracks(file_info, pref_subs_langs)

                    updated_subtitle_languages = subs_track_languages
                    # Check if any of the subtitle tracks needs to be converted using OCR

                    if needs_convert:
                        print(f"[MKVEXTRACT] Some subtitles need to be converted to SRT, extracting subtitles...")
                        output_subtitles = []
                        generated_srt_files = []

                        if "sub" in sub_filetypes:
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetypes, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_vobsub_subtitles(subtitle_files, subs_track_languages)

                        elif "sup" in sub_filetypes:
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetypes, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_pgs_subtitles(subtitle_files, subs_track_languages)

                        elif "ass" in sub_filetypes:
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetypes, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = convert_ass_to_srt(subtitle_files, subs_track_languages)

                        if always_remove_sdh:
                            remove_sdh(output_subtitles, quiet, remove_music)
                            needs_sdh_removal = False

                        if resync_subtitles == 'fast':
                            resync_srt_subs_fast(input_file, output_subtitles, quiet)
                        elif resync_subtitles == 'ai':
                            resync_srt_subs_ai(input_file, output_subtitles, quiet)

                        for file in generated_srt_files:
                            sub_filetypes.insert(0, file)

                        repack_tracks_in_mkv(input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs)

                    elif not needs_convert:
                        if needs_sdh_removal and always_remove_sdh or resync_subtitles != 'false':
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                             sub_filetypes, subs_track_languages)

                        if needs_sdh_removal and (always_remove_sdh or remove_music):
                            remove_sdh(subtitle_files, quiet, remove_music)

                        if resync_subtitles != 'false':
                            if resync_subtitles == 'fast':
                                resync_srt_subs_fast(input_file, subtitle_files, quiet)
                            elif resync_subtitles == 'ai':
                                resync_srt_subs_ai(input_file, subtitle_files, quiet)

                        if needs_sdh_removal and always_remove_sdh or resync_subtitles != 'false':
                            repack_tracks_in_mkv(input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs)

                remove_all_mkv_track_tags(input_file)

                if needs_tag_rename:
                    if file_tag != "default":
                        updated_filename = replace_tags_in_file(input_file, file_tag)
                        file_name = updated_filename

                        input_file = os.path.join(dirpath, file_name)
                        output_file = os.path.join(structure, file_name)

                #move_file(input_file, output_file)
                if not args.output_file:
                    move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder, others_folder)
                else:
                    move_file(input_file, output_file)

                file_index += 1
                file_name_printed = False
                print('')
            else:
                move_file(input_file, output_file)
                print('')
                continue

    # Sorting the dirpaths such that entries with
    # the longest subdirectories are removed first
    dirpaths.sort(key=lambda path: path.count('/'), reverse=True)
    for dirpath in dirpaths:
        safe_delete_dir(dirpath)

    print("[INFO] All files successfully processed.\n")
    if needs_copy and args.input_dir:
        os.rmdir(temp_dir)
    exit(0)


def main():
    # Create the main parser
    parser = argparse.ArgumentParser(description="A tool that aims to remove necessary clutter from Matroska (.mkv) "
                                                 "files by removing and/or converting any subtitle tracks in the "
                                                 "source file.")
    parser.add_argument("--input", "-i", dest="input_file", type=str, required=False,
                        help="input filename (absolute path)")
    parser.add_argument("--output", "-o", dest="output_file", type=str, required=False,
                        help="output filename (absolute path)")
    parser.add_argument("--input_folder", "-if", dest="input_dir", type=str, required=False,
                        help="input folder path (default: 'input/')")
    parser.add_argument("--output_folder", "-of", dest="output_dir", type=str, required=False,
                        help="output folder path (default: 'output/'")
    parser.add_argument("--tempdir", "-td", dest="temp_dir", type=str, required=False, default='.tmp/',
                        help="temp directory (default: '<current directory>/.tmp/')")

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
