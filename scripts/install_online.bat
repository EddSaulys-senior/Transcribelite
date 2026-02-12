@echo off
setlocal
cd /d "%~dp0\.."

call scripts\create_venv.bat || exit /b 1
call ".venv\Scripts\activate.bat"

python -m pip install -r requirements.txt || exit /b 1
python -m pip install --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple -r requirements-gpu.txt || exit /b 1

call scripts\doctor.bat
