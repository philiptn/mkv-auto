#!/bin/bash

log_file="/mkv-auto/logs/mkv-auto.log"

touch "$log_file"
chmod 666 "$log_file"

# Answers output-path lookups from the shared queue folder (used by the
# qBittorrent live-copy integration). Runs alongside the poll loop below, which
# blocks for the whole duration of a processing run. Its output is discarded so
# it cannot interleave with the pipeline's console output - run the worker by
# hand to see its log.
if [ "${RESOLVE_WORKER,,}" = "true" ]; then
    . /pre/venv/bin/activate
    python3 -u /mkv-auto/resolve-worker.py > /dev/null 2>&1 &
fi

while true; do
    if [ -f /mkv-auto/config/user.ini ]; then
        cp /mkv-auto/config/user.ini /mkv-auto/.user.ini.tmp && mv /mkv-auto/.user.ini.tmp /mkv-auto/user.ini
    fi
    if [ -f /mkv-auto/config/subliminal.toml ]; then
        cp /mkv-auto/config/subliminal.toml /mkv-auto/.subliminal.toml.tmp && mv /mkv-auto/.subliminal.toml.tmp /mkv-auto/subliminal.toml
    fi

    # Matches only this loop's own run - a --resolve-path subprocess started by
    # the resolve worker must neither block nor be blocked by it.
    if ! pgrep -f 'mkv-auto\.py --service' > /dev/null; then
        if [ "$(ls /mkv-auto/files/input | wc -l)" -gt 0 ]; then
            cd /mkv-auto
            . /pre/venv/bin/activate
            python3 -u mkv-auto.py --service --move --silent --temp_folder /mkv-auto/files/tmp --log_file "$log_file" --input_folder /mkv-auto/files/input --output_folder /mkv-auto/files/output $DEBUG_FLAG
        fi
    fi

    sleep 5
done
