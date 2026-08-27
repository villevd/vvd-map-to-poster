<#
.SYNOPSIS
    Generates a Val-d'Or map poster with no city/country text overlay.

.DESCRIPTION
    Wraps create_map_poster.py using the project's .venv, with --no-labels
    applied so the "VAL-D'OR / CANADA" text is omitted from the output.

.PARAMETER Theme
    Theme name from the themes folder. Default: midnight_blue

.PARAMETER Distance
    Map radius in meters. Default: 18000

.PARAMETER Width
    Poster width in inches. Default: 12

.PARAMETER Height
    Poster height in inches. Default: 16

.EXAMPLE
    .\generate_valdor_no_text.ps1
    .\generate_valdor_no_text.ps1 -Theme noir -Distance 12000
#>

param(
    [string]$Theme = "midnight_blue",
    [int]$Distance = 18000,
    [double]$Width = 12,
    [double]$Height = 16
)

# NOTE: intentionally not setting $ErrorActionPreference = "Stop" here.
# Windows PowerShell treats a native exe's stderr output as a terminating
# error under "Stop", which cut off python's real traceback last time.

$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    Write-Warning "No .venv found at $VenvPython - falling back to system 'python'."
    $Python = "python"
}

$CityName = "Val-d'Or"

$PyArgs = @(
    "create_map_poster.py",
    "--city", $CityName,
    "--country", "Canada",
    "--theme", $Theme,
    "--distance", $Distance,
    "--width", $Width,
    "--height", $Height,
    "--no-labels"
)

& $Python $PyArgs
$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -ne 0) {
    Write-Host "Poster generation failed (exit code $ExitCode). See output above for details." -ForegroundColor Red
} else {
    Write-Host "Done. Check the posters folder for the output PNG." -ForegroundColor Green
}

Write-Host ""
Read-Host "Press Enter to close"
