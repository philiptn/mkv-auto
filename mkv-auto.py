from configparser import ConfigParser

from scripts.file_operations import *
from scripts.mkv import *
from scripts.ocr import *
from scripts.srt import *

input_dir = "input/"
output_dir = "output/"

# Get user preferences
variables = ConfigParser()
variables.read('preferences.ini')
# Audio
pref_audio_langs = [item.strip() for item in variables.get('audio', 'PREFERRED_AUDIO_LANG').split(',')]
remove_commentary = True if variables.get('audio', 'REMOVE_COMMENTARY_TRACK').lower() == "true" else False
# Subtitles
pref_subs_langs = [item.strip() for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
always_enable_subs = True if variables.get('subtitles', 'ALWAYS_ENABLE_SUBS').lower() == "true" else False
always_remove_sdh = True if variables.get('subtitles', 'REMOVE_SDH').lower() == "true" else False
resync_subtitles = True if variables.get('subtitles', 'RESYNC_SUBTITLES').lower() == "true" else False

total_files = get_total_mkv_files(input_dir)
file_index = 1

if total_files == 0:
    print(f"[INFO] No files found in input directory.")
    exit(0)

dirpaths = []
for dirpath, dirnames, filenames in os.walk(input_dir):
    # Skip directories or files starting with '.'
    if '/.' in dirpath or dirpath.startswith('./.'):
        continue

    if not dirpath == 'input/':
        dirpaths.append(dirpath)

    structure = os.path.join(output_dir, os.path.relpath(dirpath, input_dir))
    if not os.path.isdir(structure):
        os.mkdir(structure)  # creates the directory structure

    output_file_mkv = ''
    external_subs_print = True
    quiet = False

    for file_name in filenames:
        if file_name.startswith('.'):
            continue

        input_file = os.path.join(dirpath, file_name)
        output_file = os.path.join(structure, file_name)

        if file_name.endswith('.mkv'):
            external_subs_print = True
            quiet = False
            output_file_mkv = output_file
            print(f"\n[INFO] Processing file {file_index} of {total_files}:\n")
            print(f"[FILE] '{file_name}'")
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

            if needs_processing_subs:
                # Get updated file info after mkv tracks reduction
                file_info, pretty_file_info = get_mkv_info(input_file)
                wanted_subs_tracks, a, b, c, \
                    sub_filetypes, subs_track_languages, e = get_wanted_subtitle_tracks(file_info, pref_subs_langs)

                updated_subtitle_languages = subs_track_languages
                # Check if any of the subtitle tracks needs to be converted using OCR

                if needs_convert:
                    print(f"[MKVEXTRACT] Some subtitles need to be converted to SRT, extracting subtitles...")
                    output_subtitles = []
                    generated_srt_files = []

                    for sub_filetype in sub_filetypes:
                        if sub_filetype == "sub":
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetype, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_vobsub_subtitles(subtitle_files, subs_track_languages)

                        elif sub_filetype == "sup":
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetype, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_pgs_subtitles(subtitle_files, subs_track_languages)

                        elif sub_filetype == "ass":
                            subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                                  sub_filetype, subs_track_languages)
                            output_subtitles, updated_subtitle_languages, generated_srt_files = convert_ass_to_srt(subtitle_files, subs_track_languages)

                        if always_remove_sdh:
                            remove_sdh(output_subtitles, quiet)
                            if resync_subtitles:
                                resync_srt_subs(input_file, output_subtitles, quiet)
                            needs_sdh_removal = False
                            break
                        else:
                            break

                    for file in generated_srt_files:
                        sub_filetypes.insert(0, file)

                # If an SDH track is spotted in the input file, and preference is set to remove
                if needs_sdh_removal and always_remove_sdh:
                    subtitle_files = []
                    for sub_filetype in sub_filetypes:
                        subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
                                                         sub_filetype, subs_track_languages)
                    remove_sdh(subtitle_files, quiet)
                    if resync_subtitles:
                        resync_srt_subs(input_file, subtitle_files, quiet)
                repack_tracks_in_mkv(input_file, sub_filetypes, updated_subtitle_languages)
        elif file_name.endswith('.srt'):
            if external_subs_print:
                quiet = True
            input_files = [input_file]
            if always_remove_sdh:
                if external_subs_print:
                    print("[SRT_EXT] Removing SDH in external subtitles...")
                remove_sdh(input_files, quiet)
            if resync_subtitles:
                if external_subs_print:
                    print("[SRT_EXT] Synchronizing external subtitles to audio track...")
                resync_srt_subs(output_file_mkv, input_files, quiet)
            external_subs_print = False

        else:
            move_file(input_file, output_file)
            continue
        move_file(input_file, output_file)
        file_index += 1

# Sorting the dirpaths such that entries with
# the longest subdirectories are removed first
dirpaths.sort(key=lambda path: path.count('/'), reverse=True)
for dirpath in dirpaths:
    safe_delete_dir(dirpath)

print(f"\n[INFO] All files successfully processed.\n")
