@echo off
REM Make a map poster. Pass options straight through, or answer the prompts.
REM   launch.bat --city "Val-d'Or" --country Canada
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not "%~1"=="" goto :withargs
set /p CITY=City: 
set /p COUNTRY=Country: 
%PY% create_map_poster.py --city "%CITY%" --country "%COUNTRY%"
goto :done
:withargs
%PY% create_map_poster.py %*
:done
pause
