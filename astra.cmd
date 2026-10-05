@echo off
rem ASTRA entry point. On first run it creates the Python environment, then hands over to astra.py.
rem Messages here are English on purpose: a .cmd file cannot safely carry Korean text on every Windows setup.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo [astra] First run: creating the Python environment. This takes a few minutes.
where py >nul 2>nul
if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
if not exist ".venv\Scripts\python.exe" (
  echo [astra] Python 3 was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.lock.txt
if errorlevel 1 (
  echo [astra] Installing packages failed. Check the internet connection and run this again.
  exit /b 1
)
:run
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
".venv\Scripts\python.exe" astra.py %*
exit /b %errorlevel%
