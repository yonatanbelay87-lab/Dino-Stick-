@echo off
REM Dino Stick launcher. Double-click this, or run it from a terminal.
REM Creates the virtual environment on first run, then starts the game.
setlocal
title Dino Stick
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if exist "%PY%" goto run

echo First run: setting up. This takes a minute...
REM Kivy 2.3.1 has no wheels for Python 3.14, so pin 3.12 explicitly.
py -3.12 -m venv "%ROOT%.venv"
if errorlevel 1 goto nopython
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install "kivy[base]==2.3.1"
if errorlevel 1 goto nokivy

:run
echo Starting Dino Stick...
"%PY%" "%ROOT%dinostick\main.py" %*
if errorlevel 1 goto crashed
exit /b 0


:nopython
echo.
echo ERROR: could not create the virtual environment.
echo Python 3.12 is required; Kivy has no wheels for 3.14 yet.
echo Installed Python versions:
py -0p
echo.
pause
exit /b 1


:nokivy
echo.
echo ERROR: installing Kivy failed. Check your internet connection.
echo.
pause
exit /b 1

:crashed
echo.
echo The game exited with an error. Scroll up for the message.
echo.
pause
exit /b 1
