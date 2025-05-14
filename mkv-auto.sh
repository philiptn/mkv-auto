#!/usr/bin/env bash

# Determine the current Git branch and set the Docker tag accordingly
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -z "$branch" ]; then
  echo "Warning: not in a Git repository. Defaulting branch to 'main'."
  branch="main"
fi

# Default tag based on branch
tag=""
custom_tag=""
if [ "$branch" = "main" ]; then
  tag="latest"
else
  tag="$branch"
fi

# Set defaults
HOST_FOLDER="$(pwd)"
IMAGE_REPO="philiptn/mkv-auto"
IMAGE_NAME=""  # Will be set later
build_flag=false
no_cache=false
move_files='--move'
extra_args=()

# Check if the user is a member of the docker group
if groups "$USER" | grep -q '\bdocker\b'; then
    SUDO=''
else
    SUDO='sudo'
fi

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
        --tag)
            shift
            if [[ -n "$1" ]]; then
                custom_tag="$1"
                shift
            else
                echo "Error: --tag requires an argument."
                exit 1
            fi
            ;;
        *)
            extra_args+=("$1")
            shift
            ;;
    esac
done

# Use custom tag if provided
if [ -n "$custom_tag" ]; then
    tag="$custom_tag"
fi

# Final image name
IMAGE_NAME="$IMAGE_REPO:$tag"

# Ensure sudo (if needed) is invoked
$SUDO true

# Build or pull
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
    $SUDO docker pull "$IMAGE_NAME"
fi

# Run the container
$SUDO docker run --rm -it -u "$(id -u):$(id -g)" -v "$HOST_FOLDER:/mkv-auto/files" "$IMAGE_NAME" --docker $move_files "${extra_args[@]}"
