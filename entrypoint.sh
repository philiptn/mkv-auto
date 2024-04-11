#!/bin/bash

execute_command() {
    . venv/bin/activate
    python3 -u mkv-auto.py "$@"
}

# Pipe the output through sed to remove ANSI codes before writing to the log file
execute_command "$@" 2>&1 | stdbuf -oL tee -a >(sed 's/\x1b\[[0-9;]*m//g' > /mkv-auto/files/mkv-auto.log)