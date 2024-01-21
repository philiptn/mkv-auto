import subprocess
import json
import os
from tqdm import tqdm
from datetime import datetime
import shutil


def get_timestamp():
	"""Return the current UTC timestamp in the desired format."""
	current_time = datetime.utcnow()
	return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def convert_video_to_mkv(video_file, output_file):
    # FFmpeg command
    command = [
        'ffmpeg', '-fflags', '+genpts', '-i', video_file, '-c', 'copy',
        '-y', output_file
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    # Verifying completion
    return_code = process.returncode
    if return_code != 0:
        print(f"Failed to convert {video_file}")
        print("Error from FFmpeg:", stderr.decode())  # Print the exact error


def convert_all_videos_to_mkv(input_folder, silent):
    video_files = []
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.endswith(('.mp4', '.avi')):
                video_files.append(os.path.join(root, file))

    total_files = len(video_files)
    if total_files == 0:
        return

    pbar = tqdm(total=total_files, bar_format='\r{desc}{bar:8} {percentage:3.0f}%', leave=False, disable=silent)
    for i, video_file in enumerate(video_files, start=1):
        pbar.set_description(f'[UTC {get_timestamp()}] [INFO] Converting file {i} of {total_files} to MKV')
        output_file = os.path.splitext(video_file)[0] + '.mkv'
        convert_video_to_mkv(video_file, output_file)
        pbar.update(1)  # Update progress bar by one file
    pbar.close()



def get_mkv_info(filename):
    command = ["mkvmerge", "-J", filename]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stderr)

    # Parse the JSON output and pretty-print it
    parsed_json = json.loads(result.stdout)
    pretty_json = json.dumps(parsed_json, indent=2)
    return parsed_json, pretty_json


def get_mkv_video_codec(filename):
    parsed_json, _ = get_mkv_info(filename)
    for track in parsed_json['tracks']:
        if track['type'] == 'video':
            return track['codec']
    return None


def remove_all_mkv_track_tags(filename):
    command = ['mkvpropedit', filename,
               '--edit', 'track:v1', '--set', 'name=',
               '--edit', 'track:a1', '--set', 'name=',
               '--set', 'flag-default=1', '-e', 'info', '-s', 'title=']
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvpropedit command: " + result.stderr)



def remove_cc_hidden_in_file(filename):
    print(f"[UTC {get_timestamp()}] [FFMPEG] Removing any hidden Closed Captions (CC) in the video stream...")
    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ['ffmpeg', '-i', filename, '-codec', 'copy', '-map', '0',
                '-map', '-v', '-map', 'V', '-bsf:v', 'filter_units=remove_types=6', temp_filename]

    # Remove empty entries
    command = [arg for arg in command if arg]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing ffmpeg command: " + result.stderr)
        print(f"[UTC {get_timestamp()}] [INFO] Skipping ffmpeg process...")
        try:
            os.remove(temp_filename)
        except:
            pass
    else:
        os.remove(filename)
        shutil.move(temp_filename, filename)



def strip_tracks_in_mkv(filename, audio_tracks, default_audio_track,
                        sub_tracks, default_subs_track, always_enable_subs):
    print(f"[UTC {get_timestamp()}] [MKVMERGE] Filtering audio and subtitle tracks...")
    audio_track_names_list = []
    subtitle_tracks = ''
    subs_default_track = ''
    default_subs_track_str = ''
    subs_track_names_list = []

    # If no audio tracks has been selected, copy all as fallback,
    # else, generate copy string
    if len(audio_tracks) == 0:
        audio = ''
        audio_tracks_str = ''
        audio_track_names_list = []
        audio_default_track = ''
        default_audio_track_str = ''
    else:
        audio = '--atracks'
        audio_tracks_str = ','.join(map(str, audio_tracks))
        audio_default_track = "--default-track"
        default_audio_track_str = f'{default_audio_track}:yes'
        # For generating an empty title for each audio track
        for audio_track in audio_tracks:
            audio_track_names_list += ["--track-name", f"{audio_track}:"]

    if always_enable_subs and len(sub_tracks) != 0:
        subs_default_track = "--default-track"
        default_subs_track_str = f'{default_subs_track}:yes'
        for sub_track in sub_tracks:
            subs_track_names_list += ["--track-name", f"{sub_track}:"]
    if len(sub_tracks) == 0:
        subs = "--no-subtitles"
    else:
        subs = '--subtitle-tracks'
        subtitle_tracks = ','.join(map(str, sub_tracks))

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    command = ["mkvmerge",
               "--output", temp_filename,
               audio, audio_tracks_str,
               audio_default_track, default_audio_track_str] + audio_track_names_list + [
               subs, subtitle_tracks,
               subs_default_track, default_subs_track_str] + subs_track_names_list + [
               filename]
    # Remove empty entries
    command = [arg for arg in command if arg]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error executing mkvmerge command: " + result.stdout)
        print("Continuing...")

    os.remove(filename)
    shutil.move(temp_filename, filename)


def repack_tracks_in_mkv(filename, sub_filetypes, sub_languages, pref_subs_langs, audio_filetypes, audio_languages, pref_audio_langs):
    sub_files_list = []
    final_sub_languages = sub_languages
    audio_files_list = []
    final_audio_languages = audio_languages

    # If the first preferred language is found in the audio languages,
    # reorder the list to place the preferred language first
    if audio_languages:
        # Function to get the priority of each language
        def get_priority(lang):
            try:
                return pref_audio_langs.index(lang)
            except ValueError:
                return len(pref_audio_langs)

        # Zip the audio_languages and audio_filetypes together, sort them, and unzip them
        paired = zip(audio_languages, audio_filetypes)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_audio_languages, sorted_audio_filetypes = zip(*sorted_paired)

        # Convert tuples back to lists if necessary
        final_audio_languages = list(sorted_audio_languages)
        audio_filetypes = list(sorted_audio_filetypes)

    # Initialize first_pref_audio_index to -1 (indicating no match found yet)
    first_pref_audio_index = -1
    # Iterate through pref_audio_langs to find the first matching language in audio_languages
    for i, lang in enumerate(pref_audio_langs):
        if lang in final_audio_languages:
            first_pref_audio_index = i
            break

    # If the first preferred language is found in the sub languages,
    # reorder the list to place the preferred language first
    if sub_languages:
        # Function to get the priority of each language
        def get_priority(lang):
            try:
                return pref_subs_langs.index(lang)
            except ValueError:
                return len(pref_subs_langs)

        # Zip the subs_languages and subs_filetypes together, sort them, and unzip them
        paired = zip(sub_languages, sub_filetypes)
        sorted_paired = sorted(paired, key=lambda x: get_priority(x[0]))
        sorted_sub_languages, sorted_sub_filetypes = zip(*sorted_paired)

        # Convert tuples back to lists if necessary
        final_sub_languages = list(sorted_sub_languages)
        sub_filetypes = list(sorted_sub_filetypes)


    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension

    default_locked = False
    default_track_str = []

    for index, filetype in enumerate(audio_filetypes):
        if not default_locked:
            if final_audio_languages[index] == pref_audio_langs[first_pref_audio_index]:
                default_track_str = "0:yes"
                default_locked = True
            else:
                default_track_str = "0:no"
        else:
            default_track_str = "0:no"
        langs_str = f"0:{final_audio_languages[index]}"
        filelist_str = f"{base}.{final_audio_languages[index][:-1]}.{filetype}"
        audio_files_list += '--default-track', default_track_str, '--language', langs_str, filelist_str

    default_locked = False
    default_track_str = []
    for index, filetype in enumerate(sub_filetypes):
        # mkvmerge does not support the .sub file as input,
        # and requires the .idx specified instead
        if filetype == "sub":
            filetype = "idx"
        if not default_locked:
            if filetype == "srt":
                default_track_str = "0:yes"
                default_locked = True
        else:
            default_track_str = "0:no"
        langs_str = f"0:{final_sub_languages[index]}"
        filelist_str = f"{base}.{final_sub_languages[index][:-1]}.{filetype}"
        sub_files_list += '--default-track', default_track_str, '--language', langs_str, filelist_str

    if audio_filetypes:
        # Remove all subtitle and audio tracks
        print(f"[UTC {get_timestamp()}] [MKVMERGE] Removing existing subtitles and audio in mkv...")
        command = ["mkvmerge", "--output", temp_filename, "--no-subtitles", "--no-audio", filename]
    else:
        # Remove all subtitle tracks
        print(f"[UTC {get_timestamp()}] [MKVMERGE] Removing existing subtitles in mkv...")
        command = ["mkvmerge", "--output", temp_filename, "--no-subtitles", filename]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)
    os.remove(filename)
    shutil.move(temp_filename, filename)

    print(f"[UTC {get_timestamp()}] [MKVMERGE] Repacking tracks into mkv...")
    if audio_filetypes:
        command = ["mkvmerge",
                   "--output", temp_filename, filename] + sub_files_list + audio_files_list
    else:
        command = ["mkvmerge",
                   "--output", temp_filename, filename] + sub_files_list

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)

    os.remove(filename)
    shutil.move(temp_filename, filename)

    if audio_filetypes:
        for index, filetype in enumerate(audio_filetypes):
            os.remove(f"{base}.{final_audio_languages[index][:-1]}.{filetype}")
    if sub_filetypes:
        # Need to add the .idx file as well to filetypes list for final deletion
        for index, filetype in enumerate(sub_filetypes):
            if filetype == "sub":
                sub_filetypes.append('idx')
                final_sub_languages.append(final_sub_languages[index])
        for index, filetype in enumerate(sub_filetypes):
            os.remove(f"{base}.{final_sub_languages[index][:-1]}.{filetype}")


def get_wanted_audio_tracks(file_info, pref_audio_langs, remove_commentary, pref_audio_codec):
    audio_track_ids = []
    audio_track_languages = []

    pref_audio_track_ids = []
    pref_audio_track_languages = []
    audio_track_codecs = []

    tracks_ids_to_be_converted = []
    tracks_langs_to_be_converted = []
    other_tracks_ids = []
    other_tracks_langs = []

    default_audio_track = ''
    pref_default_audio_track = ''
    total_audio_tracks = 0
    needs_processing = False
    pref_audio_codec_found = False

    for track in file_info["tracks"]:
        if track["type"] == "audio":
            total_audio_tracks += 1
            track_name = ''
            track_language = ''
            audio_codec = ''
            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'language':
                    track_language = value
                if key == 'codec_id':
                    audio_codec = value
            if track_language in pref_audio_langs:
                if pref_audio_track_languages.count(track_language) == 0 and pref_audio_codec in audio_codec.upper():
                    pref_audio_track_ids.append(track["id"])
                    pref_audio_track_languages.append(track_language)
                    audio_track_codecs.append(audio_codec.upper())
                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower() \
                            and track_language in audio_track_languages:
                        pref_audio_track_ids.remove(track["id"])
                        pref_audio_track_languages.remove(track_language)
                    else:
                        pref_default_audio_track = track["id"]
                elif audio_track_languages.count(track_language) == 0 and pref_audio_codec not in audio_codec.upper():
                    audio_track_ids.append(track["id"])
                    audio_track_languages.append(track_language)
                    audio_track_codecs.append(audio_codec.upper())
                    # Removes commentary track if main track(s) is already added, and if pref is set to true
                    if remove_commentary and "commentary" in track_name.lower() \
                            and track_language in audio_track_languages:
                        audio_track_ids.remove(track["id"])
                        audio_track_languages.remove(track_language)
                    else:
                        default_audio_track = track["id"]
                # If there exists the preferred audio codec, use that list.
                # If the preferred audio codec is not present, use general list instead
                #if len(pref_audio_track_ids) != 0:
                #    audio_track_ids = pref_audio_track_ids
                #    audio_track_languages = pref_audio_track_languages

    audio_track_ids.sort()
    pref_audio_track_ids.sort()

    all_audio_track_ids = audio_track_ids + pref_audio_track_ids
    all_audio_track_ids.sort()
    all_audio_track_langs = audio_track_languages + pref_audio_track_languages
    all_audio_track_langs.sort()

    if len(audio_track_ids) != 0 and len(audio_track_ids) < total_audio_tracks:
        needs_processing = True

    # If the preferred audio codec is in all of the matching tracks, or with unique langs, then it is fully found
    if all(pref_audio_codec in item for item in audio_track_codecs):
        pref_audio_codec_found = True
        all_audio_track_ids = pref_audio_track_ids
        default_audio_track = pref_default_audio_track
    elif any(pref_audio_codec in codec for codec in audio_track_codecs) and all(pref_audio_track_languages[0] in item for item in all_audio_track_langs):
        pref_audio_codec_found = True
        all_audio_track_ids = pref_audio_track_ids
        default_audio_track = pref_default_audio_track
    else:
        tracks_ids_to_be_converted = audio_track_ids
        tracks_langs_to_be_converted = audio_track_languages
        other_tracks_ids = pref_audio_track_ids
        other_tracks_langs = pref_audio_track_languages

    return all_audio_track_ids, default_audio_track, needs_processing, pref_audio_codec_found, \
        tracks_ids_to_be_converted, tracks_langs_to_be_converted, other_tracks_ids, other_tracks_langs


def get_wanted_subtitle_tracks(file_info, pref_subs_langs):
    total_subs_tracks = 0
    subs_track_ids = []
    subs_track_languages = []
    default_subs_track = ''
    forced_track = ''
    all_sub_filetypes = []
    selected_sub_filetypes = []
    sub_filetypes = []
    srt_track_ids = []
    ass_track_ids = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False

    for track in file_info["tracks"]:
        if track["type"] == "subtitles":
            total_subs_tracks += 1
            track_name = ''
            track_language = ''

            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'language':
                    track_language = value
                if key == 'forced_track':
                    forced_track = value
            if track_language in pref_subs_langs:
                needs_processing = True
                needs_sdh_removal = True
                
                if subs_track_languages.count(track_language) == 0 and forced_track != True:
                    selected_sub_filetypes.append(track["codec"])
                    subs_track_ids.append(track["id"])
                    subs_track_languages.append(track_language)

                    if track["codec"] == "HDMV PGS":
                        sub_filetypes.append('sup')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "VobSub":
                        sub_filetypes.append('sub')
                        needs_convert = True
                        needs_processing = True
                    elif track["codec"] == "SubRip/SRT":
                        sub_filetypes.append('srt')
                        srt_track_ids.append(track["id"])
                    elif track["codec"] == "SubStationAlpha":
                        sub_filetypes.append('ass')
                        ass_track_ids.append(track["id"])
                        needs_convert = True
                        needs_processing = True
                else:
                    if (track["codec"] != "SubRip/SRT" and track["codec"] != "SubStationAlpha") and subs_track_languages.count(track_language) == 1:
                        if track["codec"] == "HDMV PGS" and sub_filetypes.count("sup") == 0:
                            sub_filetypes.append('sup')
                            selected_sub_filetypes.append(track["codec"])
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            needs_convert = True
                            needs_processing = True
                        elif track["codec"] == "VobSub" and sub_filetypes.count("sub") == 0:
                            sub_filetypes.append('sub')
                            selected_sub_filetypes.append(track["codec"])
                            subs_track_ids.append(track["id"])
                            subs_track_languages.append(track_language)
                            needs_convert = True
                            needs_processing = True
                        
                        if 'srt' in sub_filetypes:
                            sub_filetypes.remove('srt')

                        if 'ass' in sub_filetypes:
                            sub_filetypes.remove('ass')

                        subs_tracks_ids_no_srt = [x for x in subs_track_ids if x not in srt_track_ids]
                        subs_tracks_ids_no_ass = [x for x in subs_tracks_ids_no_srt if x not in ass_track_ids]
                        subs_track_ids = subs_tracks_ids_no_ass

    # Sets the default subtitle track to first entry in preferences,
    # reverts to any entry if not first
    for track_id, lang in zip(subs_track_ids, subs_track_languages):
        if lang == pref_subs_langs[0]:
            default_subs_track = track_id
            break
        elif lang in pref_subs_langs:
            default_subs_track = track_id
            break

    # Remove language duplicates
    seen = set()
    result = []
    for item in subs_track_languages:
        if item not in seen:
            seen.add(item)
            result.append(item)
    subs_track_languages = result

    subs_track_ids.sort()
    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True

    return subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert, \
        sub_filetypes, subs_track_languages, needs_processing


def extract_subs_in_mkv(filename, track_numbers, output_filetypes, subs_languages):
    subtitle_files = []
    base, _, extension = filename.rpartition('.')

    for index, track in enumerate(track_numbers):
        subtitle_filename = f"{base}.{subs_languages[index][:-1]}.{output_filetypes[index]}"
        command = ["mkvextract", filename, "tracks",
                   f"{track}:{subtitle_filename}"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing mkvextract command: " + result.stdout)
        subtitle_files.append(subtitle_filename)

    return subtitle_files


def extract_audio_tracks_in_mkv(filename, track_numbers, audio_languages):
    if not track_numbers:
        print(f"[UTC {get_timestamp()}] [MKVEXTRACT] Error: No track numbers passed.")
        return
    audio_files = []
    base, _, _ = filename.rpartition('.')

    for index, track in enumerate(track_numbers):
        audio_filename = f"{base}.{audio_languages[index][:-1]}.mkv"
        command = ["mkvextract", filename, "tracks", f"{track}:{audio_filename}"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing mkvextract command: " + result.stdout)
        audio_files.append(audio_filename)

    return audio_files, audio_languages

def encode_audio_tracks(audio_files, languages, output_codec, other_files, other_langs):
    if not audio_files:
        return
    if len(audio_files) > 1:
        track_str = "tracks"
    else:
        track_str = "track"
    print(f"[UTC {get_timestamp()}] [FFMPEG] Generating {output_codec.upper()} audio {track_str}...")

    custom_ffmpeg = '/.mkv-auto/ffmpeg-3.1.11/ffmpeg-3.1.11/ffmpeg'
    output_audio_files_extensions = []
    output_audio_langs = []
    other_output_audio_files_extensions = []
    other_output_audio_langs = []

    for index, file in enumerate(audio_files):
        base_and_lang, _, extension = file.rpartition('.')
        base, _, lang = base_and_lang.rpartition('.')

        output_audio_files_extensions.append(extension)
        output_audio_langs.append(languages[index])

        command = [custom_ffmpeg, "-i", file, "-strict", "-2", f"{base}.{lang}.{output_codec.lower()}"]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing ffmpeg command: " + result.stderr)

        output_audio_files_extensions.append(f"{output_codec.lower()}")
        output_audio_langs.append(f"{languages[index]}")

    # Adding the other files to return list
    for index, file in enumerate(other_files):
        base_and_lang, _, extension = file.rpartition('.')
        other_output_audio_files_extensions.append(extension)
        other_output_audio_langs.append(other_langs[index])

    output_audio_files_extensions = output_audio_files_extensions + other_output_audio_files_extensions
    output_audio_langs =  output_audio_langs + other_output_audio_langs
    output_audio_files_extensions.sort()
    output_audio_langs.sort()

    return output_audio_files_extensions, output_audio_langs
