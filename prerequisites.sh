#!/usr/bin/env bash

# Installing and updating python3-pip
sudo apt-get install python3-pip -y
python3 -m pip install --user --upgrade pip

# Installing python3 venv
python3 -m pip install --user virtualenv
sudo apt install python3.10-venv -y

# Installing MKVToolNix
sudo apt-get install mkvtoolnix -y

# Installing flatpak and HandBrakeCLI (via flatpak)
sudo apt-get install flatpak -y
sudo flatpak install fr.handbrake.ghb -y

# Needed for unpacking archives, is most likely already installed
sudo apt-get install unrar -y

# Installing packages required for SubtitleEdit (in 'utlities/') to work, as well as autosubsync
sudo apt-get install mono-complete libhunspell-dev libmpv-dev tesseract-ocr vlc ffmpeg libgtk2.0-0 libsndfile1 libcanberra-gtk-module -y

# Installing tesseract-ocr (for use with SubtitleEdit)
# Reference: https://pypi.org/project/pgsrip/
sudo add-apt-repository ppa:alex-p/tesseract-ocr5 -y
sudo apt-get update
sudo apt-get install tesseract-ocr -y
# Additional language packs need to be installed manually!
# To list the available language packs run `sudo apt install tesseract-ocr-lang`
sudo apt-get install tesseract-ocr-nor -y

# Installing training data for tesseract (tessdata) (note: large datasets, may take a while to download)
# Reference: https://pypi.org/project/pgsrip/
mkdir -p ~/.mkv-auto
git clone https://github.com/tesseract-ocr/tessdata_best.git ~/.mkv-auto/tessdata

# Create a Python virtual environment (venv) and activate it
python3 -m venv venv
source venv/bin/activate

# Install required PyPI packages using Pip
pip3 install --upgrade pip
pip3 install -r requirements.txt