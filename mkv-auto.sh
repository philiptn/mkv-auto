#!/usr/bin/env bash

# Determine the current Git branch and set the Docker tag accordingly
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -z "$branch" ]; then
  echo "Warning: not in a Git repository. Defaulting branch to 'main'."
  branch="main"
fi

if [ "$branch" = "main" ]; then
  tag="latest"
else
  tag="$branch"
fi

# Set the image name using the computed tag.
IMAGE_NAME="philiptn/mkv-auto:$tag"
HOST_FOLDER="$(pwd)"

# Check if the user is a member of the docker group
if groups "$USER" | grep -q '\bdocker\b'; then
    SUDO=''
else
    SUDO='sudo'
fi

# Initialize variables for the options
extra_args=()
build_flag=false
move_files='--move'
no_cache=false

# Process script arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            build_flag=true
            shift
            ;;
        --copy)
            move_files=''
            shift
            ;;
        --no-cache)
            no_cache=true
            shift
            ;;
        *)
            extra_args+=("$1")
            shift
            ;;
    esac
done

# Ensure sudo (if needed) is invoked
$SUDO true

if [ "$build_flag" = true ]; then
    if [ "$no_cache" = true ]; then
        $SUDO docker image rm "$IMAGE_NAME" > /dev/null 2>&1
        $SUDO docker system prune -a -f > /dev/null 2>&1
        $SUDO docker build --no-cache -t "$IMAGE_NAME" .
    else
        echo "Building Docker image..."
        $SUDO docker image rm "$IMAGE_NAME" > /dev/null 2>&1
        $SUDO docker build -t "$IMAGE_NAME" . > /dev/null 2>&1
        echo -e "\033[K\033[1A\033[K"
    fi
else
    # Pull the image from Docker Hub
    $SUDO docker pull "$IMAGE_NAME"
fi

$SUDO docker run --rm -it -u "$(id -u):$(id -g)" -v "$HOST_FOLDER:/mkv-auto/files" "$IMAGE_NAME" --docker $move_files "${extra_args[@]}"
