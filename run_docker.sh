#!/usr/bin/env bash

# Defaults
IMAGE_NAME="mkv-auto"
HOST_FOLDER="/home/$USER/mkv-auto-docker"

# Source .env if it exists and override defaults
if [[ -f .env ]]; then
    source .env
fi

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi

# Invoke sudo
$SUDO true

# Build the Docker image
$SUDO docker build -t $IMAGE_NAME .

# Run the Docker container
$SUDO docker run --rm --name $IMAGE_NAME -it -v "$HOST_FOLDER:/mkv-auto/files" $IMAGE_NAME --docker
