# Start FRIDAY Python backend (fails fast if port 8000 is already taken)
$ErrorActionPreference = "Stop"
$BackendDir = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
Set-Location $BackendDir

$inUse = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"
if ($inUse) {
    Write-Host "Port 8000 is already in use. Another FRIDAY backend may be running." -ForegroundColor Yellow
    Write-Host "Run: .\scripts\stop-friday.ps1" -ForegroundColor Cyan
    exit 1
}

Write-Host "Starting FRIDAY backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" main.py