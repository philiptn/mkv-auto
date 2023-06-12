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
    level_1_dirs = [d.path for d in os.scandir(root_dir) if d.is_dir()]

    # Move files from subdirectories to level 1 directories
    for level_1_dir in level_1_dirs:
        for dirpath, dirnames, filenames in os.walk(level_1_dir):
            for filename in filenames:
                if filename.endswith(('.mkv', '.srt')):
                    new_path = os.path.join(level_1_dir, filename)
                    if not os.path.exists(new_path):
                        shutil.move(os.path.join(dirpath, filename), new_path)

    # Remove subdirectories
    for level_1_dir in level_1_dirs:
        for dirpath, dirnames, filenames in os.walk(level_1_dir, topdown=False):
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
