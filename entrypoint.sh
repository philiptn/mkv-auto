#!/bin/bash

execute_command() {
    . venv/bin/activate
    python3 mkv-auto.py "$@"
}

execute_command "$@" 2>&1 | tee -a /mkv-auto/files/mkv-auto.log