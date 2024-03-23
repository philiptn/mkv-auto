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

# Default debug mode to off
debug=0

# Loop through all the arguments
for arg in "$@"
do
    case $arg in
        --debug)
            debug=1
            ;;
        *)
            # Handle other arguments if necessary
            ;;
    esac
done

if [[ $debug -eq 1 ]]; then
  debug="--debug"
else
  debug=""
fi

# Build the Docker image
echo -e "Building Docker image..."
docker build -t $IMAGE_NAME . > /dev/null 2>&1
echo -e "\033[K\033[1A\033[K"

# Remove old, dangling images to free up space
docker image prune -f > /dev/null 2>&1

# Run the Docker container
docker run --rm --name $IMAGE_NAME -it -v "$HOST_FOLDER:/mkv-auto/files" $IMAGE_NAME --notemp --docker $debug