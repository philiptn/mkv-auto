import argparse
import subprocess
import os


def convert_path(win_path):
    # Remove any single quotes
    win_path = win_path.replace("'", "")

    # List of valid file extensions
    valid_extensions = ['.mkv', '.avi', '.mp4']

    # Check if the path is a file or folder
    if any(win_path.endswith(ext) for ext in valid_extensions):
        is_directory = False
    else:
        is_directory = True

    # Mapping of Windows drive letters to Linux paths
    drive_mapping = {
        'Z:': '/media/share',
        'Y:': '/media/single',
        'X:': '/media/vault'
    }

    # Check if the path starts with a mapped drive letter
    for drive_letter, linux_path in drive_mapping.items():
        if win_path.startswith(drive_letter):
            win_path = win_path.replace(drive_letter, linux_path, 1)

    # Replace backslashes with forward slashes
    final_path = win_path.replace('\\', '/')

    if not final_path.endswith('/') and is_directory:
        final_path = f"{final_path}/"

    return final_path, is_directory


def process_file(file_path):
    mkv_auto_folder_path = '/media/philip/nvme/mkv-auto/'

    lock_file_path = file_path + '.lock'

    # Check for the existence of the lock file
    if os.path.exists(lock_file_path):
        return

    # Create the lock file
    with open(lock_file_path, 'w') as lock_file:
        lock_file.write('LOCKED')

    # Read the file the first time
    with open(file_path, 'r') as file:
        lines = file.readlines()

    if not lines:
        os.remove(lock_file_path)  # Remove the lock file
        return

    first_line = lines[0].strip()
    tag, path = [item.strip().strip("'") for item in first_line.split(',', 1)]

    linux_path, is_directory = convert_path(path)

    # If tag is 'Plex', copy files and process using mkv-auto
    if tag == 'Plex':
        # If the path is directly to a file, copy the file to mkv-auto input folder
        try:
            subprocess.run(["cp", "-r", linux_path, f'{mkv_auto_folder_path}input/'])
        except Exception as e:
            print(f"\n[SERVICE] An error occurred while executing the command: {e}")
            os.remove(lock_file_path)  # Remove the lock file
            return

    # Read the file again
    with open(file_path, 'r') as file:
        new_lines = file.readlines()

    # Take all lines except the first one from the second read
    updated_lines = new_lines[1:]

    # Overwrite the file with the updated lines
    with open(file_path, 'w') as file:
        file.writelines(updated_lines)

    os.remove(lock_file_path)  # Remove the lock file


def main():
    parser = argparse.ArgumentParser(
        description="A service for mkv-auto that can parse input paths from a queue text file")
    parser.add_argument("--file_path", dest="file_path", type=str,
                        help="The path to the text file containing the input paths")
    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"\n[SERVICE] File {args.file_path} not found!")
        return

    process_file(args.file_path)


if __name__ == "__main__":
    main()
