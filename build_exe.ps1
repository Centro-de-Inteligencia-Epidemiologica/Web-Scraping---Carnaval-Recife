# Build InstaEpi Monitor into a Windows executable (Defender-friendly).
#
# Usage:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# Produces: dist\InstaEpiMonitor\InstaEpiMonitor.exe  (onedir bundle)
#
# Anti-false-positive measures (see InstaEpiMonitor.spec):
#   * onedir build, NOT onefile  -> avoids self-extracting stub heuristic
#   * UPX disabled               -> packed binaries are aggressively flagged
#   * embedded version metadata  -> unsigned + no metadata looks suspicious
#
# The Chromium browser is NOT bundled; the app uses the per-user Playwright
# cache. On a fresh machine run once:  python -m playwright install chromium

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
python -m pip install --quiet pyinstaller -r requirements.txt

Write-Host "Cleaning previous build..." -ForegroundColor Cyan
if (Test-Path dist)       { Remove-Item dist -Recurse -Force }
if (Test-Path build_tmp)  { Remove-Item build_tmp -Recurse -Force }

Write-Host "Building..." -ForegroundColor Cyan
python -m PyInstaller InstaEpiMonitor.spec --noconfirm --distpath dist --workpath build_tmp

$exe = "dist\InstaEpiMonitor\InstaEpiMonitor.exe"
if (Test-Path $exe) {
    Write-Host "`nBuild OK -> $exe" -ForegroundColor Green
    Write-Host "Run it, or zip the dist\InstaEpiMonitor folder to distribute." -ForegroundColor Green
} else {
    Write-Host "`nBuild FAILED - exe not found." -ForegroundColor Red
    exit 1
}
