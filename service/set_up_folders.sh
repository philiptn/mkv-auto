#!/bin/bash

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "$ENV_FILE file not found. Make sure to make your own from '.env_example'."
fi

. .env

# Define folders in an array
FOLDERS=("$INPUT_FOLDER" "$OUTPUT_FOLDER" "$TEMP_FOLDER" "$CONFIG_FOLDER" "$LOGS_FOLDER")

echo "Environment loaded from $ENV_FILE"
echo "Checking and creating folders..."

for FOLDER in "${FOLDERS[@]}"; do
    if [ -d "$FOLDER" ]; then
        echo "Already exists: $FOLDER"
    else
        mkdir -p "$FOLDER"
        echo "Created:        $FOLDER"
    fi
done
