#!/bin/ash

. /mkv-auto-service/.env

docker load -i /mkv-auto-service/mkv-auto.tar

echo "* * * * * docker run --rm -t -v '$HOST_FOLDER:/mkv-auto/files' -v '$INPUT_FOLDER:/mkv-auto/files/input' -v '$OUTPUT_FOLDER:/mkv-auto/files/output' $IMAGE_NAME --notemp --docker --silent" >> /etc/crontabs/root
crond -f -d 8