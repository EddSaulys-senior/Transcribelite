@echo off
setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
  echo Usage: scripts\run.bat ^<file_or_folder^>
  exit /b 1
)

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

python -m transcribelite.app --config config.ini transcribe "%~1"

