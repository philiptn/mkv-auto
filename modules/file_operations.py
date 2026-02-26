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


def get_folder_size(folder_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for file in filenames:
            file_path = os.path.join(dirpath, file)
            if os.path.exists(file_path):  # Ensure the file exists
                total_size += os.path.getsize(file_path)

    return total_size


def extract_archives(logger, input_folder):
    header = "FILES"
    description = "Extracting archives"

    archives = []
    for root, _, files in os.walk(input_folder):
        for f in files:
            if f.lower().endswith(('.rar', '.zip')):
                archives.append((root, f))

    total = len(archives)
    completed = 0

    if total == 0:
        return

    print_with_progress(logger, completed, total, header=header, description=description)

    for root, archive_file in archives:
        archive_path = os.path.join(root, archive_file)
        temp_extract_path = os.path.join(root, f".tmp_extract_{uuid4().hex}")

        try:
            os.makedirs(temp_extract_path, exist_ok=True)

            if archive_file.lower().endswith('.rar'):
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(temp_extract_path)
            else:
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(temp_extract_path)

            for entry in os.scandir(temp_extract_path):
                src = entry.path
                dst = os.path.join(input_folder, entry.name)
                if os.path.exists(dst):
                    base, ext = os.path.splitext(entry.name)
                    i = 1
                    while os.path.exists(os.path.join(input_folder, f"{base} ({i}){ext}")):
                        i += 1
                    dst = os.path.join(input_folder, f"{base} ({i}){ext}")
                shutil.move(src, dst)

            shutil.rmtree(temp_extract_path, ignore_errors=True)
            if os.path.exists(archive_path):
                os.remove(archive_path)

            if archive_file.lower().endswith('.rar'):
                prefix = os.path.splitext(archive_file)[0]
                for i in range(100):
                    part_name = f"{prefix}.r{i:02d}"
                    part_path = os.path.join(root, part_name)
                    if os.path.exists(part_path):
                        try:
                            os.remove(part_path)
                        except Exception:
                            pass

            completed += 1
            print_with_progress(logger, completed, total, header=header, description=description)

        except Exception as e:
            try:
                shutil.rmtree(temp_extract_path, ignore_errors=True)
            except Exception:
                pass
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
        # Check if the directory is now empty
        if not os.listdir(root):
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
        "actual_file_sizes": actual_file_sizes,
        "actual_moved_file_sizes": actual_moved_file_sizes,
        "required_space_gib": all_required_space,
        "available_space_gib": initial_available_space,
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
        "actual_file_sizes": actual_file_sizes,
        "actual_copied_file_sizes": actual_copied_file_sizes,
        "required_space_gib": all_required_space,
        "available_space_gib": available_space,
        "skipped_files": skipped_files_counter[0]
    }


def detect_dynamic_range_from_filename(filename):
    dv_pattern = re.compile(
        r'(?i)(?:^|[.\- _])(dv|dovi|dolby[ ._-]?vision)(?=$|[.\- _])'
    )

    hdr_pattern = re.compile(
        r'(?i)(?:^|[.\- _])(hdr10\+?|hdr)(?=$|[.\- _])'
    )

    false_hdr = re.compile(r'(?i)hdrip')
    false_dv = re.compile(r'(?i)dvd')

    is_dv = dv_pattern.search(filename) is not None and not false_dv.search(filename)
    is_hdr = hdr_pattern.search(filename) is not None and not false_hdr.search(filename)

    return {
        'is_dv': is_dv,
        'is_hdr': is_hdr,
        'is_dv_hdr': is_dv and is_hdr
    }


def move_file_to_output(logger, debug, input_file_path, output_folder, folder_structure):
    original_folders, original_restored_filename = unflatten_file(input_file_path, '')

    base, ext = os.path.splitext(original_restored_filename)
    new_folders_str = original_restored_filename
    full_info = {}
    full_info_found = False
    is_extra = False
    extra_is_hdr = False
    extra_is_4k = False

    normalize_filenames = check_config(config, 'general', 'normalize_filenames')
    keep_original_file_structure = check_config(config, 'general', 'keep_original_file_structure')

    sep = ' ' if normalize_filenames.lower() in ('full-jf', 'simple-jf') else ' - '

    file_info = reformat_filename(original_restored_filename, True, False, False)
    media_type = file_info["media_type"]
    media_name = file_info["media_name"]

    dynamic = detect_dynamic_range_from_filename(original_restored_filename)
    is_dv = dynamic['is_dv']
    is_hdr = dynamic['is_hdr']
    is_dv_hdr = dynamic['is_dv_hdr']

    if media_type.startswith('movie'):
        if is_dv_hdr:
            media_type = 'movie_dv_hdr'
        elif is_dv:
            media_type = 'movie_dv'
        elif is_hdr:
            media_type = 'movie_hdr'

    elif media_type.startswith('tv_show'):
        if is_dv_hdr:
            media_type = 'tv_show_dv_hdr'
        elif is_dv:
            media_type = 'tv_show_dv'
        elif is_hdr:
            media_type = 'tv_show_hdr'

    extras_pattern = "|".join(
        re.escape(e.lstrip("-")) for e in extras_definitions
    )

    tv_extra_match = re.search(
        rf"S000E\d+\s*[-\s_]*"
        rf"(?P<original>.*\b(?:{extras_pattern})\b.*)$",
        base,
        re.IGNORECASE,
    )

    if tv_extra_match:
        is_extra = True
        restored_filename = tv_extra_match.group("original") + ext
        if restored_filename.startswith('HDR - '):
            extra_is_hdr = True
            restored_filename = restored_filename.strip('HDR - ')
        elif restored_filename.endswith('4K - '):
            extra_is_4k = True
            restored_filename = restored_filename.strip('4K - ')
        if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
            if normalize_filenames.lower() in ('full', 'full-jf'):
                full_info = get_tv_episode_metadata(logger, debug, f"{media_name}{sep}S01E01")
                if full_info:
                    additional_info = ''
                    if extra_is_hdr:
                        additional_info = f'{sep}HDR'
                    if extra_is_4k:
                        additional_info = f'{sep}4K'
                    new_folders_str = (
                        f"{full_info['show_name']} ({full_info['show_year']}){sep}"
                        f"S01E01{additional_info}.mkv"
                    )
                    full_info_found = True
    else:
        if media_type in ['movie', 'movie_hdr', 'movie_dv', 'movie_dv_hdr', 'movie_4k']:
            pattern = re.compile(r"^" + re.escape(media_name) + r"\s*-\s*(?P<extra>.+)$")
            movie_extra_match = pattern.match(base)
            if movie_extra_match:
                restored_filename = movie_extra_match.group("extra") + ext
            else:
                if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
                    if media_type == 'movie_dv_hdr':
                        restored_filename = f"{media_name}{sep}DV HDR{ext}"
                    elif media_type == 'movie_dv':
                        restored_filename = f"{media_name}{sep}DV{ext}"
                    elif media_type == 'movie_hdr':
                        restored_filename = f"{media_name}{sep}HDR{ext}"
                    elif media_type == 'movie_4k':
                        restored_filename = f"{media_name}{sep}4K{ext}"
                    else:
                        restored_filename = f"{media_name}{ext}"
                else:
                    restored_filename = original_restored_filename
        elif media_type in ['tv_show', 'tv_show_hdr', 'tv_show_dv', 'tv_show_dv_hdr', 'tv_show_4k', 'anime']:
            season, episodes = extract_season_episode(original_restored_filename)
            if season and episodes:
                episode_list = compact_episode_list(episodes, True, True)
                formatted_season = f"{season:02}" if season < 100 else f"{season:03}"
                show_name_format = media_name
                if normalize_filenames.lower() in ('full', 'full-jf', 'simple', 'simple-jf'):
                    if normalize_filenames.lower() in ('full', 'full-jf'):
                        full_info = get_tv_episode_metadata(logger, debug, f"{media_name}{sep}S{formatted_season}{episode_list}")
                        # If no match, try to get show name only using S01E01
                        if not full_info:
                            full_info = get_tv_episode_metadata(logger, debug,
                                                                f"{media_name}{sep}S01E01")
                            if full_info:
                                episode_list_short = compact_episode_list(episodes, False)
                                full_info['episode_title'] = f'Episode {episode_list_short}'
                    if full_info:
                        if normalize_filenames.lower() in ('simple', 'simple-jf'):
                            show_name_format = f"{full_info['show_name']}"
                        else:
                            show_name_format = f"{full_info['show_name']} ({full_info['show_year']})"
                    if media_type == 'tv_show_dv_hdr':
                        if full_info:
                            restored_filename = (f"{show_name_format}{sep}"
                                                f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}DV HDR{ext}")
                            new_folders_str = (f"{show_name_format}{sep}"
                                            f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}DV HDR{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{sep}DV HDR{ext}"
                    elif media_type == 'tv_show_dv':
                        if full_info:
                            restored_filename = (f"{show_name_format}{sep}"
                                                f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}DV{ext}")
                            new_folders_str = (f"{show_name_format}{sep}"
                                            f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}DV{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{sep}DV{ext}"
                    elif media_type == 'tv_show_hdr':
                        if full_info:
                            restored_filename = (f"{show_name_format}{sep}"
                                                 f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}HDR{ext}")
                            new_folders_str = (f"{show_name_format}{sep}"
                                               f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}HDR{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{sep}HDR{ext}"
                    elif media_type == 'tv_show_4k':
                        if full_info:
                            restored_filename = (f"{show_name_format}{sep}"
                                                 f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}4K{ext}")
                            new_folders_str = (f"{show_name_format}{sep}"
                                               f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{sep}4K{ext}")
                            media_name = full_info['show_name']
                            full_info_found = True
                        else:
                            restored_filename = f"{media_name}{sep}S{formatted_season}E{episode_list}{sep}4K{ext}"
                    else:
                        if full_info:
                            restored_filename = (f"{show_name_format}{sep}"
                                                 f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{ext}")
                            new_folders_str = (f"{show_name_format}{sep}"
                                               f"S{formatted_season}{episode_list}{sep}{full_info['episode_title']}{ext}")
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
        except Exception as e:
            traceback.print_tb(e.__traceback__)
            raise

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
            if any(excluded_tag in tag for excluded_tag in extras_definitions):
                return filename  # Return the original filename if tag is excluded

            base = tag_regex.sub(replacement + (match.group(2) or ""), base)
        elif ext == ".mkv":
            base += replacement

    return base + ext


def remove_sample_files_and_dirs(root_dir):
    # Matches base names ending with optional separator + "sample"
    end_sample_pattern = re.compile(r'(?:[-_.]?sample)$', re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Exclude dot directories
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]

        # Remove directories named exactly "sample"
        for dirname in dirnames:
            if dirname.lower() == "sample":
                shutil.rmtree(os.path.join(dirpath, dirname))

        # Remove files with names starting with or ending in "sample"
        for filename in filenames:
            if filename.startswith('.'):
                continue
            base, ext = os.path.splitext(filename)
            if end_sample_pattern.search(base) or base.lower().startswith('sample'):
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
