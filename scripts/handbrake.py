import os

#flatpak run --command=HandBrakeCLI fr.handbrake.ghb

def encode_with_handbrake(filename, video_codec, gpu_acceleration):
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

    #
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