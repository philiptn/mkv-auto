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
    win_path = win_path.replace('\\', '/')

    return win_path, is_directory


def process_file(file_path, output_folder):
    mkv_auto_folder_path = '/media/philip/nvme/mkv-auto/'
    ready_for_final_processing_path = '/media/share/mkv-auto-queue/files/'

    lock_file_path = file_path + '.lock'
    ready_for_nvenc_folder_path = None

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
    tag, path = [item.strip().strip("'") for item in first_line.split(',')]

    linux_path, is_directory = convert_path(path)

    # If tag is 'Plex', copy files and process using mkv-auto
    if tag == 'Plex':
        if not is_directory:
            # If the path is directly to a file, copy the file to mkv-auto input folder
            try:
                subprocess.run(["cp", linux_path, f'{mkv_auto_folder_path}input/'])
            except Exception as e:
                print(f"\n[SERVICE] An error occurred while executing the command: {e}")

            command = ["venv/bin/python3", "-u", "mkv-auto.py", "--output_folder",
                       output_folder, "--silent", "--notemp"]
        else:
            command = ["venv/bin/python3", "-u", "mkv-auto.py", "--output_folder",
                       output_folder, "--silent", "--input_folder", linux_path]

        try:
            subprocess.run(command, cwd=mkv_auto_folder_path)
        except Exception as e:
            print(f"\n[SERVICE] An error occurred while executing the command: {e}")
            os.remove(lock_file_path)  # Remove the lock file
            return

    # If tag contains 'NVEnc', copy files to queue path
    elif "NVEnc" in tag:
        if 'NVEnc18' in tag:
            ready_for_nvenc_folder_path = '/media/share/mkv-auto-queue/nvenc_queue/quality_18'
        elif 'NVEnc30' in tag:
            ready_for_nvenc_folder_path = '/media/share/mkv-auto-queue/nvenc_queue/quality_30'

        command = ["cp", "-r", linux_path, ready_for_nvenc_folder_path]

        # Execute the command
        try:
            subprocess.run(command, cwd=mkv_auto_folder_path)
        except Exception as e:
            print(f"\n[SERVICE] An error occurred while executing the command: {e}")
            os.remove(lock_file_path)  # Remove the lock file
            return

    # If tag is 'Ready', process using mkv-auto with no temp-copy
    elif tag == 'Ready':
        # Command template
        command = ["venv/bin/python3", "-u", "mkv-auto.py", "--output_folder",
                   output_folder, "--silent", "--notemp", "--input_folder",
                   ready_for_final_processing_path]

        # Execute the command
        try:
            subprocess.run(command, cwd=mkv_auto_folder_path)
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
    parser.add_argument("--output_folder", dest="output_folder", type=str,
                        help="The output folder path used by mkv-auto to save its files")
    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"\n[SERVICE] File {args.file_path} not found!")
        return

    process_file(args.file_path, args.output_folder)


if __name__ == "__main__":
    main()
