@echo off
REM Image Hunter daily hunt - called by the Scheduled Task
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python daily_hunt.py %*
exit /b %errorlevel%
