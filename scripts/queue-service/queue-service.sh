#!/usr/bin/env bash

cd /media/philip/nvme/mkv-auto || return
. venv/bin/activate
python3 scripts/queue-service/queue-service.py --file_path "/media/share/mkv-auto-queue/in_queue.txt"