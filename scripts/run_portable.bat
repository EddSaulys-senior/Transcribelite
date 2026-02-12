@echo off
setlocal
cd /d "%~dp0\.."

set "DIST=portable_dist\TranscribeLite-Portable"
if not exist "%DIST%\run_portable.bat" (
  echo Portable build not found.
  echo Run scripts\build_portable.bat first.
  exit /b 1
)

if "%~1"=="" (
  echo Usage: scripts\run_portable.bat ^<file_or_folder^>
  exit /b 1
)

call "%DIST%\run_portable.bat" "%~1"
