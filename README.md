# mkv-auto
A utility made in Python that aims to automatically remove any unwanted audio or subtitle tracks from Matroska (mkv) files. By using technology such as OCR, the utility will automatically convert any picture-based subtitles to SubRip/SRT to maximise playback compatibility. If the subtitles are not properly synced to the audio of the video file, this can also be done automatically using AI & machine learning (at the cost of processing time).

### Features
- Removes any audio or subtitle tracks in video file that does not match user preferences
- Converts any picture-based subtitles (BluRay/DVD) to SupRip (SRT) using Tesseract OCR
- Converts Advanced SubStation Alpha (ASS/SSA) subtitle files to SRT for maximizing compatibility
- Removes SDH (such as `[GUNSHOTS]` or `[LAUGHING]`) from SRT subtitles
- Resync subtitles to properly match the speech in audio using AI & machine learning

## Prerequisites
Most of the utility's functionality can be performed cross-platform as long as Python and the other packages is installed and available in PATH, however some features (such as DVD VobSub conversion) are only available when using Linux/WSL. Therefore, this utility mainly focuses its support on Linux-based operating systems.

### Linux (Ubuntu/Debian)

1. Run `./prerequisites.sh` to install and configure the necessary `apt` and `pip` packages needed for this utility to work.  

Note: Depending on your language preferences you may need to install additional tesseract language packs, modify script as needed.

## Usage
Note: `defaults.ini` contains the default options set for this utility. If you want to make any changes, create a new file named `user.ini` with all the same parameters to override the default settings.

1. Place the mkv files inside the `input/` folder (files inside folders are also supported).
2. Activate the Python virtual environment using `source venv/bin/activate`
3. Run the utility using `python3 mkv-auto.py`.
4. Processed files can now be found in the `output/` folder.
5. Exit the Python virtual environment by running `deactivate`.

####  (WARNING: This utility will destructively alter any files placed in the `input/` folder, proceed at own risk!)

### Acknowledgments

Thanks to: 

Matt Lyon for subtitle-filter (SDH removal)  
https://github.com/m-lyon/filter-subs

ratoaq2 for PGSRip (OCR of BluRay subtitles)  
https://github.com/ratoaq2/pgsrip

NikseDK for SubtitleEdit (OCR of DVD subtitles)  
https://github.com/SubtitleEdit/subtitleedit/releases  
https://www.nikse.dk/subtitleedit/help#linux

qqq1243 for asstosrt (SSA/ASS to SRT conversion)  
https://github.com/sorz/asstosrt/

oseiskar for autosubsync (Resyncing subtitles to audio using AI & machine learning)  
https://github.com/oseiskar/autosubsync