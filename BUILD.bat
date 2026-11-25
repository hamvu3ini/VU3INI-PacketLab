@echo off
setlocal
cd /d "%~dp0"
title VU3INI PacketLab - building the Windows installer
echo.
echo  ==========================================================
echo     VU3INI PacketLab  -  one-click Windows installer build
echo     By Sasanka Boruah - VU3INI
echo  ==========================================================
echo.
set "PY="
for %%V in (3.13 3.12 3.14 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo  Python 3.11, 3.12, 3.13 or 3.14 ^(64-bit^) was not found.
  echo  Install it from the Microsoft Store or python.org, tick "Add python.exe to PATH", then run BUILD.bat again.
  echo.
  pause
  exit /b 1
)
echo  Using: %PY%
%PY% build.py %*
if errorlevel 1 (
  echo.
  echo  The build stopped with an error - see the messages above.
  pause
  exit /b 1
)
echo.
echo  Finished. The installer zip is in the "release" folder.
pause
