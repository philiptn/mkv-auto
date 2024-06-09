#!/usr/bin/env bash

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi

# Updating apt
$SUDO apt-get update

# Installing tesseract-ocr (for use with SubtitleEdit)
# Reference: https://pypi.org/project/pgsrip/
$SUDO apt-get install software-properties-common -y
$SUDO add-apt-repository ppa:alex-p/tesseract-ocr5 -y
$SUDO apt-get update
# Installing Tesseract-OCR
$SUDO apt-get install tesseract-ocr -y
# Install all OCR language packs excluding 'ocr-script' and 'old' packages
$SUDO apt-cache search tesseract-ocr | grep -v 'ocr-script' | grep -v 'old' | awk '{print $1}' | xargs $SUDO apt install -y

# Installing python3.12
$SUDO apt-get install python3.12 -y

# Installing and updating python3-pip
$SUDO apt-get install python3-pip -y
python3 -m pip install --user --upgrade pip

# Installing python3 venv
python3 -m pip install --user virtualenv
$SUDO apt install python3.12-venv -y

# Installing MKVToolNix
$SUDO apt-get install mkvtoolnix -y

# Needed for unpacking archives, is most likely already installed
$SUDO apt-get install unrar -y

# Install tzdata and set timezone to UTC
DEBIAN_FRONTEND=noninteractive apt-get install tzdata -y

# Installing packages required for SubtitleEdit to work + other remaining packages
$SUDO apt-get install mono-complete libhunspell-dev libmpv-dev \
  vlc ffmpeg libgtk2.0-0 libsndfile1 libcanberra-gtk-module git xvfb -y

# Create a Python virtual environment (venv) and activate it
python3 -m venv venv
source venv/bin/activate

# Install required PyPI packages using Pip
pip3 install --upgrade pip
pip3 install -r requirements.txt