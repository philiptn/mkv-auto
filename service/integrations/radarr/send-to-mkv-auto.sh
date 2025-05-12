#!/bin/bash

# If files are inside radarr_movie_path, move to mkv-auto
if find "$radarr_movie_path" -mindepth 1 | read; then
    mv "$radarr_movie_path" "/mkv-auto-input"
fi