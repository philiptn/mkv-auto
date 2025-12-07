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

# Installing python3.12 and python3-dev (required for ffsubsync to install via pip)
$SUDO apt-get install python3.12 python3-dev -y

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
$SUDO apt-get install mono-complete libhunspell-dev libmpv-dev tesseract-ocr \
  vlc libgtk2.0-0 libsndfile1 libcanberra-gtk-module git xvfb x11-utils wget flatpak vim -y

# Install static build of ffmpeg 7.1
current_dir=$(pwd)
mkdir -p ffmpeg_static_install
cd ffmpeg_static_install
wget -O ffmpeg.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2025-11-30-12-53/ffmpeg-n7.1.3-7-gf65fc0b137-linux64-gpl-7.1.tar.xz
tar -xf ffmpeg.tar.xz
cd ffmpeg-*/bin
$SUDO cp ffmpeg ffprobe /usr/local/bin
cd $current_dir
rm -rf ffmpeg_static_install

# Installing HandBrakeCLI from source
$SUDO apt-get install -y \
  autoconf automake build-essential cmake git libass-dev libbz2-dev \
  libfontconfig-dev libfreetype-dev libfribidi-dev libharfbuzz-dev \
  libjansson-dev liblzma-dev libmp3lame-dev libnuma-dev libogg-dev \
  libopus-dev libsamplerate0-dev libspeex-dev libtheora-dev libtool \
  libtool-bin libturbojpeg0-dev libvorbis-dev libx264-dev libxml2-dev \
  libvpx-dev m4 make meson nasm ninja-build patch pkg-config tar zlib1g-dev \
  curl libssl-dev clang

# Build HandBrakeCLI from source
handbrake_version="1.10.2"
current_dir=$(pwd)
wget -O HandBrake.tar.bz2 https://github.com/HandBrake/HandBrake/releases/download/${handbrake_version}/HandBrake-${handbrake_version}-source.tar.bz2
tar -xvjf HandBrake.tar.bz2
cd HandBrake-*
./configure --disable-gtk --enable-cli
cd build
make -j"$(nproc)"
$SUDO cp HandBrakeCLI /usr/local/bin/
cd $current_dir
# Clean up
rm -rf HandBrake

# Create a Python virtual environment (venv) and activate it
python3 -m venv venv
source venv/bin/activate

# Install required PyPI packages using Pip
pip3 install --upgrade pip
pip3 install -r requirements.txt

# Cleanup
$SUDO apt-get clean
$SUDO rm -rf /var/lib/apt/lists/*