<#
.SYNOPSIS
    Generates a Val-d'Or map poster with no city/country text overlay.

.DESCRIPTION
    Wraps create_map_poster.py using the project's .venv, with --no-labels
    applied so the "VAL-D'OR / CANADA" text is omitted from the output.

.PARAMETER Theme
    Theme name from the themes/ folder. Default: midnight_blue

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

$ErrorActionPreference = "Stop"

try {
    # Always run from the script's own directory, regardless of caller's cwd
    $ProjectRoot = $PSScriptRoot
    Set-Location $ProjectRoot

    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

    if (Test-Path $VenvPython) {
        $Python = $VenvPython
    } else {
        Write-Warning "No .venv found at $VenvPython — falling back to system 'python'."
        $Python = "python"
    }

    & $Python "create_map_poster.py" `
        --city "Val-d'Or" `
        --country "Canada" `
        --theme $Theme `
        --distance $Distance `
        --width $Width `
        --height $Height `
        --no-labels

    if ($LASTEXITCODE -ne 0) {
        throw "Poster generation failed (exit code $LASTEXITCODE)."
    }

    Write-Host "`nDone. Check the posters\ folder for the output PNG." -ForegroundColor Green
}
catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
}
finally {
    Write-Host "`nPress any key to close..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
