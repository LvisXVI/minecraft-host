@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 wurde nicht gefunden. Bitte Python 3.11 oder neuer installieren.
  echo https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
python app.py
if errorlevel 1 pause
