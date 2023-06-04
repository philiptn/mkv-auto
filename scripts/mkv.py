import subprocess
import json
import os


def get_mkv_info(filename):
    command = ["mkvmerge", "-J", filename]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stderr)

    # Parse the JSON output and pretty-print it
    parsed_json = json.loads(result.stdout)
    pretty_json = json.dumps(parsed_json, indent=2)
    return parsed_json, pretty_json


def strip_tracks_in_mkv(filename, audio_tracks, default_audio_track,
                        sub_tracks, default_subs_track, always_enable_subs):
    print(f"[MKVMERGE] Filtering audio and subtitle tracks...")
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
        raise Exception("Error executing mkvmerge command: " + result.stdout)

    os.remove(filename)
    os.rename(temp_filename, filename)


def repack_tracks_in_mkv(filename, sub_filetypes, sub_languages):
    sub_files_list = []

    base, extension = os.path.splitext(filename)
    new_base = base + "_tmp"
    temp_filename = new_base + extension
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
        langs_str = f"0:{sub_languages[index]}"
        filelist_str = f"{base}.{sub_languages[index][:-1]}.{filetype}"
        sub_files_list += '--default-track', default_track_str, '--language', langs_str, filelist_str

    # Remove all subtitle tracks first
    print(f"[MKVMERGE] Removing existing subtitles in mkv...")
    command = ["mkvmerge", "--output", temp_filename, "--no-subtitles", filename]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)
    os.remove(filename)
    os.rename(temp_filename, filename)

    print(f"[MKVMERGE] Repacking tracks into mkv...")
    command = ["mkvmerge",
               "--output", temp_filename, filename] + sub_files_list

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error executing mkvmerge command: " + result.stdout)

    os.remove(filename)
    os.rename(temp_filename, filename)

    # Need to add the .idx file as well to filetypes list for final deletion
    for index, filetype in enumerate(sub_filetypes):
        if filetype == "sub":
            sub_filetypes.append('idx')
            sub_languages.append(sub_languages[index])
    for index, filetype in enumerate(sub_filetypes):
        os.remove(f"{base}.{sub_languages[index][:-1]}.{filetype}")


def get_wanted_audio_tracks(file_info, pref_audio_langs, remove_commentary):
    audio_track_ids = []
    audio_track_languages = []
    default_audio_track = ''
    total_audio_tracks = 0
    needs_processing = False

    print(f"[MKVINFO] Analyzing audio tracks...")
    for track in file_info["tracks"]:
        if track["type"] == "audio":
            total_audio_tracks += 1
            track_name = ''
            track_language = ''
            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'language':
                    track_language = value
            if track_language in pref_audio_langs:
                audio_track_ids.append(track["id"])
                audio_track_languages.append(track_language)
                # Removes commentary track if main track(s) is already added, and if pref is set to true
                if remove_commentary and "commentary" in track_name.lower() \
                        and track_language in audio_track_languages:
                    audio_track_ids.remove(track["id"])
                else:
                    default_audio_track = track["id"]
    audio_track_ids.sort()
    if len(audio_track_ids) != 0 and len(audio_track_ids) < total_audio_tracks:
        needs_processing = True
    return audio_track_ids, default_audio_track, needs_processing


def get_wanted_subtitle_tracks(file_info, pref_subs_langs):
    total_subs_tracks = 0
    subs_track_ids = []
    subs_track_languages = []
    default_subs_track = ''
    sub_filetypes = []
    needs_sdh_removal = False
    needs_convert = False
    needs_processing = False
    default_track_locked = False

    print(f"[MKVINFO] Analyzing subtitle tracks...")
    for track in file_info["tracks"]:
        if track["type"] == "subtitles":
            total_subs_tracks += 1
            track_name = ''
            track_language = ''

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
                needs_convert = False
            elif track["codec"] == "SubStationAlpha":
                sub_filetypes.append('ass')
                needs_convert = True
                needs_processing = True

            for key, value in track["properties"].items():
                if key == 'track_name':
                    track_name = value
                if key == 'language':
                    track_language = value
            if track_language in pref_subs_langs:
                subs_track_ids.append(track["id"])
                subs_track_languages.append(track_language)

                # Removes the SDH track if non-SDH of same language is already added
                if "SDH" in track_name.upper() and track_language in subs_track_languages:
                    subs_track_ids.remove(track["id"])
                    subs_track_languages.remove(track_language)
                    needs_sdh_removal = False
                    needs_processing = False
                else:
                    needs_sdh_removal = True
                    # Sets the default subtitle track to first entry in preferences,
                    # reverts to any entry if not first
                    if not default_track_locked:
                        needs_processing = True
                        if track_language == pref_subs_langs[0]:
                            default_subs_track = track["id"]
                            default_track_locked = True
                        elif track_language in pref_subs_langs:
                            default_subs_track = track["id"]
                    else:
                        needs_processing = False
    subs_track_ids.sort()
    if len(subs_track_ids) != 0 and len(subs_track_ids) < total_subs_tracks:
        needs_processing = True
    return subs_track_ids, default_subs_track, needs_sdh_removal, needs_convert, \
        sub_filetypes, subs_track_languages, needs_processing


def extract_subs_in_mkv(filename, track_numbers, output_filetype, subs_languages):
    subtitle_files = []
    base, _, extension = filename.rpartition('.')

    for index, track in enumerate(track_numbers):
        subtitle_filename = f"{base}.{subs_languages[index][:-1]}.{output_filetype}"
        command = ["mkvextract", filename, "tracks",
                   f"{track}:{subtitle_filename}"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing mkvextract command: " + result.stdout)
        subtitle_files.append(subtitle_filename)

    return subtitle_files
