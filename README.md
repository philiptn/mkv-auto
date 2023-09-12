# mkv-auto
A utility made in Python that aims to automatically remove any unwanted audio or subtitle tracks from Matroska (mkv) files, as well as converting/cleaning/resyncing any subtitles from the source video.

### Features
- Removes any audio or subtitle tracks in video file that does not match user preferences
- Converts any picture-based subtitles (BluRay/DVD) to SupRip (SRT) using Tesseract OCR
- Converts Advanced SubStation Alpha (ASS/SSA) subtitle files to SRT for maximizing compatibility
- Removes SDH (such as `[GUNSHOTS]` or `[LAUGHING]`) from SRT subtitles
- Resyncs subtitles to properly match the speech in audio using language-agnostic automatic synchronization (fast) or AI & machine learning (ai)
- Unpacks any `.rar` or `.zip` archives and converts `.mp4` or `.avi` files to MKV before processing the media
- Remove hidden CC (Closed Captions) from the video stream portion of the MKV container using ffmpeg 
- Automatically categorize the media content type (TV Show/Movie) based on info in filename

## Prerequisites
Most of the utility's functionality can be performed cross-platform as long as Python and the other packages is installed and available in PATH, however some features (such as DVD VobSub conversion) are only available when using Linux/WSL. Therefore, this utility mainly focuses its support on Linux-based operating systems.

### Linux (Ubuntu/Debian)

1. Run `./prerequisites.sh` to install and configure the necessary `apt` and `pip` packages needed for this utility to work.  

Note: Depending on your language preferences you may need to install additional tesseract language packs, modify script as needed.

## Usage
Note: `defaults.ini` contains the default options set for this utility. If you want to make any changes, create a new file named `user.ini` with all the same parameters to override the default settings.  

Tip! Save this repository on a fast storage medium (NVMe SSD is optimal), as the process of unpacking and repacking mkv files benefits greatly from high read/write performance. Alternatively, point the `TEMP_DIR` variable (from `user.ini`) to a fast storage medium.

1. Place the mkv files inside the `input/` folder (files inside folders are also supported). Alternatively, a custom input folder can be specified using `--input_folder` option. The utility will then copy all the files to `<mkv-auto folder>/.tmp/` unless another `TEMP_DIR` is specified. 
2. Activate the Python virtual environment using `source venv/bin/activate`
3. Run the utility using `python3 mkv-auto.py` (this will pull the files from `input/` and place them in `output/`). To use other input and output folders, use the `--input_folder` and `--output_folder` options. Enclosing the directories in double quotes (`--input_folder "folder/input media"`) is recommended to prevent any parsing errors. 
4. Processed files can then be found in the output folder, categorized as either a movie (`output/Movies/movie.mkv`) or TV Show (`output/TV Shows/tv show name/tv.show.name.S01E01.mkv`).
5. To exit the Python virtual environment, run `deactivate` in the terminal.

## Docker
`docker build -t mkv-auto .`
`docker run --rm -it -v /mnt/d/mkv-auto-docker:/mkv-auto/files mkv-auto --help`

## CLI
### mkv-auto

```
usage: mkv-auto.py [-h] [--input_folder INPUT_DIR] [--output_folder OUTPUT_DIR] [--silent]

A tool that aims to remove necessary clutter from Matroska (.mkv) files by removing and/or converting any subtitle tracks in the source file(s).

options:
  -h, --help            show this help message and exit
  --input_folder INPUT_DIR, -if INPUT_DIR
                        input folder path (default: 'input/')
  --output_folder OUTPUT_DIR, -of OUTPUT_DIR
                        output folder path (default: 'output/')
  --silent              supress visual elements like progress bars (default: False)
```

### mkv-auto-service

```
usage: mkv-auto-service.py [-h] [--file_path FILE_PATH] [--output_folder OUTPUT_FOLDER]

A service for mkv-auto that can parse input folder paths from a text file

options:
  -h, --help            show this help message and exit
  --file_path FILE_PATH
                        The path to the text file containing the input folder paths
  --output_folder OUTPUT_FOLDER
                        The output folder path used by mkv-auto to save its files
```

## PyPI Acknowledgments

#### Thanks to: 

Matt Lyon for subtitle-filter (SDH removal)  
https://github.com/m-lyon/filter-subs

NikseDK for SubtitleEdit (OCR of BluRay/DVD subtitles)  
https://github.com/SubtitleEdit/subtitleedit/releases  
https://www.nikse.dk/subtitleedit/help#linux

qqq1243 for asstosrt (SSA/ASS to SRT conversion)  
https://github.com/sorz/asstosrt/

smacke for ffsubsync (Resyncing subtitles to audio) [fast]  
https://github.com/smacke/ffsubsync  

oseiskar for autosubsync (Resyncing subtitles to audio) [ai]  
https://github.com/oseiskar/autosubsync

jeanb for pysrt (Removing all-uppercase letters in improperly formatted SDH subtitles)  
https://github.com/byroot/pysrt

Marko Kreen for rarfile (Unpacking `.rar` and `.zip` archives in Python)  
https://github.com/markokr/rarfile