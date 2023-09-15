#!/bin/bash

command=". venv/bin/activate && python3 mkv-auto.py"
eval "$command" "$@"