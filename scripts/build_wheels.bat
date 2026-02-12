@echo off
setlocal
cd /d "%~dp0\.."

call scripts\create_venv.bat || exit /b 1
call ".venv\Scripts\activate.bat"

if not exist wheels mkdir wheels

python -m pip download -r requirements.txt -d wheels || exit /b 1
python -m pip download --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple -r requirements-gpu.txt -d wheels || exit /b 1
python -m pip download pip setuptools wheel -d wheels || exit /b 1

python -m pip freeze > wheels\freeze.txt || exit /b 1
echo Wheels are ready in .\wheels
