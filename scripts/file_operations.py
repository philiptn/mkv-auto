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
    # Regex pattern to match tags (starting with "-" followed by any letters,
    # possibly followed by "-sample", before file extension if exists), case-insensitive
    pattern = re.compile(r"-\w*((-sample)?(?=\.\w+$|$))", re.IGNORECASE)

    # Walk through all directories and files recursively, bottom-up
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        # Process files
        for name in filenames:
            # Check if a tag exists
            if pattern.search(name):
                new_name = pattern.sub(lambda m: replacement + (m.group(2) if m.group(2) else ""), name)
            elif name.endswith('.mkv'):  # No tag exists, so add one at the end of the name (before the extension)
                base_name, ext = os.path.splitext(name)
                new_name = base_name + replacement + ext
            else:  # Skip non-mkv files that don't have a tag
                continue

            if new_name != name:
                try:
                    shutil.move(os.path.join(dirpath, name), os.path.join(dirpath, new_name))
                except Exception as e:
                    print(f"Unable to process file: {os.path.join(dirpath, name)}")
                    print(f"Error: {e}")
        # Process directories
        for name in dirnames:
            # Check if a tag exists
            if pattern.search(name):
                new_name = pattern.sub(lambda m: replacement + (m.group(2) if m.group(2) else ""), name)
            else:  # No tag exists, so add one at the end of the name
                new_name = name + replacement

            if new_name != name:
                try:
                    shutil.move(os.path.join(dirpath, name), os.path.join(dirpath, new_name))
                except Exception as e:
                    print(f"Unable to process directory: {os.path.join(dirpath, name)}")
                    print(f"Error: {e}")
