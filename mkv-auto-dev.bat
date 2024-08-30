@echo off

docker image rm mkv-auto-dev
docker build -t mkv-auto-dev .
docker run --rm -it -v "%cd%:/mkv-auto/files" mkv-auto-dev --docker

timeout /t 3 /nobreak >nul
