#!/bin/bash

log_file='/mkv-auto/files/mkv-auto.log'

USER_ID=1000
GROUP_ID=1000

if grep -qEi 'microsoft|wsl' /proc/version 2>/dev/null; then
    IS_LINUX=true
else
    IS_LINUX=$(uname | grep -qi linux && echo true || echo false)
fi

if [ "$IS_LINUX" = true ]; then
    chown -R "$USER_ID:$GROUP_ID" /mkv-auto

    # Run as non-root user. "$@" rather than $* so arguments containing spaces
    # survive; the absolute script path keeps this out of the service loop's
    # pgrep pattern.
    exec gosu "$USER_ID:$GROUP_ID" bash -c \
        '. /pre/venv/bin/activate && exec python3 -u /mkv-auto/mkv-auto.py --log_file "$1" "${@:2}"' \
        bash "$log_file" "$@"
else
    # Windows Docker or fallback
    . /pre/venv/bin/activate
    exec python3 -u mkv-auto.py --log_file "$log_file" "$@"
fi
