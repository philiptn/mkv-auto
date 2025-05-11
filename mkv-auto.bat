@echo off

:: Ask runtime option
echo Runtime options:
echo 1. Latest (philiptn/mkv-auto:latest)
echo 2. Dev (philiptn/mkv-auto:dev)
echo 3. Build locally
echo.
set "default_runtime=1"
set /p runtime="Select an option [%default_runtime%]: "
if "%runtime%"=="" set runtime=%default_runtime%

:: Validate runtime choice
if "%runtime%" NEQ "1" if "%runtime%" NEQ "2" if "%runtime%" NEQ "3" (
    echo Invalid choice. Please select 1, 2 or 3.
    goto :eof
)

:: Perform selected actions
if "%runtime%"=="1" (
    docker pull philiptn/mkv-auto:latest
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto:latest --docker
) else if "%runtime%"=="2" (
    docker pull philiptn/mkv-auto:dev
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto:dev --docker
) else (
    docker build -t mkv-auto-local . >nul 2>nul
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" mkv-auto-local --docker
)

:: End script
echo Press any key to exit...
pause >nul
exit
