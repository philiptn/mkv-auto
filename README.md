# mkv-auto
A utility made in Python that aims to automatically remove any unwanted audio or subtitle tracks from Matroska (mkv) files. By using technology such as OCR, the utility will automatically convert any picture-based subtitles to SubRip/SRT to maximise playback compatibility.  

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

Thanks to NikseDK for SubtitleEdit (OCR)  
https://github.com/SubtitleEdit/subtitleedit/releases  
https://www.nikse.dk/subtitleedit/help#linux


