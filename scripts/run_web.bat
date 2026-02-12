@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m uvicorn transcribelite.web:app --host 127.0.0.1 --port 7860
) else (
  python -m uvicorn transcribelite.web:app --host 127.0.0.1 --port 7860
)
