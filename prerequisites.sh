#!/usr/bin/env bash

START_DIR=$(pwd)

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi

# Updating apt
$SUDO apt-get update

# Download old ffmpeg version containing DTS encoder (dca) and compile it
$SUDO apt-get install wget build-essential yasm -y
$SUDO mkdir -p /.mkv-auto/ffmpeg-3.1.11
cd /.mkv-auto/ffmpeg-3.1.11
$SUDO wget https://ffmpeg.org/releases/ffmpeg-3.1.11.tar.gz
$SUDO tar -xzf ffmpeg-3.1.11.tar.gz
cd ffmpeg-3.1.11
$SUDO ./configure
# Compile ffmpeg (this may take a while)
$SUDO make 2>/dev/null
# Return to the starting directory
cd "$START_DIR"

# Installing python3.10
$SUDO apt-get install python3.10 -y

# Installing and updating python3-pip
$SUDO apt-get install python3-pip -y
python3 -m pip install --user --upgrade pip

# Installing python3 venv
python3 -m pip install --user virtualenv
$SUDO apt install python3.10-venv -y

# Installing MKVToolNix
$SUDO apt-get install mkvtoolnix -y

# Installing flatpak and HandBrakeCLI (via flatpak)
$SUDO apt-get install flatpak -y
$SUDO flatpak install app/fr.handbrake.ghb/x86_64/stable -y

# Needed for unpacking archives, is most likely already installed
$SUDO apt-get install unrar -y

# Install tzdata and set timezone to UTC
DEBIAN_FRONTEND=noninteractive apt-get install tzdata -y

# Installing packages required for SubtitleEdit (in 'utlities/') to work,
# as well as autosubsync and other packages
$SUDO apt-get install mono-complete libhunspell-dev libmpv-dev tesseract-ocr \
  vlc ffmpeg libgtk2.0-0 libsndfile1 libcanberra-gtk-module git xvfb -y

# Installing tesseract-ocr (for use with SubtitleEdit)
# Reference: https://pypi.org/project/pgsrip/
$SUDO apt-get install software-properties-common -y
$SUDO add-apt-repository ppa:alex-p/tesseract-ocr5 -y
$SUDO apt-get update
# Additional language packs need to be installed manually!
# To list the available language packs run `sudo apt install tesseract-ocr-lang`
# or just add the ISO-639-2/B (3-letter) format to the end of "tesseract-ocr-<LANG>"
$SUDO apt-get install tesseract-ocr \
  tesseract-ocr-nor -y

# DEPRECATED due to pgsrip no longer being used
# Installing training data for tesseract (tessdata) (note: large datasets, may take a while to download)
# Reference: https://pypi.org/project/pgsrip/
#mkdir -p ~/.mkv-auto
#git clone https://github.com/tesseract-ocr/tessdata_best.git ~/.mkv-auto/tessdata

# Create a Python virtual environment (venv) and activate it
python3 -m venv venv
source venv/bin/activate

# Install required PyPI packages using Pip
pip3 install --upgrade pip
pip3 install -r requirements.txt