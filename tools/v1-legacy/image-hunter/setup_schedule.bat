@echo off
REM ============================================================
REM  One-time install: register a daily Windows Scheduled Task.
REM  Double-click this file. Uninstall:
REM    schtasks /Delete /TN "ImageHunterDaily" /F
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo [1/3] Checking Python and requests ...
python --version >nul 2>&1 || (echo Python not found in PATH. Please install Python first. & pause & exit /b 1)
python -c "import requests" 2>nul || python -m pip install requests

echo [2/3] Registering Scheduled Task "ImageHunterDaily" (daily at 08:30) ...
schtasks /Create /TN "ImageHunterDaily" /TR "\"%~dp0run_daily.bat\"" /SC DAILY /ST 08:30 /F
if %errorlevel% neq 0 ( echo Register FAILED. Try right-click then Run as administrator. & pause & exit /b 1 )

echo [3/3] Test run now (dry-run: discover + score only, no download) ...
python daily_hunt.py --dry-run

echo.
echo DONE. The task will run every day at 08:30.
echo   Run for real now:    run_daily.bat
echo   Change time/sources: edit config.json
echo   Show next run time:   schtasks /Query /TN "ImageHunterDaily" /V /FO LIST
pause
