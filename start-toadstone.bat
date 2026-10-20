@echo off

if not exist data mkdir data

rem One port for the message, the browser and the server alike.
if "%PORT%"=="" set PORT=5000
set EXTERNAL_PORT=%PORT%

echo Checking for Docker...
docker --version 2>nul
if errorlevel 1 goto nodocker

echo Docker found! Starting Toadstone...
docker compose up -d
timeout /t 3 /nobreak >nul

echo.
echo ================================================================================
echo Toadstone is running!
echo Opening browser to http://localhost:%PORT%
echo.
echo Press Ctrl+C to stop Toadstone
echo DO NOT close this window or Toadstone will keep running!
echo ================================================================================
echo.

start http://localhost:%PORT%
docker compose logs -f
docker compose down
exit /b

:nodocker
echo Docker not found. Checking for Python...
python --version 2>nul
if errorlevel 1 goto trypy

:foundpython
echo Python found! Checking dependencies...
pip show flask 2>nul
if errorlevel 1 goto installdeps

:rundirect
echo Starting Toadstone...
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:%PORT%"
python main.py -l --port %PORT%
exit /b

:trypy
py --version 2>nul
if errorlevel 1 goto nopython
set PYTHON=py
goto foundpython

:installdeps
echo Installing dependencies...
pip install -r requirements.txt
playwright install chromium
goto rundirect

:nopython
echo.
echo ERROR: Neither Docker nor Python found!
echo.
echo Please install one of:
echo - Docker Desktop: https://www.docker.com/products/docker-desktop
echo - Python 3.11+: https://www.python.org/downloads/
echo.
pause
exit /b 1
