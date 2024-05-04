#!/usr/bin/env bash

# Defaults
IMAGE_NAME="mkv-auto"
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

# Loop through all the arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        *)  # Capture any other arguments
            extra_args+=("$1")
            shift # Move to the next argument
            ;;
    esac
done

# Invoke sudo
$SUDO true

# Build the Docker image
echo "Building Docker image..."
$SUDO docker build -t "$IMAGE_NAME" . > /dev/null 2>&1
echo -e "\033[K\033[1A\033[K"

# Remove old, dangling images to free up space
$SUDO docker image prune -f > /dev/null 2>&1

# Run the Docker container with the accumulated options
$SUDO docker run --rm --name "$IMAGE_NAME" -it -v "$HOST_FOLDER:/mkv-auto/files" $IMAGE_NAME --docker "${extra_args[@]}"
