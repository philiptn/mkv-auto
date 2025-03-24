@echo off

:: Ask runtime option
echo Runtime options:
echo 1. Latest (philiptn/mkv-auto)
echo 2. Build locally
echo.
set "default_runtime=latest"
set /p runtime="Select an option [%default_runtime%]: "
if "%runtime%"=="" set runtime=%default_runtime%

:: Validate runtime choice
if "%runtime%" NEQ "1" if "%runtime%" NEQ "2" (
    echo Invalid choice. Please select 1 or 2.
    goto :eof
)

:: Perform selected actions
if "%runtime%"=="1" (
    docker pull philiptn/mkv-auto
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto --docker %operation_flag%
) else (
    docker build -t mkv-auto-local . >nul 2>nul
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" mkv-auto-local --docker
)

:: End script
echo Press any key to exit...
pause >nul
exit
