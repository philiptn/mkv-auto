#!/usr/bin/env bash

# Defaults
IMAGE_NAME="mkv-auto"
# Source .env if it exists and override defaults
if [[ -f .env ]]; then
    source .env_example
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

# Build the mkv-auto Docker image
printf "Building Docker image 'mkv-auto'... "
$SUDO docker build -t $IMAGE_NAME . > /dev/null 2>&1
printf "Done.\n"

# Save the mkv-auto Docker image to tar
printf "Saving Docker image 'mkv-auto'... "
$SUDO docker save -o service/mkv-auto-builds/mkv-auto.tar mkv-auto:latest
printf "Done.\n"

# Build the mkv-auto Docker image
printf "Building Docker image 'mkv-auto-service'... "
$SUDO docker build -t mkv-auto-service -f service/Dockerfile . > /dev/null 2>&1
printf "Done.\n"
