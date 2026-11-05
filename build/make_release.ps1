# CloneHeroAutoChart - build script
# Usage: from project root run:   .\build\make_release.ps1
# Prereqs: Python 3.11 on PATH

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "=== CloneHeroAutoChart build ===" -ForegroundColor Cyan
Write-Host "root: $root"

# 1. venv
if (-not (Test-Path "$root\.venv")) {
    Write-Host "[1/5] creating venv..." -ForegroundColor Yellow
    python -m venv .venv
}
$venvPy = "$root\.venv\Scripts\python.exe"
$venvPip = "$root\.venv\Scripts\pip.exe"

# 2. deps
Write-Host "[2/5] installing deps..." -ForegroundColor Yellow
& $venvPy -m pip install --upgrade pip
& $venvPip install -r requirements.txt

# 3. ffmpeg
$resources = "$root\resources"
New-Item -ItemType Directory -Force -Path $resources | Out-Null
if (-not (Test-Path "$resources\ffmpeg.exe")) {
    Write-Host "[3/5] downloading ffmpeg (gyan.dev gpl essentials)..." -ForegroundColor Yellow
    $zip = "$env:TEMP\ffmpeg-release-essentials.zip"
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    $extract = "$env:TEMP\ffmpeg-extract"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive $zip -DestinationPath $extract -Force
    $binDir = Get-ChildItem "$extract" -Directory | Select-Object -First 1
    Copy-Item "$($binDir.FullName)\bin\ffmpeg.exe"  $resources -Force
    Copy-Item "$($binDir.FullName)\bin\ffprobe.exe" $resources -Force
    Write-Host "  ffmpeg installed to $resources"
} else {
    Write-Host "[3/5] ffmpeg already present." -ForegroundColor Green
}

# 4. clean
Write-Host "[4/5] cleaning previous build..." -ForegroundColor Yellow
if (Test-Path "$root\build\dist") { Remove-Item -Recurse -Force "$root\build\dist" }
if (Test-Path "$root\dist") { Remove-Item -Recurse -Force "$root\dist" }
if (Test-Path "$root\build\build")  { Remove-Item -Recurse -Force "$root\build\build" }

# 5. pyinstaller
Write-Host "[5/5] running PyInstaller..." -ForegroundColor Yellow
& $venvPy -m PyInstaller --noconfirm --clean "$root\build\build.spec"

Write-Host ""
Write-Host "DONE. Executable at: $root\dist\CloneHeroAutoChart.exe" -ForegroundColor Green
