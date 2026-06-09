@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
REM Force Python to emit UTF-8 so Chinese logs render under code page 65001
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

set "DDS_PROJECT=C:\Users\shiguanyu\DDS"
set "DDS_URL=http://localhost:8080"

title DDS Web - Bauhaus Decision Dashboard
echo ============================================================
echo  DDS Web UI
echo  Project: %DDS_PROJECT%
echo  Service: Flask Web (Bauhaus interactive cards)
echo  URL    : %DDS_URL%
echo ============================================================
echo.

cd /d "%DDS_PROJECT%"

REM Detect Python launcher: prefer "python", fall back to "py -3"
set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
    where py >nul 2>nul && set "PYCMD=py -3"
)
if not defined PYCMD (
    echo [FAIL] Python not found in PATH. Install Python 3 and re-run.
    pause
    exit /b 1
)
echo [info] Using: %PYCMD%

REM Check .env
if exist ".env" (
    echo [info] .env loaded
) else (
    echo [warn] .env missing - run: copy .env.example .env  then fill API keys
)

REM Ensure core deps; auto-install if missing
%PYCMD% -c "import flask, duckdb, pandas, anthropic" >nul 2>nul || (
    echo [info] Installing dependencies from requirements.txt ...
    %PYCMD% -m pip install -r requirements.txt
)

echo.
echo [start] Flask starting - browser will open at %DDS_URL%
echo [stop ] Press Ctrl+C
echo.

REM Background: poll port, open browser as soon as Flask is ready (max 30s)
start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 30;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('localhost',8080);$c.Close();Start-Process '%DDS_URL%';break}catch{Start-Sleep -Milliseconds 800}}"

%PYCMD% -u app.py

echo.
echo [info] Service stopped
endlocal
pause
