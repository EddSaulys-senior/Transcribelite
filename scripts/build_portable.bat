@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "DIST_ROOT=portable_dist\TranscribeLite-Portable"
if not "%PORTABLE_DIST_ROOT%"=="" set "DIST_ROOT=%PORTABLE_DIST_ROOT%"
set "DIST_TMP=portable_dist\_portable_build_tmp"
set "PY_SRC=C:\Python311"
if not "%PORTABLE_PYTHON_SRC%"=="" set "PY_SRC=%PORTABLE_PYTHON_SRC%"

if not exist ".venv\Lib\site-packages" (
  echo ERROR: .venv not found. Run scripts\install_online.bat first.
  exit /b 1
)

if not exist "%PY_SRC%\python.exe" (
  echo ERROR: Python runtime source not found: %PY_SRC%
  echo Tip: set PORTABLE_PYTHON_SRC to your Python 3.11 folder.
  exit /b 1
)

echo Preparing folders...
if exist "%DIST_TMP%" rmdir /s /q "%DIST_TMP%"
if exist "%DIST_TMP%" (
  echo ERROR: Cannot remove temp folder "%DIST_TMP%".
  exit /b 1
)
if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
if exist "%DIST_ROOT%" (
  echo ERROR: Cannot remove old portable build "%DIST_ROOT%".
  echo Close apps/processes that may lock files and retry.
  exit /b 1
)
mkdir "%DIST_TMP%" || exit /b 1

echo Copying application files ^(minimal set^)...
robocopy "transcribelite" "%DIST_TMP%\transcribelite" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc *.pyo
if errorlevel 8 exit /b %errorlevel%
robocopy "web" "%DIST_TMP%\web" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc *.pyo
if errorlevel 8 exit /b %errorlevel%
robocopy "prompts" "%DIST_TMP%\prompts" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc *.pyo
if errorlevel 8 exit /b %errorlevel%
copy /Y "config.ini" "%DIST_TMP%\config.ini" >nul

echo Copying Python runtime from %PY_SRC%...
robocopy "%PY_SRC%" "%DIST_TMP%\python" /E /NFL /NDL /NJH /NJS /NP ^
  /XD __pycache__ .git Doc Tools include libs Scripts tcl Lib\site-packages Lib\test Lib\tkinter Lib\idlelib
if errorlevel 8 exit /b %errorlevel%

echo Copying project dependencies from .venv...
mkdir "%DIST_TMP%\python\Lib\site-packages" >nul 2>nul
robocopy ".venv\Lib\site-packages" "%DIST_TMP%\python\Lib\site-packages" /E /NFL /NDL /NJH /NJS /NP ^
  /XD __pycache__ /XF *.pyc *.pyo
if errorlevel 8 exit /b %errorlevel%

echo Preparing portable config and ffmpeg...
powershell -NoProfile -Command ^
  "$p='%CD%\%DIST_TMP%\config.ini';" ^
  "(Get-Content $p) -replace '^base_dir\s*=.*$','base_dir = .' | Set-Content $p -Encoding UTF8"

powershell -NoProfile -Command ^
  "$p='%CD%\%DIST_TMP%\config.ini';" ^
  "$ff=(Get-Content $p | Where-Object { $_ -match '^ffmpeg_path\s*=' } | Select-Object -First 1);" ^
  "$v=$null; if($ff){ $v=($ff -split '=',2)[1].Trim() };" ^
  "if($v -and (Test-Path $v)){ " ^
  "  $dir=Split-Path $v -Parent;" ^
  "  New-Item -ItemType Directory -Force -Path '%CD%\%DIST_TMP%\ffmpeg' | Out-Null;" ^
  "  Copy-Item -Path (Join-Path $dir '*') -Destination '%CD%\%DIST_TMP%\ffmpeg' -Recurse -Force;" ^
  "  (Get-Content $p) -replace '^ffmpeg_path\s*=.*$','ffmpeg_path = ffmpeg\\ffmpeg.exe' | Set-Content $p -Encoding UTF8;" ^
  "} else { " ^
  "  (Get-Content $p) -replace '^ffmpeg_path\s*=.*$','ffmpeg_path = ffmpeg' | Set-Content $p -Encoding UTF8;" ^
  "}"

echo Creating runtime data folders...
mkdir "%DIST_TMP%\cache" >nul 2>nul
mkdir "%DIST_TMP%\cache\dictation" >nul 2>nul
mkdir "%DIST_TMP%\cache\uploads" >nul 2>nul
mkdir "%DIST_TMP%\data" >nul 2>nul
mkdir "%DIST_TMP%\logs" >nul 2>nul
mkdir "%DIST_TMP%\output" >nul 2>nul
mkdir "%DIST_TMP%\models" >nul 2>nul
mkdir "%DIST_TMP%\wheels" >nul 2>nul

echo Writing portable launchers...
(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo if "%%~1"=="" ^(
  echo   echo Usage: run_portable.bat ^^^<file_or_folder^^^>
  echo   exit /b 1
  echo ^)
  echo python\python.exe -m transcribelite.app --config config.ini transcribe "%%~1"
) > "%DIST_TMP%\run_portable.bat"

(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo python\python.exe -m transcribelite.app --config config.ini doctor
) > "%DIST_TMP%\doctor_portable.bat"

(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo python\python.exe -m uvicorn transcribelite.web:app --host 127.0.0.1 --port 7860
) > "%DIST_TMP%\run_web.bat"

(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo python\python.exe -m transcribelite.hotkey --config config.ini
) > "%DIST_TMP%\run_hotkey.bat"

(
  echo TranscribeLite Portable
  echo =======================
  echo.
  echo QUICK START
  echo -----------
  echo 1^) Unpack this folder on the target PC.
  echo 2^) Run environment check:
  echo    doctor_portable.bat
  echo 3^) Run transcription from CLI:
  echo    run_portable.bat ^<file_or_folder^>
  echo 4^) Or run Web UI:
  echo    run_web.bat
  echo    then open http://127.0.0.1:7860
  echo 5^) Optional global hotkey dictation:
  echo    run_hotkey.bat
  echo.
  echo WHISPER MODELS ^(models\ folder^)
  echo -------------------------------
  echo Online PC:
  echo - Models are downloaded automatically on first run.
  echo Offline PC:
  echo - Copy pre-downloaded model folders into models\ from another machine.
  echo.
  echo OLLAMA SUMMARY MODELS
  echo ---------------------
  echo - Summary/QA polish requires local Ollama.
  echo - Install Ollama and pull model, for example:
  echo   ollama pull llama3.1:8b
  echo - If Ollama is unavailable, transcription still works, only summary is skipped.
  echo.
  echo HUGGING FACE TOKENS ^(only for gated/private models, e.g. pyannote^)
  echo --------------------------------------------------------------------
  echo 1^) Login: https://huggingface.co/login
  echo 2^) Create token ^(Read role^): https://huggingface.co/settings/tokens
  echo 3^) In cmd for current session:
  echo    set HF_TOKEN=hf_your_token_here
  echo 4^) To save permanently:
  echo    setx HF_TOKEN "hf_your_token_here"
  echo.
  echo PYANNOTE ACCESS CONFIRMATION PAGES
  echo ----------------------------------
  echo - https://huggingface.co/pyannote/segmentation-3.0
  echo - https://huggingface.co/pyannote/speaker-diarization-3.1
  echo On each page click access/agree buttons to accept repository terms.
  echo.
  echo IMPORTANT PATHS
  echo ---------------
  echo - models\      : Whisper models cache
  echo - output\      : Exported results
  echo - logs\        : Application logs
  echo - config.ini   : Main portable configuration
  echo.
  echo TROUBLESHOOTING
  echo ---------------
  echo - ffmpeg FAIL in doctor: ensure ffmpeg\ffmpeg.exe exists.
  echo - torch.cuda FAIL: GPU may be unavailable; CPU fallback is used.
  echo - "No module named ...": rebuild portable from machine with valid .venv.
) > "%DIST_TMP%\README_PORTABLE.txt"

move "%DIST_TMP%" "%DIST_ROOT%" >nul
if errorlevel 1 (
  echo ERROR: Failed to finalize portable folder.
  exit /b 1
)

echo Portable build completed: %DIST_ROOT%
echo Run: %DIST_ROOT%\doctor_portable.bat
