import os
import shutil
import time


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
