# Start FRIDAY desktop: stops stale processes, then launches Next.js + Electron
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

& "$PSScriptRoot\stop-friday.ps1"
Write-Host "Starting FRIDAY desktop..." -ForegroundColor Cyan
Set-Location (Join-Path $Root "frontend")
npm run dev:desktop