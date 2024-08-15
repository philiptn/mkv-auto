#!/bin/bash

log_file='/mkv-auto/files/mkv-auto.log'

execute_command() {
    . venv/bin/activate
    python3 -u mkv-auto.py --log_file $log_file "$@"
}

execute_command "$@"