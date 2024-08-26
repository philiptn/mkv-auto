import os
import shutil
import re
import rarfile
import zipfile
from datetime import datetime
import concurrent.futures
from scripts.misc import *
from scripts.logger import *


def copy_file(src, dst):
    shutil.copy2(src, dst)


def move_file(src, dst):
    # Create any necessary subdirectories
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    # Move the file
    shutil.move(src, dst)


def extract_archives(logger, input_folder):
    for root, dirs, files in os.walk(input_folder):
        # Filter for .rar and .zip files
        archive_files = [f for f in files if f.endswith('.rar') or f.endswith('.zip')]

        if archive_files:
            for archive_file in archive_files:
                archive_path = os.path.join(root, archive_file)
                temp_extract_path = os.path.join(root, "temp_extracted")

                try:
                    os.makedirs(temp_extract_path, exist_ok=True)
                    if archive_file.endswith('.rar'):
                        # Extract RAR file
                        with rarfile.RarFile(archive_path) as rf:
                            rf.extractall(temp_extract_path)
                    elif archive_file.endswith('.zip'):
                        # Extract ZIP file
                        with zipfile.ZipFile(archive_path, 'r') as zf:
                            zf.extractall(temp_extract_path)

                    # Move extracted files to root of input_folder
                    for extracted_root, extracted_dirs, extracted_files in os.walk(temp_extract_path):
                        for file in extracted_files:
                            shutil.move(os.path.join(extracted_root, file), input_folder)
                        for dir in extracted_dirs:
                            shutil.move(os.path.join(extracted_root, dir), input_folder)

                    # Remove temporary extraction directory
                    shutil.rmtree(temp_extract_path)

                    # Remove the archive file after extraction
                    os.remove(archive_path)

                    # Remove all sub-rar files like .r00, .r01, etc.
                    sub_rar_files = [f for f in files if f.startswith(archive_file.split('.rar')[0]) and f.endswith(
                        tuple(f'.r{i:02d}' for i in range(100)))]
                    for sub_rar_file in sub_rar_files:
                        os.remove(os.path.join(root, sub_rar_file))

                except Exception as e:
                    custom_print(logger, f"{RED}[ERROR]{RESET} Failed to extract {archive_file}: {e}")


def count_files(directory):
    total_files = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not d[0] == '.']  # remove directories starting with '.' from the list
        for filename in filenames:
            if not filename.startswith('.'):
                total_files += 1
    return total_files


def count_bytes(directory):
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not d[0] == '.']  # remove directories starting with '.' from the list
        for filename in filenames:
            if not filename.startswith('.'):
                total_bytes += os.path.getsize(os.path.join(dirpath, filename))
    return total_bytes


def get_free_space(directory):
    return shutil.disk_usage(directory).free


def move_file_with_progress(src_file, dst_file, pbar, file_counter, total_files):
    shutil.move(src_file, dst_file)
    pbar.update(1)
    pbar.set_description(f"{GREY}[INFO]{RESET} Moving file {file_counter[0]} of {total_files}")


def copy_file_with_progress(src_file, dst_file, pbar, file_counter, total_files):
    chunk_size = 1024 * 1024  # e.g., copy in 1 MB chunks
    with open(src_file, 'rb') as fsrc, open(dst_file, 'wb') as fdst:
        while True:
            chunk = fsrc.read(chunk_size)
            if not chunk:
                break
            fdst.write(chunk)
            pbar.update(len(chunk))
        pbar.set_description(f"{GREY}[INFO]{RESET} Copying file {file_counter[0]} of {total_files}")


def move_directory_contents(source_directory, destination_directory, pbar, file_counter=[0], total_files=0):
    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    # Make sure that the temp dir has at least 150% of the storage capacity
    total_bytes = count_bytes(source_directory)
    total_gb_print = total_bytes / (1024 ** 3)  # Convert to gigabytes
    required_space_print = (total_bytes * 1.5) / (1024 ** 3)  # Convert to gigabytes
    available_space_print = get_free_space(destination_directory) / (1024 ** 3)  # Convert to gigabytes

    #log_debug(logger, f"Input files: {total_gb_print:.2f} GB")
    #log_debug(logger, f"Required space: {required_space_print:.2f} GB")
    #log_debug(logger, f"Available space: {available_space_print:.2f} GB\n")

    available_space = get_free_space(destination_directory)

    # Function to move a single file or directory
    def move_item(s, d):
        nonlocal available_space

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            for item in os.listdir(s):
                next_source = os.path.join(s, item)
                next_destination = os.path.join(d, item)
                move_item(next_source, next_destination)
            if not os.listdir(s):
                os.rmdir(s)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 1.5  # 150% of the original file size

            if available_space >= required_space:
                available_space -= required_space
                with pbar.get_lock():
                    file_counter[0] += 1
                    pbar.set_postfix_str(f"Moving file {file_counter[0]} of {total_files}...")
                move_file_with_progress(s, d, pbar, file_counter, total_files)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for item in os.listdir(source_directory):
            if item.startswith('.'):  # Skip files or folders starting with a dot
                continue
            s = os.path.join(source_directory, item)
            d = os.path.join(destination_directory, item)
            future = executor.submit(move_item, s, d)
            futures.append(future)

        concurrent.futures.wait(futures)


def copy_directory_contents(source_directory, destination_directory, pbar, file_counter=[0], total_files=0):
    logger = get_custom_logger()

    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    # Make sure that the temp dir has at least 150% of the storage capacity
    total_bytes = count_bytes(source_directory)
    total_gb_print = total_bytes / (1024 ** 3)  # Convert to gigabytes
    required_space_print = (total_bytes * 1.5) / (1024 ** 3)  # Convert to gigabytes
    available_space_print = get_free_space(destination_directory) / (1024 ** 3)  # Convert to gigabytes

    #log_debug(logger, f"Input files: {total_gb_print:.2f} GB")
    #log_debug(logger, f"Required space: {required_space_print:.2f} GB")
    #log_debug(logger, f"Available space: {available_space_print:.2f} GB\n")

    available_space = get_free_space(destination_directory)

    def copy_item(s, d):
        nonlocal available_space

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            for item in os.listdir(s):
                next_source = os.path.join(s, item)
                next_destination = os.path.join(d, item)
                copy_item(next_source, next_destination)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 1.5  # 150% of the original file size

            if available_space >= required_space:
                available_space -= required_space
                with pbar.get_lock():
                    file_counter[0] += 1
                    pbar.set_postfix_str(f"Copying file {file_counter[0]} of {total_files}...")
                copy_file_with_progress(s, d, pbar, file_counter, total_files)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for item in os.listdir(source_directory):
            if item.startswith('.'):  # Skip files or folders starting with a dot
                continue
            s = os.path.join(source_directory, item)
            d = os.path.join(destination_directory, item)
            future = executor.submit(copy_item, s, d)
            futures.append(future)

        concurrent.futures.wait(futures)


def move_file_to_output(input_file_path, output_folder, folder_structure):
    filename = os.path.basename(input_file_path)
    flatten_directories = True

    new_folders, new_filename = reformat_filename(filename, False)
    if flatten_directories:
        output_path = os.path.join(output_folder, new_folders, new_filename)
    else:
        output_path = os.path.join(output_folder, new_folders, *folder_structure, new_filename)
    # Create necessary subdirectories
    directory_path = os.path.dirname(output_path)
    # Ensure the path ends with a slash if not already present
    if not directory_path.endswith('/'):
        directory_path += '/'
    os.makedirs(os.path.dirname(directory_path), exist_ok=True)

    # Move the file
    shutil.move(input_file_path, output_path)


def safe_delete_dir(directory_path):
    """Safely delete a directory, only if it is empty."""
    try:
        os.rmdir(directory_path)
    except OSError as e:
        # print(f"Failed to remove directory {directory_path}. Error: {str(e)}")
        pass


def get_total_mkv_files(path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        # Skip directories or files starting with '.'
        if '/.' in dirpath or dirpath.startswith('./.'):
            continue
        for f in filenames:
            if f.startswith('.'):
                continue
            if f.endswith('.mkv'):
                total += 1
    return total


def replace_tags_in_file(file_path, replacement):
    # List of tags to exclude from replacement
    # https://support.plex.tv/articles/local-files-for-trailers-and-extras/
    excluded_tags = [
        "-behindthescenes", "-deleted", "-featurette",
        "interview", "-scene", "-short", "-trailer", "-other"
    ]

    # Regular expression to match tags
    tag_regex = re.compile(r"-\w*(-sample)?(\.\w{2,3})?$", re.IGNORECASE)

    # Convert the relative path to an absolute path
    abs_file_path = os.path.abspath(file_path)

    dirpath, filename = os.path.split(abs_file_path)
    base, ext = os.path.splitext(filename)

    if ext in {".mkv", ".srt"}:
        match = tag_regex.search(base)
        if match:
            tag = match.group(0)  # Capture the entire tag (e.g., "-trailer", "-sample")

            # Check if the tag is in the list of excluded tags
            if any(excluded_tag in tag for excluded_tag in excluded_tags):
                return filename  # Return the original filename if tag is excluded

            base = tag_regex.sub(replacement + (match.group(2) or ""), base)
        elif ext == ".mkv":
            base += replacement

    return base + ext


def remove_sample_files_and_dirs(root_dir):
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Exclude directories starting with a dot
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for dirname in dirnames:
            if dirname.lower() == "sample":
                shutil.rmtree(os.path.join(dirpath, dirname))

        for filename in filenames:
            base, ext = os.path.splitext(filename)
            if not filename.startswith('.'):
                if base.lower().endswith("-sample") or base.lower() == "sample":
                    os.remove(os.path.join(dirpath, filename))


def fix_episodes_naming(directory):
    for dirpath, _, filenames in os.walk(directory):
        for file_name in filenames:
            if file_name.endswith(".mkv") or file_name.endswith(".srt"):
                parts = os.path.splitext(file_name)[0].split(".")
                extension = os.path.splitext(file_name)[1]
                season_index = next((i for i, part in enumerate(parts) if part.lower() == 'season'), None)
                episode_index = next((i for i, part in enumerate(parts) if part.lower() == 'episode'), None)

                if season_index is not None and episode_index is not None:
                    # Preserve all parts of the original name before "season" and after "episode"
                    show_name = '.'.join(parts[:season_index])
                    post_episode = '.'.join(parts[episode_index+2:]) if episode_index + 2 < len(parts) else ""

                    # Determine the case for 'S' and 'E'
                    se_case = 'S' if parts[season_index][0].isupper() else 's'
                    ee_case = 'E' if parts[episode_index][0].isupper() else 'e'

                    # Generate new file name, preserving case of "season" and "episode"
                    new_name = f"{show_name}.{se_case}{int(parts[season_index+1]):02}{ee_case}{int(parts[episode_index+1]):02}"
                    new_name += f".{post_episode}" if post_episode else ""
                    new_name += extension

                    shutil.move(os.path.join(dirpath, file_name), os.path.join(dirpath, new_name))


def remove_ds_store(root_dir):
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if ".DS_Store" in filenames:
            try:
                os.remove(os.path.join(dirpath, ".DS_Store"))
            except OSError as e:
                print(f"Error: {e.strerror}")


def remove_wsl_identifiers(root_dir):
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if ".Identifier" in filenames:
            try:
                os.remove(os.path.join(dirpath, ".Identifier"))
            except OSError as e:
                print(f"Error: {e.strerror}")
