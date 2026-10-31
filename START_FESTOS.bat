@echo off
title FestOS - Aarohan 2026 Control Room
color 0F

echo ======================================================================
echo    FESTOS: Aarohan 2026 Festival Control Operating System
echo    Epinoia 2026 Hackathon Submission - IIM Indore
echo ======================================================================
echo.

set PYTHONIOENCODING=utf-8

:: Check if Python is installed
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    py --version >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        python3 --version >nul 2>&1
        if %ERRORLEVEL% NEQ 0 (
            echo [ERROR] Python 3.9+ was not found on your system PATH.
            echo Please install Python 3.9 or higher from https://python.org
            echo and ensure "Add Python to PATH" is checked during setup.
            echo.
            pause
            exit /b 1
        ) else (
            set PY_CMD=python3
        )
    ) else (
        set PY_CMD=py -3
    )
) else (
    set PY_CMD=python
)

echo [OK] Python runtime detected.
echo [OK] Starting FestOS local control portal...
echo.
echo ----------------------------------------------------------------------
echo DEMO CREDENTIALS (All accounts use password: aarohan2026)
echo ----------------------------------------------------------------------
echo   * Festival Director : ST08  (Full access, overrides, budget, audit)
echo   * Hospitality       : ST02  (Rooming, accessibility, transport, diet)
echo   * Registration Desk : ST01  (Check-in, duplicate merges, waterfall)
echo   * Events Team       : ST03  (Schedule Gantt, heats, judge assignments)
echo   * Logistics Head    : ST04  (Inventory movement, equipment shortages)
echo   * Merchandise Desk  : ST05  (Issue desk, fair-share quotas)
echo   * Food Coordinator  : ST06  (Diet max-flow, compliance, settlement)
echo   * Volunteer         : ST07  (Help desk lookup; sensitive data masked)
echo ----------------------------------------------------------------------
echo [INFO] Your browser will open automatically at http://localhost:8000
echo [INFO] Press Ctrl+C in this console to terminate the server.
echo ======================================================================
echo.

cd /d "%~dp0"
%PY_CMD% run.py --port 8000

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [NOTE] Server terminated.
    pause
)
