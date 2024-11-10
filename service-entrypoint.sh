#!/bin/bash

# Set default UID and GID dynamically based on the input folder's ownership
USER_ID=${UID:-$(stat -c '%u' /mkv-auto/files/input 2>/dev/null || echo 1000)}
GROUP_ID=${GID:-$(stat -c '%g' /mkv-auto/files/input 2>/dev/null || echo 1000)}

# Create a user and group with the determined UID and GID
groupadd -o -g "$GROUP_ID" tempgroup
useradd -o -u "$USER_ID" -g "$GROUP_ID" -m tempuser

# Set ownership for mounted directories
chown -R tempuser:tempgroup /mkv-auto/files /mkv-auto/config /mkv-auto/logs

# Log file path
log_file="/mkv-auto/logs/mkv-auto.log"

# Ensure the log file exists
touch "$log_file"
chmod 666 "$log_file"

# Switch to the created user and run the main loop
exec gosu tempuser bash -c '
# Main loop
while true; do
    # Always copy user.ini from the host if it exists to pick up potential updates
    if [ -f /mkv-auto/config/user.ini ]; then
        cp /mkv-auto/config/user.ini /mkv-auto/user.ini
    fi
    if [ -f /mkv-auto/config/subliminal.toml ]; then
        cp /mkv-auto/config/subliminal.toml /mkv-auto/subliminal.toml
    fi
    # Check if the script is already running
    if ! pgrep -f "python3 -u mkv-auto.py" > /dev/null; then
        # Check for new files in the input directory
        if [ $(ls /mkv-auto/files/input | wc -l) -gt 0 ]; then
            cd /mkv-auto
            . venv/bin/activate
            # Determine if debug mode is enabled
            DEBUG_FLAG=""
            if [[ "${DEBUG_MODE}" == "true" ]]; then
                DEBUG_FLAG="--debug --service"
            fi
            # Run the Python script, ensure we capture real-time updates in user.ini
            python3 -u mkv-auto.py --move --silent --temp_folder /mkv-auto/files/tmp --log_file $log_file --input_folder /mkv-auto/files/input --output_folder /mkv-auto/files/output $DEBUG_FLAG
        fi
    fi

    sleep 5
done
'