@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title School Bus Tracker - Stop

echo ============================================================
echo   School Bus Tracker - stopping
echo ============================================================
echo.

set "STOPPED=0"

if exist "instance\server.pid" (
    set /p SERVER_PID=<"instance\server.pid"
    if not "!SERVER_PID!"=="" (
        echo [SERVER] Stopping PID !SERVER_PID!...
        taskkill /PID !SERVER_PID! /F >nul 2>&1
        if not errorlevel 1 set "STOPPED=1"
    )
    del /q "instance\server.pid" >nul 2>&1
) else (
    echo [SERVER] No instance\server.pid file. The server was not started by start.bat.
)

if exist "instance\tunnel.pid" (
    set /p TUNNEL_PID=<"instance\tunnel.pid"
    if not "!TUNNEL_PID!"=="" (
        echo [TUNNEL] Stopping PID !TUNNEL_PID!...
        taskkill /PID !TUNNEL_PID! /F >nul 2>&1
        if not errorlevel 1 set "STOPPED=1"
    )
    del /q "instance\tunnel.pid" >nul 2>&1
) else (
    echo [TUNNEL] No instance\tunnel.pid file. The tunnel was not started by start.bat.
)

echo.
if "!STOPPED!"=="0" (
    echo Nothing was stopped. It may already be offline.
) else (
    echo Application stopped.
)
echo Note: an active trip stays in the database. After restarting, the driver can
echo end it, or the admin can use "End trip" on the Buses page.
echo ============================================================
pause
endlocal
