<#
.SYNOPSIS
  Builds the 3D map as a static site and publishes it to villevd.ca/carte on Cloudflare.

.DESCRIPTION
  1. checks gh (GitHub CLI) is signed in and the repository, workflow and `production` environment exist
  2. asks for the two Cloudflare secrets if the environment does not have them yet (the values are never shown or stored)
  3. exports the baked map (.db) to site/carte (map3d.static_site)
  4. zips it as carte-bundle.zip and attaches it to a new GitHub release
  5. runs the "Deploy carte" workflow with that release and waits for it to finish

  The release is on a public repository, so the bundle becomes publicly downloadable: the script asks before it
  publishes anything (use -Yes to skip the question).

.EXAMPLE
  .\scripts\publish-carte.ps1
  .\scripts\publish-carte.ps1 -Map "posters\val-d'or_gradient_roads_3d_20261124_091206.db" -NoSatellite -Yes
#>
[CmdletBinding()]
param(
    # Baked map to publish. Default: the newest .db in posters/.
    [string]$Map,
    # Release tag. Default: carte-<date and time>.
    [string]$Tag = ("carte-" + (Get-Date -Format "yyyyMMdd-HHmm")),
    # Skip the Esri satellite pictures (smaller and faster; the ground then has no satellite picture).
    [switch]$NoSatellite,
    # Do not ask for confirmation before publishing.
    [switch]$Yes,
    # Repository that holds the workflow.
    [string]$Repo = "villevd/vvd-map-to-poster"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }
function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$exe $($arguments -join ' ') failed (exit code $LASTEXITCODE)" }
}

# ---------------------------------------------------------------- 1. checks
Step "Checking GitHub access"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Fail "GitHub CLI (gh) is not installed: https://cli.github.com" }
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { Fail "gh is not signed in: run 'gh auth login' first" }
$onMain = (git rev-parse --abbrev-ref HEAD).Trim() -eq "main"
if (-not $onMain) { Write-Warning "You are not on the main branch; the workflow only deploys from main." }
$unpushed = (git log origin/main..main --oneline 2>$null)
if ($unpushed) { Write-Warning "Commits not pushed yet (the workflow runs the version on GitHub):`n$unpushed" }

gh workflow view deploy-carte.yml --repo $Repo *> $null
if ($LASTEXITCODE -ne 0) { Fail "Workflow deploy-carte.yml not found in $Repo. Push the latest main first." }

# The environment is created if it is missing (needs admin rights on the repository; harmless when it exists)
gh api -X PUT "repos/$Repo/environments/production" *> $null
$have = @()
$secretNames = gh api "repos/$Repo/environments/production/secrets" --jq ".secrets[].name" 2>$null
if ($LASTEXITCODE -eq 0 -and $secretNames) { $have = @($secretNames) }

foreach ($name in "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID") {
    if ($have -contains $name) { Write-Host "  secret ${name}: present"; continue }
    Write-Host "  secret ${name}: missing from the 'production' environment" -ForegroundColor Yellow
    Write-Host "  Use the same value as vvd-calendrier. Token permissions: Workers Scripts (edit) and, on the villevd.ca zone, Workers Routes (edit)."
    $secure = Read-Host "  Paste $name (input is hidden, Enter to skip)" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($plain) {
        $plain | gh secret set $name --env production --repo $Repo
        if ($LASTEXITCODE -ne 0) { Fail "Could not save $name (admin rights are needed on the repository)" }
        Write-Host "  secret ${name}: saved"
    } else {
        Write-Warning "$name skipped: the deploy will fail until it is set (Settings, Environments, production)."
    }
    $plain = $null
}

# ---------------------------------------------------------------- 2. export
Step "Exporting the map"
if (-not $Map) {
    $latest = Get-ChildItem "posters" -Filter *.db -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { Fail "No baked map (.db) in posters/. Pass one with -Map." }
    $Map = $latest.FullName
}
if (-not (Test-Path $Map)) { Fail "Map not found: $Map" }
$dbItem = Get-Item $Map
Write-Host ("  map: {0} ({1:N0} MB)" -f $dbItem.Name, ($dbItem.Length / 1MB))

$site = Join-Path $root "site\carte"
if (Test-Path $site) { Remove-Item $site -Recurse -Force }
$exportArgs = @("-m", "map3d.static_site", $dbItem.FullName, $site)
if ($NoSatellite) { $exportArgs += "--no-satellite" }
Run python $exportArgs

$all = Get-ChildItem $site -Recurse -File
$files = @{ Count = $all.Count }
Write-Host ("  {0} files, {1:N0} MB" -f $all.Count, (($all | Measure-Object Length -Sum).Sum / 1MB))
if ($files.Count -gt 20000) { Fail "$($files.Count) files: over Cloudflare's 20,000 file limit on the free plan. Run again with -NoSatellite." }
$tooBig = Get-ChildItem $site -Recurse -File | Where-Object { $_.Length -gt 25MB }
if ($tooBig) { Fail ("File over Cloudflare's 25 MiB limit: " + ($tooBig.FullName -join ", ")) }

# ---------------------------------------------------------------- 3. zip
Step "Packing carte-bundle.zip"
$zip = Join-Path $root "carte-bundle.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $site "*") -DestinationPath $zip -CompressionLevel NoCompression   # tiles are already compressed
Write-Host ("  {0:N0} MB" -f ((Get-Item $zip).Length / 1MB))

# ---------------------------------------------------------------- 4. publish
Step "Ready to publish"
Write-Host "  Release $Tag on $Repo (public: anyone can download the map bundle)"
Write-Host "  then deploy to https://villevd.ca/carte/ through Cloudflare"
if (-not $Yes) {
    $answer = Read-Host "  Continue? (y/N)"
    if ($answer -notmatch '^(y|yes|o|oui)$') { Write-Host "Stopped. Nothing was published. The bundle is in $zip"; exit 0 }
}

Step "Creating the release"
Run gh @("release", "create", $Tag, $zip, "--repo", $Repo, "--title", "Carte $Tag", "--notes", "3D map bundle for villevd.ca/carte (built from $($dbItem.Name))")

Step "Starting the deploy workflow"
Run gh @("workflow", "run", "deploy-carte.yml", "--repo", $Repo, "--ref", "main", "-f", "release_tag=$Tag")
Start-Sleep -Seconds 8
$runId = gh run list --repo $Repo --workflow deploy-carte.yml --limit 1 --json databaseId --jq ".[0].databaseId"
if (-not $runId) { Fail "Workflow started, but its run was not found: check https://github.com/$Repo/actions" }
Write-Host "  https://github.com/$Repo/actions/runs/$runId"
gh run watch $runId --repo $Repo --exit-status
if ($LASTEXITCODE -ne 0) { Fail "The deploy failed: open the run above and read the failing step." }

Step "Done"
Write-Host "  https://villevd.ca/carte/"
Write-Host "  Embed: <iframe src=`"/carte/index.html?embed=1`" title=`"Carte 3D`" loading=`"lazy`" style=`"width:100%;height:80vh;border:0`"></iframe>"
