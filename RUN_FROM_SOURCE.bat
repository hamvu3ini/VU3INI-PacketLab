@echo off
rem Run VU3INI PacketLab directly from the source code (uses the build environment made by BUILD.bat)
setlocal
cd /d "%~dp0"
set "VPY="
for /d %%D in ("build\venv-cp3*") do if exist "%%D\Scripts\pythonw.exe" set "VPY=%%D\Scripts\pythonw.exe"
if not defined VPY (
  echo Run BUILD.bat once first ^(it creates the Python environment^).
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0app"
start "" "%VPY%" "%~dp0app\run_packetlab.py" %*
