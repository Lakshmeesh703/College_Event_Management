@echo off
cd /d "%~dp0"

if not exist .env (
  echo Creating .env with SECRET_KEY only. Add DATABASE_URL or full DB_* values before running...
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

if "%DATABASE_URL%"=="" (
  if "%DB_USER%"=="" goto :db_missing
  if "%DB_PASSWORD%"=="" goto :db_missing
  if "%DB_HOST%"=="" goto :db_missing
  if "%DB_PORT%"=="" goto :db_missing
  if "%DB_NAME%"=="" goto :db_missing
)

set SECRET_KEY=dev-local-change-me

echo.
echo   Open: http://127.0.0.1:5000
echo   Stop: Ctrl+C
echo.

python -m backend.start
pause
goto :eof

:db_missing
echo Missing database configuration.
echo Set DATABASE_URL, or set all DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME before running.
pause
