@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m transcribelite.hotkey --config config.ini
) else (
  python -m transcribelite.hotkey --config config.ini
)

