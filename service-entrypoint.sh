#!/bin/bash

# Log file path
log_file="/mkv-auto/logs/mkv-auto.log"

# Ensure the log file exists
touch "$log_file"
chmod 666 "$log_file"

# Main loop
while true; do
    # Always copy user.ini from the host if it exists to pick up potential updates
    if [ -f /mkv-auto/config/user.ini ]; then
        cp /mkv-auto/config/user.ini /mkv-auto/user.ini
    fi
    # Check if the script is already running
    if ! pgrep -f 'python3 -u mkv-auto.py' > /dev/null; then
        # Check for new files in the input directory
        if [ $(ls /mkv-auto/files/input | wc -l) -gt 0 ]; then
            cd /mkv-auto
            . venv/bin/activate
            # Determine if debug mode is enabled
            DEBUG_FLAG=""
            if [[ "${DEBUG_MODE}" == "true" ]]; then
                DEBUG_FLAG="--debug"
            fi
            # Run the Python script, ensure we capture real-time updates in user.ini
            python3 -u mkv-auto.py --move --silent --temp_folder /mkv-auto/files/tmp --input_folder /mkv-auto/files/input --output_folder /mkv-auto/files/output $DEBUG_FLAG 2>&1 | tee >(sed 's/\x1b\[[0-9;]*m//g' >> "$log_file")
        fi
    fi

    sleep 5
done
