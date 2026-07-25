@echo off
setlocal EnableExtensions
title Convert all Trainz IM files in this folder

set "OUT=%~dp0OBJ_EXPORT"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0im_to_obj_universal.py" "%~dp0" -o "%OUT%" --copy-textures
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
    python "%~dp0im_to_obj_universal.py" "%~dp0" -o "%OUT%" --copy-textures
)

set "CODE=%errorlevel%"
echo.
echo Output folder: "%OUT%"
if not "%CODE%"=="0" echo Some files were not converted. Check the batch report.
echo.
pause
exit /b %CODE%
