@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=python"
set "PIDFILE=%ROOT%state\main.pid"

%PYTHON% -c "import psutil, dotenv, requests" >nul 2>&1
if errorlevel 1 (
    echo Installing missing dependencies...
    %PYTHON% -m pip install psutil python-dotenv requests -q
)

set "ALREADY_RUNNING=0"
if exist "%PIDFILE%" (
    set /p STORED_PID=<"%PIDFILE%"
    tasklist /FI "PID eq %STORED_PID%" /FI "IMAGENAME eq python.exe" 2>NUL | find /I "python.exe" >NUL
    if not errorlevel 1 set "ALREADY_RUNNING=1"
)

if "%ALREADY_RUNNING%"=="1" (
    echo Service already running  pid=%STORED_PID%
    echo Results: %ROOT%results.html
) else (
    echo Starting commit-skill-runner...
    start "commit-skill-runner" /min "%PYTHON%" "%ROOT%Scripts\main.py"
    echo Bot started in background window.
    timeout /t 2 /nobreak >nul
    start "" "http://localhost:8099"
    echo Results page opened in browser.
)
endlocal
