#!/bin/bash

command=". venv/bin/activate && python3 mkv-auto.py >> files/mkv-auto.log 2>&1"
eval "$command" "$@"