#!/bin/bash

execute_command() {
    . venv/bin/activate
    python3 -u mkv-auto.py "$@"
}

execute_command "$@" 2>&1 | stdbuf -oL tee -a /mkv-auto/files/mkv-auto.log