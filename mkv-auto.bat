@echo off

:: Ask runtime option
echo Runtime options:
echo 1. Latest (philiptn/mkv-auto:latest)
echo 2. Custom tag (philiptn/mkv-auto:)
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

:: If custom tag is selected, ask for tag only
set "custom_tag="
if "%runtime%"=="2" (
    echo.
    echo Custom tag for image: philiptn/mkv-auto:
    set /p custom_tag="Enter tag (e.g. dev, beta, 1.2.3): "
    if not defined custom_tag (
        echo No tag entered. Exiting.
        pause
        goto :eof
    )
)

:: Ask move/copy option
echo.
echo File operation:
echo 1. Move files
echo 2. Copy files
echo.
set "default_action=1"
set /p action="Select an option [%default_action%]: "
if "%action%"=="" set action=%default_action%

:: Validate action choice and set flag
set "move_flag="
if "%action%"=="1" (
    set "move_flag=--move"
) else if "%action%"=="2" (
    set "move_flag="
) else (
    echo Invalid choice. Please select 1 or 2.
    goto :eof
)

:: Perform selected actions
if "%runtime%"=="1" (
    docker pull philiptn/mkv-auto:latest
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto:latest --docker %move_flag%
) else if "%runtime%"=="2" (
    docker pull philiptn/mkv-auto:%custom_tag%
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" philiptn/mkv-auto:%custom_tag% --docker %move_flag%
) else (
    docker build -t mkv-auto-local . >nul 2>nul
    echo.
    docker run --rm -it -v "%cd%:/mkv-auto/files" mkv-auto-local --docker %move_flag%
)

:: End script
echo Press any key to exit...
pause >nul
exit
