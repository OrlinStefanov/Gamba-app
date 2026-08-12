@echo off
REM Windows launcher. First run creates .venv and installs dependencies.
setlocal

cd /d "%~dp0"

if not exist ".venv" (
  echo Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

if "%ANTHROPIC_API_KEY%"=="" (
  echo.
  echo ANTHROPIC_API_KEY is not set.
  echo Get a key at https://console.anthropic.com/settings/keys then run:
  echo     setx ANTHROPIC_API_KEY sk-ant-...
  echo and open a new terminal.
  echo.
)

".venv\Scripts\python.exe" -m gamba %*
goto :eof

:nopython
echo Python 3.10+ is required. Install it from https://python.org
exit /b 1

:fail
echo Dependency installation failed.
exit /b 1
