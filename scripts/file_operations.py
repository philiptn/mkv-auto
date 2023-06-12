import os
import shutil
import re


def copy_file(src, dst):
    shutil.copy2(src, dst)


def move_file(src, dst):
    shutil.move(src, dst)


def safe_delete_dir(directory_path):
    """Safely delete a directory, only if it is empty."""
    try:
        os.rmdir(directory_path)
    except OSError as e:
        print(f"Failed to remove directory {directory_path}. Error: {str(e)}")


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


def replace_tags(root_dir, replacement):
    tag_regex = re.compile(r"-\w*(-sample)?(\.\w{2,3})?$", re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # rename files
        for filename in filenames:
            base, ext = os.path.splitext(filename)
            if ext in {".mkv", ".srt"}:
                match = tag_regex.search(base)
                if match:
                    base = tag_regex.sub(replacement + (match.group(2) or ""), base)
                elif ext == ".mkv":
                    base += replacement
                os.rename(os.path.join(dirpath, filename), os.path.join(dirpath, base + ext))

        # rename directories
        for dirname in dirnames:
            base = os.path.join(dirpath, dirname)
            parent_dir = os.path.dirname(base)
            if parent_dir != root_dir:
                new_dirname = tag_regex.sub(replacement, dirname)
                os.rename(os.path.join(dirpath, dirname), os.path.join(dirpath, new_dirname))


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
                if filename.lower().endswith("-sample"):
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

                    os.rename(os.path.join(dirpath, file_name), os.path.join(dirpath, new_name))
