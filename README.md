# MKV-Auto
A fully automatic media processing tool that processes media files based on user preferences.

<img src="https://raw.githubusercontent.com/philiptn/mkv-auto/refs/heads/dev/resources/mkv-auto-demo.gif" width="540">

## Features
- Multithreaded file processing - uses up to 85% of available CPU and RAM by default
- Remove any audio or subtitle tracks from video that does not match user preferences (language, codec)
- Automatically download missing subtitles languages using [Subliminal](https://github.com/Diaoul/subliminal) (default enabled)
- Generate audio tracks in various codec and channel configurations (DTS, AAC, AC3, 5.1, 2.0 etc.)
- Convert any picture-based subtitles (BluRay/DVD) to SupRip (SRT) using SubtitleEdit and a custom Tesseract OCR library ([alex-p](https://launchpad.net/~alex-p/+archive/ubuntu/tesseract-ocr5))
- Convert Advanced SubStation Alpha (ASS) and MP4 (tx3g) subtitles to SRT using Python libraries and FFmpeg
- Remove SDH (such as `[PHONE RINGING]` or `*DOG GROWLING*`) from SRT subtitles (default enabled)
- Resynchronize any external or downloaded subtitles using FFsubsync to match the audio track in the media
- Unpack any `.rar` or `.zip` archives and convert `.mp4` or `.avi` files to MKV before processing the media
- Remove any hidden Closed Captions (CC) from the video stream using FFmpeg 
- Automatically categorize and rename media content (TV Show/Movie, SDR/HDR) based on filename and TVMAZE

### Even-Out-Sound (EOS)
<img src="https://raw.githubusercontent.com/philiptn/mkv-auto/refs/heads/dev/resources/meme.jpg" width="350">

Like many others I had become tired of badly mixed dialogue levels in modern media (yes, I do have a dedicated center channel), as well as loud sounds disrupting the movie experience. Sometimes this may be desirable, but in most cases I watch things at a moderate listening volume.

To fix this, I made a custom FFmpeg filter chain that does the following:
- Dynamic audio leveling
- Dialogue focused mixing (if surround audio is available)

Below is an illustration of the dynamic audio leveling filter, which slightly boosts moderate levels and brings down louder sounds.

<img src="https://raw.githubusercontent.com/philiptn/mkv-auto/refs/heads/dev/resources/eos_filter_curve.png" width="450">

Even-Out-Sound (EOS) is enabled by default, placed behind the original audio as the secondary audio track (AC3).

## Usage
It is recommended to save this repository on a path located on a fast storage medium (like NVMe SSD), as the process of unpacking and repacking mkv files benefits greatly from high read/write performance. Alternatively, point the `TEMP_DIR` variable (from `user.ini`) to a path that has fast disk IO (or even a ramdisk if you have the capacity).  

To ensure that the TEMP location does not run out space, MKV-Auto will calculate a buffer of 350% based on the files found in input and available space. During the process of making temp versions of media files, extracting audio+subtitle tracks, all these steps will consume storage space until they are inserted back to the MKV file. MKV-Auto may therefore skip some files from the input folder and process them on the next run. 

Note: `defaults.ini` contains the default options for this utility. If you want to make any changes, create a new file named `user.ini` with the parameters you want to override. The same applies for the Subliminal [config](https://github.com/Diaoul/subliminal/blob/main/docs/config.toml): `subliminal_defaults.toml` &rarr; `subliminal.toml`. For the Subliminal config, make sure to at least include all the original fields from `subliminal_defaults.toml`.  

## Ways to run MKV-Auto

- [Process files manually (Linux - Native Python)](#process-files-manually-linux---native-python)
- [Process files manually (Windows/Linux - Docker)](#process-files-manually-windowslinux---docker)
- [Process files automatically (Docker service - Linux)](#process-files-automatically-docker-service---linux)
- [Process files automatically from qBittorrent (Service + qBittorrent integration)](#process-files-automatically-from-qbittorrent-service--qbittorrent-integration)
- [Process files automatically on import (Service + Sonarr/Radarr integration)](#process-files-automatically-on-import-service--sonarrradarr-integration)

### Process files manually (Linux - Native Python)

To run MKV-Auto natively in Python, you need to be running Ubuntu 22.04 LTS.

1. Run `./prerequisites.sh` to install the necessary `apt` and `pip` packages needed for this utility to work.
2. Place the media files inside the `input/` folder. Alternatively, a custom input folder can be specified using `--input_folder` option. Enclosing the directories in double quotes (`--input_folder "folder/input media"`) is recommended to prevent any parsing errors. MKV-Auto will then copy all the files it can fit into `<mkv-auto folder>/.tmp/` unless a custom `TEMP_DIR` is specified. 
3. Activate the Python virtual environment using `source venv/bin/activate` or `. venv/bin/activate`.
4. Run the tool using `python3 mkv-auto.py`
5. Processed files can then be found in the output folder, categorized as either a movie or TV Show.
6. To exit the Python virtual environment, run `deactivate` in the terminal.

### Process files manually (Windows/Linux - Docker)

#### Windows
1. Make sure you have Docker for Windows installed. Docker Desktop can be found [here](https://www.docker.com/products/docker-desktop/).
2. Download the [repository](https://github.com/philiptn/mkv-auto/archive/refs/heads/main.zip) and save it to your computer. If you have a fast SSD with sufficient storage capacity, saving the repository to that drive is recommended.
3. Extract `mkv-auto-main.zip` to a folder and enter it.
4. Copy the media files you want to process inside the `input/` folder.
5. Double-click the `mkv-auto.bat` file to start MKV-Auto.
6. Check the `output/` folder for finished media files.

#### Linux
1. Make sure you have Docker Engine installed.
2. Run the script from the repository folder using `./mkv-auto.sh`. The scripts support the following options:  
    `--build` (build and run MKV-Auto using the local code, useful for debugging)  
    `--no-cache` (clear build cache when building the local Docker image)  
    `--copy` (copy files from input folder, not move them)

### Process files automatically (Docker service - Linux)

If you would like to run mkv-auto as a service, meaning that it simply checks a defined input folder for new files, then processes them automatically, this can be achieved with the mkv-auto-service.

- To get started, create a folder on your host system, such as `~/mkv-auto`.
- Next you need to copy `service/docker-compose.yml`, `service/.env_example`, `set_up_folders.sh`, `defaults.ini` and `subliminal_defaults.toml` over to this folder.
- Next, rename `defaults.ini` &rarr; `user.ini`, `subliminal_defaults.toml` &rarr; `subliminal.toml` and make the necessary modifications to suit your preferences. The `user.ini` and `subliminal.toml`files should then be placed inside a folder named `config`.  
- The `.env_example` file also needs to be renamed to `.env`. In here you need to change the `$HOST_FOLDER` variable to the location of the mkv-auto-service folder (`/home/philip/mkv-auto-service` in my case).
- The `$INPUT_FOLDER`, `$OUTPUT_FOLDER` and `$TEMP_DIR` variables in `.env` should be changed to the appropriate input, output and TEMP locations. If you have an NVMe SSD that is mounted to your system, and you have sufficient storage capacity, the tool will greatly benefit from the increased read/write speed if this drive is assigned to TEMP.
- To set up correct user permissions for the service, run `./set_up_folders.sh` as the current user. This will create all the necessary folders needed for the service.

The folder structure should look something like this: 
```text
/home/philip/mkv-auto-service/
├── .env
├── config
│   └── user.ini
│   └── subliminal.toml
├── docker-compose.yml
├── input
├── output
├── set_up_folders.sh
```

To start  mkv-auto-service, run the following command from the current folder:
````bash
docker compose up -d 
````

The service will now process any files from the input folder automatically and place them in the output folder.  

**NOTE: ALL files from the input folder will be MOVED to TEMP before starting, not copied. The program will automatically check that you have at least 350% of the total input files capacity available in TEMP before starting, and will dynamically limit the amount of files to be processed at once.**

To continuously monitor the progress of mkv-auto-service you can easily do this by adding this to your `~/.bash_aliases` file (create it if you do not already have it).  

````bash
# If you are logged in to the machine that is running the mkv-auto-service
# Press CTRL+C repeatedly to exit the view
alias mkvsl='docker logs mkv-auto-service && docker attach --sig-proxy=false mkv-auto-service'

# If you want to check the progress, but you are on another machine. Replace "ubuntu-desktop"
# with the machine/host that is running the mkv-auto-service container.
mkvsl() {
    ssh -t ubuntu-desktop 'docker logs mkv-auto-service && docker attach --sig-proxy=false mkv-auto-service'
}
````

To apply the changes to your `~/.bash_aliases` file, you can simply log in and out again, or refresh the environment by typing `source ~/.bashrc`. You can now continuously monitor the progress by running `mkvsl` in the terminal.

### Process files automatically from qBittorrent (Service + qBittorrent integration)

If you want mkv-auto-service to automatically process files from qBittorrent based on tags, this can be achieved by configuring the `qbittorrent-automation` docker service.

1. Make sure that you have enabled the Web User Interface on your qBittorrent client with a username and password.
2. Copy the `qbittorrent-automation` folder from `service/integrations/qbittorrent-automation` to a folder on your system.
3. Rename `.env_example` to `.env` and configure all the variables as needed.
4. Rename `example-docker-compose.yml` to `docker-compose.yml` and add all the necessary volume mounts.
5. (Optional) If the qBittorrent client is running on Windows, make a copy of `example-drive-mappings.txt` to `drive-mappings.txt` and change the necessary paths. Make sure to also set `TRANSLATE_WINDOWS_PATHS` to `true` in the `.env` file.
6. Start the `qbittorrent-automation` service by running `docker compose up -d`.

Any completed torrents tagged with `mkv-auto` will now be copied over to the mkv-auto-service input folder automatically. After this is done, the torrent tag will be updated with a checkmark `✔`.  

The service can be inspected by running `docker logs -f qbittorrent-automation` or by checking `automation.log` manually.

### Process files automatically on import (Service + Sonarr/Radarr integration)

If you are using Sonarr/Radarr and want MKV-Auto to process files automatically when files are imported to TV/Movies, this can be achieved by integration scripts.

Make sure that the media folder names in `user.ini` are named identically to your Sonarr/Radarr folders. If Radarr imports the media into folder `movies`, then `MOVIES_FOLDER` from `user.ini` also needs to be set to `movies`.

#### Radarr

1. Update the `RADARR_URL` in `user.ini` to the URL where you log in to Radarr.
2. Find your Radarr API key from `Settings` &rarr; `General` &rarr; `API Key` and add it to `user.ini`.
3. Add a new volume mount to the Radarr docker compose file that points to `/mkv-auto-input`:  
   `- /your/path/to/mkv-auto-service/input:/mkv-auto-input`
4. Copy the integration script from `service/integrations/radarr/send_to_mkv_auto.sh` into the Radarr `/config` folder. Make sure it is executable by running `chmod +x send_to_mkv_auto.sh`.
5. Navigate to `Settings` &rarr; `Connect` &rarr; `+` &rarr; `Custom Script` and fill out the following fields:  
   `Name: Send to MKV-Auto`  
   `Notification Triggers`  
   `☑ On File Import`  
   `☑ On File Upgrade`  
   `☑ On Movie Added`   
   `Path: /config/send-to-mkv-auto.sh`
6. Hit `Test` and then `Save` to save the custom script integration.

Any imported movies should now be grabbed by MKV-Auto, processed and then moved back to movies folder. If the movie name folder decided by MKV-Auto is different from the original, this will be updated automatically in Radarr using the API. 

#### Sonarr

1. Update the `SONARR_URL` in `user.ini` to the URL where you log in to Sonarr.
2. Find your SONARR API key from `Settings` &rarr; `General` &rarr; `API Key` and add it to `user.ini`.
3. Add a new volume mount to the Sonarr docker compose file that points to `/mkv-auto-input`:  
   `- /your/path/to/mkv-auto-service/input:/mkv-auto-input`
4. Copy the integration script from `service/integrations/sonarr/send_to_mkv_auto.sh` into the Sonarr `/config` folder. Make sure it is executable by running `chmod +x send_to_mkv_auto.sh`.
5. Navigate to `Settings` &rarr; `Connect` &rarr; `+` &rarr; `Custom Script` and fill out the following fields:  
   `Name: Send to MKV-Auto`  
   `Notification Triggers`  
   `☑ On File Import`  
   `☑ On File Upgrade`  
   `☑ On Series Add`   
   `Path: /config/send-to-mkv-auto.sh`
6. Hit `Test` and then `Save` to save the custom script integration.

Any imported TV shows should now be grabbed by MKV-Auto, processed and then moved back to TV shows folder. If the TV name folder decided by MKV-Auto is different from the original, this will be updated automatically in Sonarr using the API. 

## Acknowledgments

#### This project would not be possible without the following third-party tools/packages: 

FFmpeg (for converting audio/subtitle streams and general handling of video containers)  
https://ffmpeg.org/

MKVToolNix (for managing MKV files, extracting, merging, file info, etc.)  
https://mkvtoolnix.download/

Matt Lyon for subtitle-filter (SDH removal)  
https://github.com/m-lyon/filter-subs

NikseDK for SubtitleEdit (OCR of BluRay/DVD subtitles)  
https://github.com/SubtitleEdit/subtitleedit/releases  
https://www.nikse.dk/subtitleedit/help#linux

Tesseract OCR-5 from Alexander Pozdnyakov  
https://launchpad.net/~alex-p/+archive/ubuntu/tesseract-ocr5  

qqq1243 for asstosrt (SSA/ASS to SRT conversion)  
https://github.com/sorz/asstosrt/

smacke for ffsubsync (Resyncing subtitles to audio)  
https://github.com/smacke/ffsubsync  

jeanb for pysrt (Removing all-uppercase letters in improperly formatted SDH subtitles)  
https://github.com/byroot/pysrt

Marko Kreen for rarfile (Unpacking `.rar` and `.zip` archives in Python)  
https://github.com/markokr/rarfile

Christian Theune and Nate Schimoller for pycountry (converting ISO two-letter language codes to country names)  
https://github.com/pycountry/pycountry  

Diaoul and ratoaq2 for subliminal (automatic downloading of subtitles)  
https://github.com/Diaoul/subliminal

Giampaolo Rodola and billiejoex for psutil (calculating max OCR threads based on available system RAM)  
https://github.com/giampaolo/psutil

thombashi for pathvalidate (sanitizing filenames)  
https://github.com/thombashi/pathvalidate

seatgeek for thefuzz (fuzzy matching titles)  
https://github.com/seatgeek/thefuzz  

#### [Meme image source](https://www.facebook.com/photo.php?fbid=502535002295094)

## Donations
This tool is completely open source and free to use, but if you want to support me and my work you can buy me a coffee using the link below:

<a href="https://www.buymeacoffee.com/philiptn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=philiptn&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff?" /></a>

You can also become a sponsor by pressing the heart button next to my profile or this repository.