#!/usr/bin/env bash

# Check if the user is root or not
if [[ $EUID -ne 0 ]]; then
    # If not root, prefix commands with sudo
    SUDO='sudo'
else
    SUDO=''
fi

# Invoke sudo
$SUDO true

printf "[INFO] Starting mkv-auto-service... "
#$SUDO docker run -d --rm --name mkv-auto-service -v "/var/run/docker.sock:/var/run/docker.sock" mkv-auto-service > /dev/null 2>&1
$SUDO docker run --rm --name mkv-auto-service -v "/var/run/docker.sock:/var/run/docker.sock" mkv-auto-service
printf "Done.\n"
