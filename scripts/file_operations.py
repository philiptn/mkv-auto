import os
import shutil
import re
import rarfile
import zipfile
from datetime import datetime
import concurrent.futures


# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'

max_workers = int(os.cpu_count() * 0.8)  # Use 80% of the CPU cores


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def copy_file(src, dst):
    shutil.copy2(src, dst)


def move_file(src, dst):
    # Create any necessary subdirectories
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    # Move the file
    shutil.move(src, dst)


def extract_archives(input_folder):

    for root, dirs, files in os.walk(input_folder):
        # Filter for .rar and .zip files
        archive_files = [f for f in files if f.endswith('.rar') or f.endswith('.zip')]

        for archive_file in archive_files:
            archive_path = os.path.join(root, archive_file)

            try:
                if archive_file.endswith('.rar'):
                    print(f"{GREY}[UTC {get_timestamp()}] [RAR]{RESET} Extracting '{archive_file}'...")
                    # Extract RAR file
                    with rarfile.RarFile(archive_path) as rf:
                        rf.extractall(root)
                elif archive_file.endswith('.zip'):
                    print(f"{GREY}[UTC {get_timestamp()}] [ZIP]{RESET} Extracting '{archive_file}'...")
                    # Extract ZIP file
                    with zipfile.ZipFile(archive_path, 'r') as zf:
                        zf.extractall(root)
            except Exception as e:
                print(f"{GREY}[UTC {get_timestamp()}] [ERROR]{RESET} Failed to extract {archive_file}: {e}")


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


def move_file_with_progress(src_file, dst_file, pbar, file_counter, total_files):
    chunk_size = 1024 * 1024  # e.g., move in 1 MB chunks
    with open(src_file, 'rb') as fsrc, open(dst_file, 'wb') as fdst:
        while True:
            chunk = fsrc.read(chunk_size)
            if not chunk:
                break
            fdst.write(chunk)
            pbar.update(len(chunk))

    os.remove(src_file)  # Remove the source file after copying
    pbar.set_description(f"{GREY}[INFO]{RESET} Moving file {file_counter[0]} of {total_files}")


def move_directory_contents(source_directory, destination_directory, pbar, file_counter=[0], total_files=0, max_workers=4):
    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    # Function to move a single file or directory
    def move_item(s, d):
        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            for item in os.listdir(s):
                next_source = os.path.join(s, item)
                next_destination = os.path.join(d, item)
                move_item(next_source, next_destination)
            # After moving all items, check if the directory is empty and remove it
            if not os.listdir(s):
                os.rmdir(s)
        else:
            with pbar.get_lock():  # Synchronize access to shared resources
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
            futures.append(executor.submit(move_item, s, d))

        # Wait for all the tasks to complete
        concurrent.futures.wait(futures)


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


def copy_directory_contents(source_directory, destination_directory, pbar, file_counter=[0], total_files=0):
    if not os.path.exists(destination_directory):
        os.makedirs(destination_directory)

    # Function to copy a single file or directory
    def copy_item(s, d):
        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            for item in os.listdir(s):
                next_source = os.path.join(s, item)
                next_destination = os.path.join(d, item)
                copy_item(next_source, next_destination)
        else:
            with pbar.get_lock():  # Synchronize access to shared resources
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
            futures.append(executor.submit(copy_item, s, d))

        # Wait for all the tasks to complete
        concurrent.futures.wait(futures)


def to_sentence_case(s):
    if s == s.lower():
        return ' '.join(word.capitalize() for word in s.split(' '))
    else:
        return s


def rename_others_file_to_folder(input_dir, movie_folder, tv_folder, movie_hdr_folder, tv_hdr_folder, others_folder):
    # Iterate through the input directory recursively
    for root, dirs, files in os.walk(input_dir):
        parent_folder_name = os.path.basename(root)
        a, parent_folder_reformatted = reformat_filename(
            parent_folder_name + '.mkv', movie_folder, tv_folder, movie_hdr_folder, tv_hdr_folder, others_folder)

        # If the parent folder does not match any pattern, skip to next
        if parent_folder_reformatted.startswith(others_folder):
            continue

        # Check if the file should be categorized as others
        for filename in files:
            if not filename.endswith('.mkv'):
                continue  # Skip non-mkv files
            
            a, new_filename = reformat_filename(
                filename, movie_folder, tv_folder, movie_hdr_folder, tv_hdr_folder, others_folder)
            if new_filename.startswith(others_folder):
                # Rename the file to match its parent folder
                new_file_path = os.path.join(root, f"{parent_folder_name}.{filename.split('.')[-1]}")
                old_file_path = os.path.join(root, filename)
                shutil.move(old_file_path, new_file_path)


def reformat_filename(filename, movie_folder, tv_folder, movie_hdr_folder, tv_hdr_folder, others_folder):
    # Regular expression to match TV shows with season and episode, with or without year
    tv_show_pattern1 = re.compile(r"^(.*?)([. ]((?:19|20)\d{2}))?[. ]s(\d{2})e(\d{2})", re.IGNORECASE)
    # Regular expression to match TV shows with season range, with or without year
    tv_show_pattern2 = re.compile(r"^(.*?)([. ]((?:19|20)\d{2}))?[. ]s(\d{2})-s(\d{2})", re.IGNORECASE)
    # Regular expression to match movies
    movie_pattern = re.compile(r"^(.*?)[ .]*(?:\((\d{4})\)|(\d{4}))[ .]*(.*\.(mkv|srt))$", re.IGNORECASE)

    # Regular expression to detect 2160p without h264 or x264
    hdr_pattern = re.compile(r"2160p", re.IGNORECASE)
    non_hdr_pattern = re.compile(r"h264|x264", re.IGNORECASE)

    is_hdr = hdr_pattern.search(filename) and not non_hdr_pattern.search(filename)

    tv_match1 = tv_show_pattern1.match(filename)
    tv_match2 = tv_show_pattern2.match(filename)
    movie_match = movie_pattern.match(filename)

    if tv_match1:
        # TV show with season and episode
        showname = tv_match1.group(1).replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname) # Transform to sentence case
        year = tv_match1.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder
        # Format the filename
        return os.path.join(folder, f"{showname} ({year})") if year else os.path.join(folder, showname), filename
    elif tv_match2:
        # TV show with season range
        showname = tv_match2.group(1).replace('.', ' ')
        showname = showname.replace(' -', '')
        showname = to_sentence_case(showname) # Transform to sentence case
        year = tv_match2.group(3)
        folder = tv_hdr_folder if is_hdr else tv_folder
        # Format the filename
        return os.path.join(folder, f"{showname} ({year})") if year else os.path.join(folder, showname), filename
    elif movie_match:
        # Movie
        folder = movie_hdr_folder if is_hdr else movie_folder
        return os.path.join(folder), filename
    else:
        # Unidentified file
        return os.path.join(others_folder), filename


def move_file_to_output(input_file_path, output_folder, movie_folder, tv_folder, movie_hdr_folder,
                        tv_hdr_folder, others_folder, folder_structure, flatten_directories):
    filename = os.path.basename(input_file_path)
    new_folders, new_filename = reformat_filename(filename, movie_folder, tv_folder,
                                                  movie_hdr_folder, tv_hdr_folder, others_folder)
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
    tag_regex = re.compile(r"-\w*(-sample)?(\.\w{2,3})?$", re.IGNORECASE)

    # Convert the relative path to an absolute path
    abs_file_path = os.path.abspath(file_path)

    dirpath, filename = os.path.split(abs_file_path)
    base, ext = os.path.splitext(filename)
    if ext in {".mkv", ".srt"}:
        match = tag_regex.search(base)
        if match:
            base = tag_regex.sub(replacement + (match.group(2) or ""), base)
        elif ext == ".mkv":
            base += replacement
        shutil.move(os.path.join(dirpath, filename), os.path.join(dirpath, base + ext))

    return base + ext


def flatten_dirs(root_dir):
    # Get a list of all first level directories
    level_1_dirs = [d.path for d in os.scandir(root_dir) if d.is_dir() and not d.name.startswith(".")]

    # Move files from subdirectories to level 1 directories
    for level_1_dir in level_1_dirs:
        for dirpath, dirnames, filenames in os.walk(level_1_dir):
            # Exclude directories starting with "."
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            for filename in filenames:
                if filename.endswith(('.mkv', '.srt')):
                    new_path = os.path.join(level_1_dir, filename)
                    if not os.path.exists(new_path):
                        shutil.move(os.path.join(dirpath, filename), new_path)

    # Remove subdirectories
    for level_1_dir in level_1_dirs:
        for dirpath, dirnames, filenames in os.walk(level_1_dir, topdown=False):
            # Exclude directories starting with "."
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            for dirname in dirnames:
                shutil.rmtree(os.path.join(dirpath, dirname))


def remove_sample_files_and_dirs(root_dir):
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Exclude directories starting with a dot
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for dirname in dirnames:
            if dirname.lower() == "sample":
                shutil.rmtree(os.path.join(dirpath, dirname))

        for filename in filenames:
            if not filename.startswith('.'):
                if filename.lower().endswith("-sample") or filename.lower() == "sample":
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

