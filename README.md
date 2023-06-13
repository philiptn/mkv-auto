# mkv-auto
A utility made in Python that aims to automatically remove any unwanted audio or subtitle tracks from Matroska (mkv) files, as well as converting/cleaning/resyncing any subtitles from the source video.

### Features
- Removes any audio or subtitle tracks in video file that does not match user preferences
- Converts any picture-based subtitles (BluRay/DVD) to SupRip (SRT) using Tesseract OCR
- Converts Advanced SubStation Alpha (ASS/SSA) subtitle files to SRT for maximizing compatibility
- Removes SDH (such as `[GUNSHOTS]` or `[LAUGHING]`) from SRT subtitles
- Resyncs subtitles to properly match the speech in audio using language-agnostic automatic synchronization (fast) or AI & machine learning (ai)

## Prerequisites
Most of the utility's functionality can be performed cross-platform as long as Python and the other packages is installed and available in PATH, however some features (such as DVD VobSub conversion) are only available when using Linux/WSL. Therefore, this utility mainly focuses its support on Linux-based operating systems.

### Linux (Ubuntu/Debian)

1. Run `./prerequisites.sh` to install and configure the necessary `apt` and `pip` packages needed for this utility to work.  

Note: Depending on your language preferences you may need to install additional tesseract language packs, modify script as needed.

## Usage
Note: `defaults.ini` contains the default options set for this utility. If you want to make any changes, create a new file named `user.ini` with all the same parameters to override the default settings.  

Tip! Save this repository on a fast storage medium (NVMe SSD is optimal), as the process of unpacking and repacking mkv files benefits greatly from high read/write performance.

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

smacke for ffsubsync (Resyncing subtitles to audio) [fast]  
https://github.com/smacke/ffsubsync  

oseiskar for autosubsync (Resyncing subtitles to audio) [ai]  
https://github.com/oseiskar/autosubsync