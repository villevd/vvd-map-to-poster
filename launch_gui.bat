@echo off
REM Open the 3D city maps GUI in your browser (Ctrl+C in this window to stop it).
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% -m map3d.gui %*
pause
