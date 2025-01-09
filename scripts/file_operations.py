import os
import shutil
import re
import rarfile
import zipfile
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def remove_empty_dirs(path):
    for root, dirs, files in os.walk(path, topdown=False):
        if not dirs and not files:
            try:
                os.rmdir(root)
            except OSError:
                pass


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


def move_directory_contents(logger, source_directory, destination_directory, file_counter=[0], total_files=0):
    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    initial_available_space = get_free_space(destination_directory)
    available_space = initial_available_space
    skipped_files_counter = [0]
    all_required_space = 0.0
    actual_file_sizes = 0.0
    moved_file_sizes = 0.0

    items = []
    for root, dirs, files in os.walk(source_directory):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        files[:] = [f for f in files if not f.startswith('.')]

        for d in dirs:
            rel_path = os.path.relpath(os.path.join(root, d), source_directory)
            items.append(rel_path)
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), source_directory)
            items.append(rel_path)

    def sort_key(rel_path):
        depth = len(rel_path.split(os.sep))
        return -depth, rel_path.lower()

    items.sort(key=sort_key)

    def move_item(rel_path):
        nonlocal available_space, actual_file_sizes, all_required_space, moved_file_sizes
        s = os.path.join(source_directory, rel_path)
        d = os.path.join(destination_directory, rel_path)

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 3.5
            actual_file_sizes += file_size
            all_required_space += required_space

            if available_space >= all_required_space:
                available_space -= required_space
                moved_file_sizes += file_size
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.move(s, d)
                file_counter[0] += 1
                print_with_progress_files(logger, file_counter[0], total_files, 'INFO', 'Moving file')
            else:
                skipped_files_counter[0] += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(move_item, item) for item in items]
        concurrent.futures.wait(futures)

    remove_empty_dirs(source_directory)

    return {
        "total_files": total_files,
        "actual_file_sizes_gb": actual_file_sizes / (1024 ** 3),
        "required_space_gib": all_required_space / (1024 ** 3),
        "available_space_gib": available_space / (1024 ** 3),
        "moved_files_gib": moved_file_sizes / (1024 ** 3),
        "skipped_files": skipped_files_counter[0]
    }


def copy_directory_contents(logger, source_directory, destination_directory, file_counter=[0], total_files=0):
    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    initial_available_space = get_free_space(destination_directory)
    available_space = initial_available_space
    skipped_files_counter = [0]
    all_required_space = 0.0
    actual_file_sizes = 0.0
    copied_file_sizes = 0.0

    items = []
    for root, dirs, files in os.walk(source_directory):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        files[:] = [f for f in files if not f.startswith('.')]

        for d in dirs:
            rel_path = os.path.relpath(os.path.join(root, d), source_directory)
            items.append(rel_path)
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), source_directory)
            items.append(rel_path)

    def sort_key(rel_path):
        depth = len(rel_path.split(os.sep))
        return (-depth, rel_path.lower())

    items.sort(key=sort_key)

    def copy_item(rel_path):
        nonlocal available_space, actual_file_sizes, all_required_space, copied_file_sizes
        s = os.path.join(source_directory, rel_path)
        d = os.path.join(destination_directory, rel_path)

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 3.5
            actual_file_sizes += file_size
            all_required_space += required_space

            if available_space >= all_required_space:
                available_space -= required_space
                copied_file_sizes += file_size
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy(s, d)
                file_counter[0] += 1
                print_with_progress_files(logger, file_counter[0], total_files, 'INFO', 'Copying file')
            else:
                skipped_files_counter[0] += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(copy_item, item) for item in items]
        concurrent.futures.wait(futures)

    return {
        "total_files": total_files,
        "actual_file_sizes_gb": actual_file_sizes / (1024 ** 3),
        "required_space_gib": all_required_space / (1024 ** 3),
        "available_space_gib": available_space / (1024 ** 3),
        "copied_files_gib": copied_file_sizes / (1024 ** 3),
        "skipped_files": skipped_files_counter[0]
    }


def move_file_to_output(input_file_path, output_folder, folder_structure):
    filename = os.path.basename(input_file_path)
    flatten_directories = True

    # Step 1: Determine folder structure based on the current (possibly disguised) filename
    new_folders, new_filename = reformat_filename(filename, False)

    base, ext = os.path.splitext(filename)
    pattern = re.compile(r"^(?P<prefix>.+? - (?:S00E\d+ - )?)(?P<original>.+)$", re.IGNORECASE)
    match = pattern.match(base)
    if match:
        original_part = match.group('original')
        # Check if this is indeed an extra by verifying it ends with an excluded tag
        if any(original_part.lower().endswith(tag) for tag in excluded_tags):
            # Restore the filename by removing the prefix
            restored_filename = original_part + ext
        else:
            # Not an extra or doesn't end with excluded tag, no restore needed
            restored_filename = filename
    else:
        # No match means no prefix was found, so it's not a processed extra
        restored_filename = filename

    # Step 3: Construct the output_path using the obtained folder structure from reformat_filename
    # and the restored filename
    if flatten_directories:
        output_path = os.path.join(output_folder, new_folders, restored_filename)
    else:
        output_path = os.path.join(output_folder, new_folders, *folder_structure, restored_filename)

    # Ensure directories exist
    directory_path = os.path.dirname(output_path)
    if not directory_path.endswith('/'):
        directory_path += '/'
    os.makedirs(os.path.dirname(directory_path), exist_ok=True)

    # Step 4: Move the file
    shutil.move(input_file_path, output_path)


def safe_delete_dir(directory_path):
    """Safely delete a directory, only if it is empty."""
    try:
        os.rmdir(directory_path)
    except OSError as e:
        # print(f"Failed to remove directory {directory_path}. Error: {str(e)}")
        pass


def wait_for_stable_files(path):
    def is_file_stable(file_path):
        """Check if a file's size is stable (indicating it is fully copied)."""
        initial_size = os.path.getsize(file_path)
        time.sleep(2.5)
        new_size = os.path.getsize(file_path)
        return initial_size == new_size

    stable_files = set()

    while True:
        # Get the current list of files to check
        files = []
        for dirpath, dirnames, filenames in os.walk(path):
            # Modify dirnames in-place to skip directories starting with a dot
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            files.extend(os.path.join(dirpath, f) for f in filenames if not f.startswith('.'))

        def process_file(file_path):
            if file_path in stable_files:
                return None  # Skip already stable files
            if is_file_stable(file_path):
                return file_path  # Return stable file
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {executor.submit(process_file, file): file for file in files if file not in stable_files}

            for future in as_completed(future_to_file):
                result = future.result()
                if result:
                    stable_files.add(result)

        # Check again
        time.sleep(2.5)
        files = []
        for dirpath, dirnames, filenames in os.walk(path):
            # Modify dirnames in-place to skip directories starting with a dot
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            files.extend(os.path.join(dirpath, f) for f in filenames if not f.startswith('.'))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {executor.submit(process_file, file): file for file in files if file not in stable_files}

            for future in as_completed(future_to_file):
                result = future.result()
                if result:
                    stable_files.add(result)

        if len(stable_files) >= len(files):
            break  # Exit if all files are stable

    return len(stable_files)


def replace_tags_in_file(file_path, replacement):
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
                if base.lower().endswith("-sample") or base.lower() == "sample" or base.lower().endswith(".sample"):
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
