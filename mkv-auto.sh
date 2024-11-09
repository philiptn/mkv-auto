#!/usr/bin/env bash

IMAGE_NAME="philiptn/mkv-auto"
HOST_FOLDER="$(pwd)"

# Check if the user is a member of the docker group
if groups $USER | grep -q '\bdocker\b'; then
    # If user is part of the docker group, do not use sudo
    SUDO=''
else
    # If not part of the docker group, prefix commands with sudo
    SUDO='sudo'
fi

# Initialize variables for the options
extra_args=()
build_flag=false
move_files='--move'
no_cache=false

# Loop through all the arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            build_flag=true
            IMAGE_NAME="mkv-auto"
            shift  # Move to the next argument
            ;;
        --copy)
            move_files=''
            shift  # Move to the next argument
            ;;
        --no-cache)
            no_cache=true
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
    if [ "$no_cache" = true ]; then
      $SUDO docker image rm -t mkv-auto > /dev/null 2>&1
      $SUDO docker system prune -a -f > /dev/null 2>&1
      $SUDO docker build --no-cache -t mkv-auto .
    else
      echo "Building Docker image..."
      $SUDO docker image rm -t mkv-auto > /dev/null 2>&1
      $SUDO docker build -t mkv-auto . > /dev/null 2>&1
      echo -e "\033[K\033[1A\033[K"
    fi
else
    # Update to latest version on Docker Hub
    $SUDO docker pull $IMAGE_NAME
fi

$SUDO docker run --rm -it -u $(id -u):$(id -g) -v "$HOST_FOLDER:/mkv-auto/files" $IMAGE_NAME --docker $move_files "${extra_args[@]}"