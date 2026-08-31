@echo off
REM Windows launcher for the beach flag predictor.
REM No virtualenv and no install step: the app is standard library only.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m beachflag %*
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m beachflag %*
    exit /b %errorlevel%
)

echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
exit /b 1
