#!/usr/bin/env bash

cd /media/philip/nvme/mkv-auto
. venv/bin/activate
python3 scripts/mkv-auto-service/mkv-auto-service.py --file_path "/media/share/mkv-auto-queue/in_queue.txt" --output_folder "/media/philip/nvme/dump/" >> /home/philip/logs/mkv-auto-service.log 2>&1