#!/bin/bash

# Define the target .env file
ENV_FILE=".env"

# Create .env file if it doesn't exist
if [ ! -f "$ENV_FILE" ]; then
    echo "$ENV_FILE file not found. Make sure to make your own from '.env_example'."
fi

. .env

mkdir -p $INPUT_FOLDER $OUTPUT_FOLDER $TEMP_FOLDER $CONFIG_FOLDER $LOGS_FOLDER