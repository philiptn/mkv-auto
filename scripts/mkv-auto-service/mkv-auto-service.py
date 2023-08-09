import argparse
import subprocess
from shutil import copyfile
import os


def convert_path(win_path):

    # Remove any single quotes
    win_path = win_path.replace("'", "")

    # Mapping of Windows drive letters to Linux paths
    drive_mapping = {
        'Z:': '/media/share',
        'Y:': '/media/single',
        'X:': '/media/vault'
    }

    # Check if the path starts with a mapped drive letter
    for drive_letter, linux_path in drive_mapping.items():
        if win_path.startswith(drive_letter):
            # Replace drive letter and backslashes with the Linux path and forward slashes
            return win_path.replace(drive_letter, linux_path).replace('\\', '/')

    # If no mapped drive letter found, just replace backslashes with forward slashes
    return win_path.replace('\\', '/')


def process_file(file_path, command_template, mkv_auto_folder_path, tag_to_check):
    lock_file_path = file_path + '.lock'
    
    # Check for the existence of the lock file
    if os.path.exists(lock_file_path):
        print("\n[SERVICE] Another instance is running. Exiting.\n")
        return
    
    # Create the lock file
    with open(lock_file_path, 'w') as lock_file:
        lock_file.write('LOCKED')

    # Read the file the first time
    with open(file_path, 'r') as file:
        lines = file.readlines()

    if not lines:
        print("\n[SERVICE] File is empty. Nothing to process.\n")
        os.remove(lock_file_path) # Remove the lock file
        return

    first_line = lines[0].strip()
    tag, path = [item.strip().strip("'") for item in first_line.split(',')]

    # Check if the tag matches
    if tag == tag_to_check:
        linux_folder_path = convert_path(path)

        # Build the command
        command = command_template + [linux_folder_path]

        # Execute the command
        try:
            subprocess.run(command, cwd=mkv_auto_folder_path)
        except Exception as e:
            print(f"\n[SERVICE] An error occurred while executing the command: {e}")
            os.remove(lock_file_path) # Remove the lock file
            return

    # Read the file again
    with open(file_path, 'r') as file:
        new_lines = file.readlines()

    # Take all lines except the first one from the second read
    updated_lines = new_lines[1:]

    # Overwrite the file with the updated lines
    with open(file_path, 'w') as file:
        file.writelines(updated_lines)

    os.remove(lock_file_path) # Remove the lock file


def main():
    parser = argparse.ArgumentParser(description="A service for mkv-auto that can parse input folder paths from a text file")
    parser.add_argument("--file_path", dest="file_path", type=str, help="The path to the text file containing the input folder paths")
    parser.add_argument("--output_folder", dest="output_folder", type=str, help="The output folder path used by mkv-auto to save its files")
    args = parser.parse_args()

    output_folder = args.output_folder
    tag_to_check = 'Plex'

    mkv_auto_folder_path = '/media/philip/nvme/mkv-auto-dev/'

    # Command template
    command_template = ["venv/bin/python3", "mkv-auto.py", "--output_folder", 
                        output_folder, "--input_folder"]

    if not os.path.exists(args.file_path):
        print(f"\n[SERVICE] File {args.file_path} not found!")
        return

    process_file(args.file_path, command_template, mkv_auto_folder_path, tag_to_check)


if __name__ == "__main__":
    main()
