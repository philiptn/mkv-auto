## ***Now supports full multithreading! Up to 56% faster processing!****
##### * Tested on a dataset of 10 episodes, generating audio and OCR for each episode. v1.0.3 and v2.0 tested. 11 CPU threads.

**NOTE**: This version has new changes to both `docker-compose.yml`, `.env_example` and `defaults.ini`. Make sure to update your own compose, `.env` and `user.ini` files before upgrading to the newest version.

To update to the latest version of mkv-auto, run `docker pull philiptn/mkv-auto`.

### New options in `defaults.ini`:
- `MAX_CPU_USAGE`
- `DOWNLOAD_MISSING_SUBS`
- `REMOVE_ALL_SUBTITLES`
- `KEEP_ORIGINAL_SUBTITLES`
- `FORCED_SUBTITLES_PRIORITY`
- `REDO_CASING`

### Other features:
- Added support for automatically downloading missing subtitles using Subliminal
- Added media overview print of detected files (Movie, TV Show etc.)
- Dynamic copying/moving of files based on available storage capacity in TEMP (checks for at least 150% available)

### Changes and bug fixes:
- Changed license from LGPL-2.1 to GPL-3.0 to comply with imported libraries
- Subtitle tracks marked with `"non-{language} Dialogue"` will now be set to `forced=True` in subtitle metadata if `FORCED_SUBTITLES_PRIORITY` is set to `"first"`
- Added `/RedoCasing` parameter for SubtitleEdit to fix all uppercase subtitles
- Moving files are now performed using `shutil.move()` instead of copying, then deleting the files
- Added new find/replace entries
- Changed FFsubsync process to check existing subtitles first before synchronizing to audio (default FFsubsync behavior)
- Generated subtitles (OCR) and forced subtitles will be skipped in the FFsubsync process due to syncing errors
- Fixed `find_available_display()` function, as previous version did not find xvfb displays properly
- Implemented logging using `logging` library to properly log to files and handle stdout
- Other various bug fixes and improvements