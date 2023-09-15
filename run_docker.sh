#!/usr/bin/env bash

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi


IMAGE_NAME="mkv-auto"
HOST_FOLDER="/mnt/d/mkv-auto-docker"


# Check if the Docker image exists
if [ "$(docker images -q $IMAGE_NAME 2> /dev/null)" == "" ]; then
    echo "Image not found. Building $IMAGE_NAME..."

    # Build the Docker image
    $SUDO docker build -t $IMAGE_NAME .

    if [ $? -ne 0 ]; then
        echo "Failed to build $IMAGE_NAME. Exiting."
        exit 1
    fi
fi

# Run the Docker container
$SUDO docker run --rm -it -v "$HOST_FOLDER:/mkv-auto/files" mkv-auto --docker