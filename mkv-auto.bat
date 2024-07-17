@echo off

docker pull philiptn/mkv-auto
docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto --docker --move

timeout /t 3 /nobreak >nul
