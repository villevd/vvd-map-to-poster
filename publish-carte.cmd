@echo off
rem Publish the 3D map to villevd.ca/carte in one go: export, zip, GitHub release, deploy workflow.
rem Double-click it, or pass options through, e.g.:  publish-carte.cmd -NoSatellite
rem It uses the newest .db in posters\ unless you pass -Map "posters\your-map.db".
rem Needs: gh (signed in), Python with the map3d requirements. Details: docs\deploy-carte.md
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish-carte.ps1" -Yes %*
echo.
if errorlevel 1 (echo Publishing failed. Read the message above.) else (echo Publishing finished.)
pause
