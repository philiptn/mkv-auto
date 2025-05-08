#!/bin/bash

# If env sonarr_series_path is not set, exit 0 (for test)
if [ -z "$sonarr_series_path" ]; then
    exit 0
fi

# If files are inside sonarr_series_path, move to mkv-auto
if find "$sonarr_series_path" -mindepth 1 | read; then
    mv "$sonarr_series_path" "/mkv-auto-input"
fi