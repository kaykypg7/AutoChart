# CloneHeroAutoChart - installer + portable-zip packager
# Run AFTER make_release.ps1 has produced dist\CloneHeroAutoChart.exe
# Usage (from project root):   .\build\make_installer.ps1
#
# Produces in release\:
#   CloneHeroAutoChart-Setup-<ver>.exe     (Inno Setup installer)
#   CloneHeroAutoChart-portable-<ver>.zip  (no-install portable)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = "1.1.0"

Write-Host "=== CloneHeroAutoChart release packager ===" -ForegroundColor Cyan
Write-Host "root: $root"
Write-Host "version: $version"

$exe = Join-Path $root "dist\CloneHeroAutoChart.exe"
if (-not (Test-Path $exe)) {
    Write-Error "dist\CloneHeroAutoChart.exe not found. Run .\build\make_release.ps1 first."
}

$release = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null

# -------- portable zip --------
Write-Host "[1/2] building portable zip..." -ForegroundColor Yellow
$portableDir = Join-Path $env:TEMP "chac-portable"
if (Test-Path $portableDir) { Remove-Item -Recurse -Force $portableDir }
$inner = Join-Path $portableDir "CloneHeroAutoChart"
New-Item -ItemType Directory -Force -Path $inner | Out-Null

Copy-Item $exe                           (Join-Path $inner "CloneHeroAutoChart.exe") -Force
Copy-Item (Join-Path $root "README.md")  (Join-Path $inner "README.md") -Force
Copy-Item (Join-Path $root "LICENSE")    (Join-Path $inner "LICENSE") -Force

@"
Clone Hero Auto-Charter - portable build $version

Just run CloneHeroAutoChart.exe. No install, no registry writes.
Settings and cache land under %LOCALAPPDATA%\CloneHeroAutoChart.
Output charts land in your Clone Hero Songs folder (configurable in Settings).

Full docs: README.md
"@ | Set-Content -Encoding UTF8 (Join-Path $inner "READ-ME-FIRST.txt")

$zipOut = Join-Path $release "CloneHeroAutoChart-portable-$version.zip"
if (Test-Path $zipOut) { Remove-Item -Force $zipOut }
Compress-Archive -Path (Join-Path $portableDir "CloneHeroAutoChart") -DestinationPath $zipOut -CompressionLevel Optimal
Write-Host "  wrote $zipOut ($('{0:N1}' -f ((Get-Item $zipOut).Length/1MB)) MB)"

# -------- inno setup installer --------
Write-Host "[2/2] compiling Inno Setup installer..." -ForegroundColor Yellow
$iscc = $null
$candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $iscc = $c; break } }
if (-not $iscc) {
    $onPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($onPath) { $iscc = $onPath.Source }
}

if (-not $iscc) {
    Write-Warning "ISCC.exe (Inno Setup compiler) not found."
    Write-Warning "Install from https://jrsoftware.org/isdl.php then re-run this script."
    Write-Warning "Portable zip is already written to release\ — ship that alone if you prefer."
    exit 0
}

$iss = Join-Path $root "build\installer.iss"
& $iscc $iss
if ($LASTEXITCODE -ne 0) { Write-Error "ISCC failed ($LASTEXITCODE)" }

$setupOut = Get-ChildItem $release -Filter "CloneHeroAutoChart-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setupOut) {
    Write-Host "  wrote $($setupOut.FullName) ($('{0:N1}' -f ($setupOut.Length/1MB)) MB)" -ForegroundColor Green
}

Write-Host ""
Write-Host "RELEASE READY in: $release" -ForegroundColor Green
Get-ChildItem $release | Format-Table Name, @{Name='MB';Expression={'{0:N1}' -f ($_.Length/1MB)}}, LastWriteTime
