#!/usr/bin/env bash

HOSTPATH=/mnt/d/test/mkv-auto

mkdir -p $HOSTPATH
docker run --name tmp_mkv-auto mkv-auto /bin/true
docker cp tmp_mkv-auto:/mkv-auto $HOSTPATH
docker rm tmp_mkv-auto