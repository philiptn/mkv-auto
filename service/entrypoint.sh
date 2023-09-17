#!/bin/ash

. /mkv-auto-service/.env

docker load -i /mkv-auto-service/mkv-auto.tar
mkdir /mkv-auto /mkv-auto/files

echo "* * * * * docker run --rm -t -v '$HOST_FOLDER:/mkv-auto/files' $IMAGE_NAME --docker --notemp --silent" >> /etc/crontabs/root
crond -f -d 8