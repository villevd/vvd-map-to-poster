@echo off
REM Stop every 3D maps GUI started on ports 8777-8787 (only processes that answer as a map3d GUI).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$n=0; 8777..8787 | ForEach-Object { $p=$_; try { $r=Invoke-WebRequest \"http://127.0.0.1:$p/api/themes\" -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; $n++; Write-Host \"Stopped the GUI on port $p\" } } } catch {} }; if ($n -eq 0) { Write-Host 'No GUI is running.' }"
timeout /t 3 >nul
