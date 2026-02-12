@echo off
setlocal
cd /d "%~dp0\.."

call scripts\create_venv.bat || exit /b 1
call ".venv\Scripts\activate.bat"

python -m pip install --no-index --find-links=.\wheels -r requirements.txt || exit /b 1
python -m pip install --no-index --find-links=.\wheels -r requirements-gpu.txt || exit /b 1

call scripts\doctor.bat

