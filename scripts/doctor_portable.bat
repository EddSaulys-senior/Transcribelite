@echo off
setlocal
cd /d "%~dp0\.."

set "DIST=portable_dist\TranscribeLite-Portable"
if not exist "%DIST%\doctor_portable.bat" (
  echo Portable build not found.
  echo Run scripts\build_portable.bat first.
  exit /b 1
)

call "%DIST%\doctor_portable.bat"
