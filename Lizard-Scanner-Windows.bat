@echo off
rem Lizard Scanner: double-click launcher for Windows
rem https://github.com/The-Dorkknight/lizard-scanner
rem
rem Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
rem Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. See LICENSE.md.
rem
rem Double-click this file. It finds Python 3 and starts lizard_scanner.py from this
rem folder. On its first run the app installs numpy, Pillow and OpenCV into lz_env\
rem in this folder. The Windows camera path is EXPERIMENTAL and UNTESTED.
setlocal
cd /d "%~dp0"
title Lizard Scanner

if not exist "lizard_scanner.py" goto noapp

set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto run
python -c "import sys; sys.exit(sys.version_info[0] != 3)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto run
goto nopython

:run
%PY% lizard_scanner.py %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Lizard Scanner stopped with an error.
echo The messages above, and bootstrap.log in this folder, say why.
echo.
pause
exit /b 1

:noapp
echo.
echo lizard_scanner.py is not in this folder.
echo Keep this launcher in the same folder as the rest of the app's files.
echo.
pause
exit /b 1

:nopython
echo.
echo Python 3 is not installed, or Windows cannot find it, and Lizard Scanner needs it to start.
echo.
echo   1. Install Python 3.12, 64-bit, from https://www.python.org/downloads/windows/
echo      In the installer keep "tcl/tk and IDLE" ticked and tick "Add python.exe to PATH".
echo   2. Double-click this launcher again.
echo.
echo Opening the download page...
start "" "https://www.python.org/downloads/windows/"
echo.
pause
exit /b 1
