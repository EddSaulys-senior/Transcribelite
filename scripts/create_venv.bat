@echo off
setlocal
cd /d "%~dp0\.."

if not exist ".venv" (
  py -3 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 exit /b 1

echo Virtual environment is ready.

