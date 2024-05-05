# mkv-auto
A tool that aims to remove unnecessary clutter from Matroska (.mkv) files by  
removing and/or converting any audio or subtitle tracks from the source video.

***Note***: If you are running Windows and just want to try the program, go [here](https://github.com/philiptn/mkv-auto?tab=readme-ov-file#windows).

### Features
- Removes any audio or subtitle tracks from video that does not match user preferences
- Generates audio tracks in preferred codec (DTS, AAC, AC3 etc.) if not already present in the media (ffmpeg)
- Converts any picture-based subtitles (BluRay/DVD) to SupRip (SRT) using SubtitleEdit and Tesseract OCR
- Converts Advanced SubStation Alpha (ASS/SSA) and MP4 (tx3g) subtitles to SRT using Python libraries and ffmpeg
- Removes SDH (such as `[MAN COUGHING]` or `[DISTANT CHATTER]`) from SRT subtitles (default enabled)
- Resynchronizes subtitles to match the audio track of the video using ffsubsync (best effort)
- Unpacks any `.rar` or `.zip` archives and converts `.mp4` or `.avi` files to MKV before processing the media
- Remove any hidden Closed Captions (CC) from the video stream using ffmpeg 
- Automatically categorize the media content type (TV Show/Movie, SDR/HDR) based on info in filename

### Processing example
<img src="https://github.com/philiptn/mkv-auto/blob/main/utilities/mkv-auto_output.png?raw=true" width="600">

## Prerequisites

### Linux (Ubuntu/Debian)

1. Run `./prerequisites.sh` to install the necessary `apt` and `pip` packages needed for this utility to work.

## Usage
Note: `defaults.ini` contains the default options set for this utility. If you want to make any changes, create a new file named `user.ini` with all the same parameters to override the default settings.  

Tip! Save this repository on a fast storage medium (NVMe SSD is optimal), as the process of unpacking and repacking mkv files benefits greatly from high read/write performance. Alternatively, point the `TEMP_DIR` variable (from `user.ini`) to a fast storage medium.

1. Place the mkv files inside the `input/` folder (files inside folders are also supported). Alternatively, a custom input folder can be specified using `--input_folder` option. Enclosing the directories in double quotes (`--input_folder "folder/input media"`) is recommended to prevent any parsing errors. The utility will then copy all the files to `<mkv-auto folder>/.tmp/` unless a custom `TEMP_DIR` is specified. 
2. Activate the Python virtual environment using `source venv/bin/activate` or `. venv/bin/activate`.
3. Run the utility using `python3 mkv-auto.py`
4. Processed files can then be found in the output folder, categorized as either a movie (`output/Movies/movie.mkv`) or TV Show (`output/TV Shows/tv show name/tv.show.name.S01E01.mkv`).
5. To exit the Python virtual environment, run `deactivate` in the terminal.

## Docker

### mkv-auto-service

If you would like to run mkv-auto as a service, meaning that it simply checks a defined input folder for new files, then processes them automatically, this can be achieved with the mkv-auto-service.

- To get started, create a folder on your host system, such as `~/mkv-auto-service`.
- Next you need to copy `docker-compose.yml`, `.env_example` and `defaults.ini` over to this folder.
- Next, rename the `defaults.ini` to `user.ini` and make the necessary modifications to suit your preferences. This file also needs to be placed inside a folder named `config`. 
- The `.env_example` file also needs to be renamed to `.env`. In here you need to change the `$HOST_FOLDER` variable to the location of the mkv-auto-service folder (`/home/philip/mkv-auto-service` in my case).
- The `$INPUT_FOLDER`, `$OUTPUT_FOLDER` and `$TEMP_DIR` variables in `.env` should be changed to the appropriate input, output and TEMP locations. If you have an NVMe SSD that is mounted to your system, and you have sufficient storage capacity, the tool will greatly benefit from the increased read/write speed if this drive is assigned to TEMP.

The folder structure should look something like this: 
```text
/home/philip/mkv-auto-service/
├── .env
├── config
│   └── user.ini
├── docker-compose.yml
├── input
├── output
```

To start the mkv-auto-service, run the following command from the mkv-auto-service folder:
````bash
sudo docker compose up -d 
````

The service will now process any files from the input folder automatically and place them in the output folder.  

**NOTE: ALL files from the input folder will be MOVED to TEMP before starting, not copied. If you are processing many large files at once, make sure that you have enough TEMP storage capacity.**

To see the progress/logs of the service, this can be viewed using `sudo docker logs mkv-auto-service` or by inspecting the `mkv-auto-service/logs/mkv-auto.log` file manually.

Tip: If you want to continuously monitor the progress of mkv-auto-service you can easily do this by adding this to your `~/.bash_aliases` file (create it if you do not already have it).  

````bash
# If you are logged in to the machine that is running the mkv-auto-service
alias mkv-auto-logs='watch --color -n 1 "docker logs --tail 30 mkv-auto-service"'

# If you want to check the progress, but you are on another machine. Replace "ubuntu-desktop"
# with the machine/host that is running the mkv-auto-service container.
mkv-auto-logs() {
    ssh -t ubuntu-desktop 'watch --color -n 1 "docker logs --tail 30 mkv-auto-service"'
}
````

To apply the changes to your `~/.bash_aliases` file, you can simply log in and out again, or refresh the environment by typing `source ~/.bashrc`. You can now continuously monitor the progress by running `mkv-auto-logs` in the terminal.


### mkv-auto (standalone)
 
To run the utility like a program using Docker, this can be done by using one of the built-in scripts.

#### Windows
1. Make sure you have Docker for Windows installed. Docker Desktop can be found [here](https://www.docker.com/products/docker-desktop/).
2. Download the [repository](https://github.com/philiptn/mkv-auto/archive/refs/heads/main.zip) and save it to your computer. If you have a fast SSD with sufficient storage capacity, saving the repository to that drive is recommended.
3. Extract `mkv-auto-main.zip` to a folder and enter it.
4. Copy the media files you want to process inside the `input/` folder.
5. Double-click the `mkv-auto.bat` file to start mkv-auto.
6. Check the `output/` folder for the finished files.

If you would like to change the default behaviour of mkv-auto, make a copy of `defaults.ini` and rename it to `user.ini`. Adjust the settings to match your preferences.


#### Linux
1. Make sure you have Docker Engine installed.
2. Run the `mkv-auto.sh` script from the repository folder. 

If you prefer to run mkv-auto in the console manually, some Docker run examples can be found below:

```text
# Linux
sudo docker run --rm -it -v "$(pwd)":/mkv-auto/files philiptn/mkv-auto --docker --move
# Command Prompt (CMD)
docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto --docker --move
# PowerShell
docker run --rm -it -v ${PWD}:/mkv-auto/files philiptn/mkv-auto --docker --move
```

**Note**: Everything up to `... mkv-auto` in the command above is Docker specific, while `--docker ...` is the arguments forwarded to the mkv-auto utility.

## To-do
- [ ] Clean up functions and use more sensible data-types such as classes, structs etc.
- [ ] Process multiple files at once - full multithreading support
- [ ] Add support for video encoding workers - Handbrake/NVEncc/ffmpeg
- [ ] (maybe) add support for automatically fetching missing subtitles

## CLI
### mkv-auto

```
usage: mkv-auto.py [-h] [--input_folder INPUT_DIR]
                   [--output_folder OUTPUT_DIR] [--temp_folder TEMP_DIR]
                   [--silent] [--move] [--docker] [--debug]

A tool that aims to remove unnecessary clutter from Matroska (.mkv) files by
removing and/or converting any audio or subtitle tracks from the source
video.

options:
  -h, --help            show this help message and exit
  --input_folder INPUT_DIR, -if INPUT_DIR
                        input folder path (default: 'input/')
  --output_folder OUTPUT_DIR, -of OUTPUT_DIR
                        output folder path (default: 'output/')
  --temp_folder TEMP_DIR, -tf TEMP_DIR
                        temp folder path (default: '.tmp/')
  --silent              supress visual elements like progress bars (default:
                        False)
  --move                process files directly by moving them, no copying
                        (default: False)
  --docker              use docker-specific default directories from 'files/'
                        (default: False)
  --debug               print debugging information such as track selection,
                        codecs, prefs etc. (default: False)
  --service             disables debug pause if enabled (default: False)
```

### queue-service

```
usage: queue-service.py [-h] [--file_path FILE_PATH] [--output_folder OUTPUT_FOLDER]

A service for mkv-auto that can parse input paths from a queue text file

options:
  -h, --help            show this help message and exit
  --file_path FILE_PATH
                        The path to the text file containing the input paths
  --output_folder OUTPUT_FOLDER
                        The output folder path used by mkv-auto to save its files
```

## Acknowledgments

#### This project would not be possible without the following third-party tools/packages: 

ffmpeg (for converting audio/subtitle streams and general handling of video containers)  
https://ffmpeg.org/

MKVToolNix (for managing MKV files, extracting, merging, file info, etc.)  
https://mkvtoolnix.download/

Matt Lyon for subtitle-filter (SDH removal)  
https://github.com/m-lyon/filter-subs

NikseDK for SubtitleEdit (OCR of BluRay/DVD subtitles)  
https://github.com/SubtitleEdit/subtitleedit/releases  
https://www.nikse.dk/subtitleedit/help#linux

Tesseract OCR (an Optical Character Recognition (OCR) engine used for converting subtitles within SubtitleEdit)  
https://github.com/tesseract-ocr/tesseract

qqq1243 for asstosrt (SSA/ASS to SRT conversion)  
https://github.com/sorz/asstosrt/

smacke for ffsubsync (Resyncing subtitles to audio)  
https://github.com/smacke/ffsubsync  

jeanb for pysrt (Removing all-uppercase letters in improperly formatted SDH subtitles)  
https://github.com/byroot/pysrt

Marko Kreen for rarfile (Unpacking `.rar` and `.zip` archives in Python)  
https://github.com/markokr/rarfile

Michal Mimino Danilak for langdetect (detecting the subtitle language of SRT files)  
https://github.com/Mimino666/langdetect  

Christian Theune and Nate Schimoller for pycountry (converting ISO two-letter language codes to country names)  
https://github.com/pycountry/pycountry  

## Donations
This utility is completely free to use, but if you want to support me and my work you can buy me a coffee using the link below:  

<a href="https://www.buymeacoffee.com/philiptn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=philiptn&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff?" /></a>
