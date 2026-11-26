@echo off
REM Start the 3D maps GUI and open it in your browser.
REM Reuses a GUI that is already running (ports 8777-8787) instead of starting a second one.
REM   start_server.bat              start or reuse, open the browser
REM   start_server.bat -NoBrowser   start or reuse, do not open the browser
REM   start_server.bat -Foreground  run in this window (Ctrl+C stops it)
REM   start_server.bat -Port 9000   look from another port
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_server.ps1" %*
if errorlevel 1 pause
