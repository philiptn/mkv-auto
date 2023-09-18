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

# Check if the Docker image exists
if [ "$($SUDO docker images -q $IMAGE_NAME 2> /dev/null)" == "" ]; then

    # Build the Docker image
    printf "Building Docker image... "
    $SUDO docker build -t $IMAGE_NAME . > /dev/null 2>&1
    printf "Done.\n"

    if [ $? -ne 0 ]; then
        echo "Failed to build $IMAGE_NAME. Exiting."
        exit 1
    fi
fi

# Build the Docker image
printf "Building Docker image... "
$SUDO docker build -t $IMAGE_NAME . > /dev/null 2>&1
printf "Done.\n"


# Run the Docker container
$SUDO docker run --rm --name mkv-auto -it -v "$HOST_FOLDER:/mkv-auto/files" mkv-auto --docker