@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Customs Bot - Setup
echo.
echo  ===== Egyptian Customs Bot - Setup =====
echo.
set "PYCMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PYCMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYCMD (
  echo [1/3] Python is not installed - installing Python 3.12, please wait ...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PYCMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYCMD (
  echo.
  echo  Could not install Python automatically.
  echo  The Python download page will open now: install it, tick "Add python.exe to PATH",
  echo  then double-click install_windows.bat again.
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo [1/3] Python OK
if not exist ".venv\Scripts\python.exe" (
  echo [2/3] Preparing the bot environment ...
  %PYCMD% -m venv .venv
  if errorlevel 1 ( echo  Failed to create the Python environment. & pause & exit /b 1 )
)
echo [2/3] Installing the bot packages ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 ( echo  Failed to install packages - check the internet connection and try again. & pause & exit /b 1 )
echo [3/3] Opening the setup window ...
".venv\Scripts\python.exe" tools\setup_wizard.py
if errorlevel 1 pause
exit /b 0
