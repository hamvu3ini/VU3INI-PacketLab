@echo off
cd /d "%~dp0"
title VU3INI PacketLab - publish to GitHub
echo Publishing... see publish_log.txt
call "%~dp0publish_steps.bat" > "%~dp0publish_log.txt" 2>&1
type "%~dp0publish_log.txt"
pause
