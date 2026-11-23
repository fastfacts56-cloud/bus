@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title School Bus Tracker - Start

echo ============================================================
echo   School Bus Tracker - starting
echo ============================================================
echo.

set "PYTHON=.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python virtual environment not found.
    echo.
    echo Run these commands in the project folder first:
    echo   py -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "logs" mkdir "logs"
if not exist "instance" mkdir "instance"

if not exist ".env" (
    echo [SETUP] No .env file found. Creating one with random secrets...
    "%PYTHON%" -c "import config"
    if errorlevel 1 (
        echo [ERROR] Could not create .env. See the message above.
        pause
        exit /b 1
    )
    echo.
)

"%PYTHON%" -c "import flask, waitress, pywebpush, requests" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python dependencies are missing.
    echo Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- server
set "SERVER_RUNNING=0"
if exist "instance\server.pid" (
    set /p SERVER_PID=<"instance\server.pid"
    if not "!SERVER_PID!"=="" (
        tasklist /FI "PID eq !SERVER_PID!" 2>nul | find /I "python.exe" >nul
        if not errorlevel 1 set "SERVER_RUNNING=1"
    )
)

if "!SERVER_RUNNING!"=="1" (
    echo [SERVER] Already running with PID !SERVER_PID!. Not starting a duplicate.
) else (
    if exist "instance\server.pid" del /q "instance\server.pid" >nul 2>&1
    echo [SERVER] Starting Flask with Waitress...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '%PYTHON%' -ArgumentList 'run_server.py' -WorkingDirectory '%CD%' -WindowStyle Minimized -RedirectStandardOutput 'logs\server.log' -RedirectStandardError 'logs\server-error.log' -PassThru"
    timeout /t 4 /nobreak >nul

    "%PYTHON%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=6)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] The server did not start correctly.
        echo Check logs\server.log and logs\server-error.log.
        echo Common causes: port 8000 already used, missing .env, or missing packages.
        echo.
        pause
        exit /b 1
    )
    echo [SERVER] Running at http://127.0.0.1:8000
)

REM ---------------------------------------------------------------- tunnel
set "TUNNEL_RUNNING=0"
if exist "instance\tunnel.pid" (
    set /p TUNNEL_PID=<"instance\tunnel.pid"
    if not "!TUNNEL_PID!"=="" (
        tasklist /FI "PID eq !TUNNEL_PID!" 2>nul | find /I "cloudflared.exe" >nul
        if not errorlevel 1 set "TUNNEL_RUNNING=1"
    )
)

if not exist "cloudflared\cloudflared.exe" (
    echo [TUNNEL] cloudflared.exe not found in cloudflared\.
    echo          The local site works, but the public website will be offline.
    echo          See DETAIL.md section 14 for the download and setup.
) else if not exist "cloudflared\config.yml" (
    echo [TUNNEL] cloudflared\config.yml not found.
    echo          Copy cloudflared\config.yml.example to config.yml and fill in
    echo          your tunnel ID and credentials file. See DETAIL.md section 14.
) else if "!TUNNEL_RUNNING!"=="1" (
    echo [TUNNEL] Already running with PID !TUNNEL_PID!. Not starting a duplicate.
) else (
    echo [TUNNEL] Starting Cloudflare Tunnel...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath 'cloudflared\cloudflared.exe' -ArgumentList 'tunnel','--config','cloudflared\config.yml','run' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardOutput 'logs\cloudflared.log' -RedirectStandardError 'logs\cloudflared-error.log' -PassThru; if ($p) { $p.Id | Set-Content 'instance\tunnel.pid' }"
    timeout /t 3 /nobreak >nul
    echo [TUNNEL] Started. Check logs\cloudflared-error.log if the public site is down.
)

echo.
for /f "tokens=1,* delims==" %%A in ('findstr /B "PUBLIC_BASE_URL=" ".env"') do set "PUBLIC_URL=%%B"
if defined PUBLIC_URL (
    echo Public URL from .env: !PUBLIC_URL!
) else (
    echo Public URL: set PUBLIC_BASE_URL in .env
)
echo Local URL: http://127.0.0.1:8000
echo.
echo Keep this PC and its internet connection on while school transport runs.
echo Use stop.bat to stop the server and tunnel.
echo ============================================================
pause
endlocal
