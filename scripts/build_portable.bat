@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."

set "DIST_ROOT=portable_dist\TranscribeLite-Portable"
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

echo Preparing %DIST_ROOT%
if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
mkdir "%DIST_ROOT%" || exit /b 1

echo Copying project files...
robocopy . "%DIST_ROOT%" /E /NFL /NDL /NJH /NJS /NP ^
  /XD .venv cache logs output models wheels Test_wav portable_dist .git .idea .vscode __pycache__ ^
  /XF *.pyc *.pyo *.pyd *.tmp *.temp
if errorlevel 8 exit /b %errorlevel%

echo Copying Python runtime from %PY_SRC%...
robocopy "%PY_SRC%" "%DIST_ROOT%\python" /E /NFL /NDL /NJH /NJS /NP ^
  /XD __pycache__ .git Doc Tools tcl\tk8.6\demos
if errorlevel 8 exit /b %errorlevel%

echo Copying installed packages from .venv...
mkdir "%DIST_ROOT%\python\Lib\site-packages" >nul 2>nul
robocopy ".venv\Lib\site-packages" "%DIST_ROOT%\python\Lib\site-packages" /E /NFL /NDL /NJH /NJS /NP ^
  /XD __pycache__
if errorlevel 8 exit /b %errorlevel%

echo Creating portable config...
copy /Y config.ini "%DIST_ROOT%\config.portable.ini" >nul
powershell -NoProfile -Command ^
  "$p='%CD%\%DIST_ROOT%\config.portable.ini';" ^
  "(Get-Content $p) -replace '^base_dir\\s*=.*$','base_dir = .' |" ^
  "ForEach-Object { $_ } | Set-Content $p -Encoding UTF8"

powershell -NoProfile -Command ^
  "$p='%CD%\%DIST_ROOT%\config.portable.ini';" ^
  "$ff=(Get-Content $p | Where-Object { $_ -match '^ffmpeg_path\\s*=' } | Select-Object -First 1);" ^
  "$v=$null; if($ff){ $v=($ff -split '=',2)[1].Trim() };" ^
  "if($v -and (Test-Path $v)){ " ^
  "  $dir=Split-Path $v -Parent;" ^
  "  New-Item -ItemType Directory -Force -Path '%CD%\%DIST_ROOT%\ffmpeg' | Out-Null;" ^
  "  Copy-Item -Path (Join-Path $dir '*') -Destination '%CD%\%DIST_ROOT%\ffmpeg' -Recurse -Force;" ^
  "  (Get-Content $p) -replace '^ffmpeg_path\\s*=.*$','ffmpeg_path = ffmpeg\\ffmpeg.exe' | Set-Content $p -Encoding UTF8;" ^
  "} else { " ^
  "  (Get-Content $p) -replace '^ffmpeg_path\\s*=.*$','ffmpeg_path = ffmpeg' | Set-Content $p -Encoding UTF8;" ^
  "}"

echo Creating empty runtime folders...
mkdir "%DIST_ROOT%\cache" >nul 2>nul
mkdir "%DIST_ROOT%\logs" >nul 2>nul
mkdir "%DIST_ROOT%\output" >nul 2>nul
mkdir "%DIST_ROOT%\models" >nul 2>nul
mkdir "%DIST_ROOT%\wheels" >nul 2>nul

echo Writing launcher...
(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo if "%%~1"=="" ^(
  echo   echo Usage: run_portable.bat ^^^<file_or_folder^^^>
  echo   exit /b 1
  echo ^)
  echo python\python.exe -m transcribelite.app --config config.portable.ini transcribe "%%~1"
) > "%DIST_ROOT%\run_portable.bat"

(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo python\python.exe -m transcribelite.app --config config.portable.ini doctor
) > "%DIST_ROOT%\doctor_portable.bat"

echo Portable build completed: %DIST_ROOT%
echo Copy this folder to another PC and run run_portable.bat
