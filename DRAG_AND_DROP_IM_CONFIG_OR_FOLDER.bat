@echo off
setlocal EnableExtensions
title Universal Trainz IM to OBJ Converter 2.1

if "%~1"=="" (
    echo.
    echo Drag and drop onto this BAT file:
    echo   - one or more .im files
    echo   - a config.txt file
    echo   - a folder containing a Trainz asset
    echo.
    echo Folders are scanned recursively.
    echo OBJ files are created next to the source IM files.
    echo.
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0im_to_obj_universal.py" %* --copy-textures
) else (
    where python >nul 2>nul
    if not %errorlevel%==0 (
        echo.
        echo ERROR: Python 3 was not found.
        echo Install Python from python.org and enable "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
    python "%~dp0im_to_obj_universal.py" %* --copy-textures
)

set "CODE=%errorlevel%"
echo.
if "%CODE%"=="0" (
    echo Conversion completed successfully.
) else (
    echo Some files could not be converted.
    echo Check _im_to_obj_batch_report.txt and the individual report files.
)
echo.
pause
exit /b %CODE%
