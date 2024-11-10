#!/bin/bash

# Define the target .env file
ENV_FILE=".env"

# Get the current user's UID and GID
USER_UID=$(id -u)
USER_GID=$(id -g)

# Create .env file if it doesn't exist
if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
fi

# Update or add UID in .env
if grep -q "^UID=" "$ENV_FILE"; then
    sed -i "s/^UID=.*/UID=${USER_UID}/" "$ENV_FILE"
else
    echo "UID=${USER_UID}" >> "$ENV_FILE"
fi

# Update or add GID in .env
if grep -q "^GID=" "$ENV_FILE"; then
    sed -i "s/^GID=.*/GID=${USER_GID}/" "$ENV_FILE"
else
    echo "GID=${USER_GID}" >> "$ENV_FILE"
fi

echo "Updated $ENV_FILE with UID=${USER_UID} and GID=${USER_GID}"
