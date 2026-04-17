@echo off
cd /d "%~dp0"

if not exist .env (
  echo Creating .env for local SQLite...
  echo USE_SQLITE_LOCAL=1> .env
  echo SECRET_KEY=dev-local-change-me>> .env
)

if not exist .venv\Scripts\python.exe (
  echo First-time setup: creating venv and installing packages...
  py -3 -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -q --upgrade pip
  pip install -q -r requirements.txt
  echo Setup done.
) else (
  call .venv\Scripts\activate.bat
)

set USE_SQLITE_LOCAL=1
set SECRET_KEY=dev-local-change-me

echo.
echo   Open: http://127.0.0.1:5000
echo   Stop: Ctrl+C
echo.

python app.py
pause
