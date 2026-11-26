# Starts the 3D maps GUI and opens it in the browser, unless one is already running.
#   - scans ports 8777..8787 for a GUI that already answers and just opens that one
#   - otherwise starts a new server on the first free port (hidden, logging to logs\gui-<port>.log)
#   - waits until it answers before opening the browser
# Options:  -Port 8777  -NoBrowser  -Foreground (run in this window, Ctrl+C stops it)
param([int]$Port = 8777, [switch]$NoBrowser, [switch]$Foreground)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$last = $Port + 10

function Test-Gui([int]$p) {         # is a map3d GUI answering on this port?
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$p/api/themes" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch { return $false }
}
function Test-Busy([int]$p) {        # is anything listening on this port?
    return [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}
function Open-Url([string]$url) {
    if ($NoBrowser) { return }
    Start-Process $url
}

# 1. A GUI is already running: reuse it
for ($p = $Port; $p -le $last; $p++) {
    if (Test-Gui $p) {
        Write-Host "A GUI is already running on port $p - opening it."
        Open-Url "http://127.0.0.1:$p/"
        exit 0
    }
}

# 2. First port nobody is listening on
$free = $null
for ($p = $Port; $p -le $last; $p++) { if (-not (Test-Busy $p)) { $free = $p; break } }
if ($null -eq $free) { Write-Host "No free port between $Port and $last."; exit 1 }
if ($free -ne $Port) { Write-Host "Port $Port is used by something else; using $free." }

$py = if (Test-Path '.venv\Scripts\python.exe') { (Resolve-Path '.venv\Scripts\python.exe').Path } else { 'python' }
$env:PYTHONUTF8 = '1'
$args = @('-m', 'map3d.gui', '--no-browser', '--port', "$free")

if ($Foreground) {
    Open-Url "http://127.0.0.1:$free/"
    & $py @args
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force logs | Out-Null
$log = "logs\gui-$free.log"
Start-Process -FilePath $py -ArgumentList $args -WindowStyle Hidden `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" | Out-Null

Write-Host -NoNewline "Starting the GUI on port $free"
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Gui $free) {
        Write-Host " - ready."
        Open-Url "http://127.0.0.1:$free/"
        Write-Host "Running in the background (log: $log). Stop it with stop_server.bat."
        exit 0
    }
    Write-Host -NoNewline '.'
    Start-Sleep -Milliseconds 500
}
Write-Host "`nThe GUI did not answer in 30 s. See $log and $log.err"
exit 1
