#!/usr/bin/env bash

IMAGE_NAME="philiptn/mkv-auto"
HOST_FOLDER="$(pwd)"

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi

# Initialize variables for the options
extra_args=()
build_flag=false

# Loop through all the arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            build_flag=true
            IMAGE_NAME="mkv-auto"
            shift  # Move to the next argument
            ;;
        *)  # Capture any other arguments
            extra_args+=("$1")
            shift # Move to the next argument
            ;;
    esac
done

# Invoke sudo
$SUDO true

# Building the image locally if "--build" is passed to the script
if [ "$build_flag" = true ]; then
    echo "Building Docker image..."
    $SUDO docker image rm -t mkv-auto > /dev/null 2>&1
    $SUDO docker build -t mkv-auto . > /dev/null 2>&1
    echo -e "\033[K\033[1A\033[K"
fi

$SUDO docker run --rm -it -v "$HOST_FOLDER:/mkv-auto/files" $IMAGE_NAME --docker --move "${extra_args[@]}"
