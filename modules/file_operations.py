import os
import shutil
import re
import rarfile
import zipfile
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from pathvalidate import sanitize_filename

from modules.misc import *
from modules.logger import *


def copy_file(src, dst):
    shutil.copy2(src, dst)


def move_file(src, dst):
    # Create any necessary subdirectories
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    # Move the file
    shutil.move(src, dst)


def get_folder_size_gb(folder_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for file in filenames:
            file_path = os.path.join(dirpath, file)
            if os.path.exists(file_path):  # Ensure the file exists
                total_size += os.path.getsize(file_path)

    return round(total_size / (1024 ** 3), 2)  # Convert to GB and round to 2 decimal places


def extract_archives(logger, input_folder):
    header = "FILES"
    description = "Extract archives"

    for root, dirs, files in os.walk(input_folder):
        # Filter for .rar and .zip files
        archive_files = [f for f in files if f.endswith('.rar') or f.endswith('.zip')]

        if archive_files:
            completed_count = 0
            print_with_progress(logger, completed_count, len(archive_files), header=header, description=description)
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

                    completed_count += 1
                    print_with_progress(logger, completed_count, len(archive_files), header=header,
                                        description=description)

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
    actual_moved_file_sizes = 0.0
    space_lock = Lock()
    file_counter_lock = Lock()

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
        nonlocal available_space, actual_file_sizes, all_required_space, actual_moved_file_sizes
        s = os.path.join(source_directory, rel_path)
        d = os.path.join(destination_directory, rel_path)

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 3.5

            with space_lock:
                all_required_space += required_space
                actual_file_sizes += file_size

                if initial_available_space >= all_required_space:
                    available_space -= file_size
                    actual_moved_file_sizes += file_size
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.move(s, d)
                    with file_counter_lock:
                        file_counter[0] += 1
                        print_with_progress_files(logger, file_counter[0], total_files, 'INFO', 'Moving file')
                else:
                    skipped_files_counter[0] += 1

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(move_item, item) for item in items]
        concurrent.futures.wait(futures)

    remove_empty_dirs(source_directory)

    return {
        "total_files": total_files,
        "actual_file_sizes": actual_file_sizes / (1024 ** 3),
        "actual_moved_file_sizes": actual_moved_file_sizes / (1024 ** 3),
        "required_space_gib": all_required_space / (1024 ** 3),
        "available_space_gib": initial_available_space / (1024 ** 3),
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
    actual_copied_file_sizes = 0.0
    space_lock = Lock()
    file_counter_lock = Lock()

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

    def copy_item(rel_path):
        nonlocal available_space, actual_file_sizes, all_required_space, actual_copied_file_sizes
        s = os.path.join(source_directory, rel_path)
        d = os.path.join(destination_directory, rel_path)

        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
        else:
            file_size = os.path.getsize(s)
            required_space = file_size * 3.5

            with space_lock:
                all_required_space += required_space
                actual_file_sizes += file_size

                if initial_available_space >= all_required_space:
                    available_space -= file_size
                    actual_copied_file_sizes += file_size
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy(s, d)
                    with file_counter_lock:
                        file_counter[0] += 1
                        print_with_progress_files(logger, file_counter[0], total_files, 'INFO', 'Copying file')
                else:
                    skipped_files_counter[0] += 1

    max_worker_threads = get_worker_thread_count()
    num_workers = max(1, max_worker_threads)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(copy_item, item) for item in items]
        concurrent.futures.wait(futures)

    return {
        "total_files": total_files,
        "actual_file_sizes": actual_file_sizes / (1024 ** 3),
        "actual_copied_file_sizes": actual_copied_file_sizes / (1024 ** 3),
        "required_space_gib": all_required_space / (1024 ** 3),
        "available_space_gib": available_space / (1024 ** 3),
        "skipped_files": skipped_files_counter[0]
    }


def move_file_to_output(logger, debug, input_file_path, output_folder, folder_structure):
    original_folders, original_restored_filename = unflatten_file(input_file_path, '')

    base, ext = os.path.splitext(original_restored_filename)
    new_folders_str = original_restored_filename
    full_info = {}
    full_info_found = False
    is_extra = False

    normalize_filenames = check_config(config, 'general', 'normalize_filenames')
    keep_original_file_structure = check_config(config, 'general', 'keep_original_file_structure')

    sep = ' ' if normalize_filenames.lower() in ('full-jf', 'simple-jf') else ' - '

    file_info = reformat_filename(original_restored_filename, True, False, False)
    media_type = file_info["media_type"]
    media_name = file_info["media_name"]

    tv_extra_match = re.search(r"S000E\d+\s*-\s*(?P<original>.+)$", base, re.IGNORECASE)
    if tv_extra_match:
        restored_filename = tv_extra_match.group("original") + ext
        if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
            # Using S01E01 as a placeholder to get the full show name with year
            full_info = get_tv_episode_metadata(logger, debug, f"{media_name}{sep}S01E01")
            if full_info:
                new_folders_str = (f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                                   f"S01E01.mkv")
                full_info_found = True
                is_extra = True
    else:
        if media_type in ['movie', 'movie_hdr']:
            pattern = re.compile(r"^" + re.escape(media_name) + r"\s*-\s*(?P<extra>.+)$")
            movie_extra_match = pattern.match(base)
            if movie_extra_match:
                restored_filename = movie_extra_match.group("extra") + ext
            else:
                if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
                    if media_type == 'movie_hdr':
                        restored_filename = f"{media_name}{sep}HDR{ext}"
                    else:
                        restored_filename = f"{media_name}{ext}"
                else:
                    restored_filename = original_restored_filename
        elif media_type in ['tv_show', 'tv_show_hdr']:
            season, episodes = extract_season_episode(original_restored_filename)
            if season and episodes:
                episode_list = compact_episode_list(episodes, True)
                formatted_season = f"{season:02}" if season < 100 else f"{season:03}"
                if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
                    if normalize_filenames.lower() in ('full', 'full-jf'):
                        full_info = get_tv_episode_metadata(logger, debug, f"{media_name}{sep}S{formatted_season}E{episode_list}")
                    if media_type == 'tv_show_hdr':
                        if full_info:
                            restored_filename = (f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                                                 f"S{formatted_season}E{episode_list}{sep}{full_info['episode_title']}{sep}HDR{ext}")
                            new_folders_str = (f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                                               f"S{formatted_season}E{episode_list}{sep}{full_info['episode_title']}{sep}HDR{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{sep}HDR{ext}"
                    else:
                        if full_info:
                            restored_filename = (f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                                                 f"S{formatted_season}E{episode_list}{sep}{full_info['episode_title']}{ext}")
                            new_folders_str = (f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                                               f"S{formatted_season}E{episode_list}{sep}{full_info['episode_title']}{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{ext}"
                else:
                    restored_filename = original_restored_filename
            else:
                restored_filename = original_restored_filename
        else:
            restored_filename = original_restored_filename

    restored_filename = sanitize_filename(restored_filename)
    new_folders, _ = reformat_filename(new_folders_str, False, full_info_found, is_extra)

    if keep_original_file_structure == 'true':
        new_folders = original_folders
        restored_filename = original_restored_filename
    elif keep_original_file_structure == 'fallback':
        if media_type in ['other']:
            new_folders = os.path.join(new_folders, original_folders)
    elif keep_original_file_structure == 'false':
        pass

    output_path = os.path.join(output_folder, new_folders, restored_filename)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    log_debug(logger, f"Moving file '{input_file_path}' to '{output_path}'")
    if os.path.exists(input_file_path):
        shutil.move(input_file_path, output_path)

    return {
        "output_folder": new_folders,
        "media_name": media_name,
        "filename": restored_filename
    }


def safe_delete_dir(directory_path):
    """Safely delete a directory, only if it is empty."""
    try:
        os.rmdir(directory_path)
    except OSError as e:
        # print(f"Failed to remove directory {directory_path}. Error: {str(e)}")
        pass


def wait_for_stable_files(path):
    def is_file_stable(file_path):
        try:
            """Check if a file's size is stable (indicating it is fully copied)."""
            initial_size = os.path.getsize(file_path)
            time.sleep(2.5)
            new_size = os.path.getsize(file_path)
            return initial_size == new_size
        except Exception:
            raise CorruptedFile

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

        # Calculate number of workers and internal threads
        max_worker_threads = get_worker_thread_count()
        num_workers = max(1, max_worker_threads)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
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

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
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
    # This regex matches a base name that ends with an optional separator (-, _, or .) followed by "sample"
    sample_pattern = re.compile(r'(?:[-_.]?sample)$', re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Exclude directories that start with a dot
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]

        # Remove directories named exactly "sample"
        for dirname in dirnames:
            if dirname.lower() == "sample":
                shutil.rmtree(os.path.join(dirpath, dirname))

        # Check each file for sample pattern in its base name
        for filename in filenames:
            if filename.startswith('.'):
                continue
            base, ext = os.path.splitext(filename)
            if sample_pattern.search(base):
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
                else:
                    new_name = file_name

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
