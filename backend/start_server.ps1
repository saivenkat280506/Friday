$inUse = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"
if ($inUse) {
    Write-Host "Port 8000 is already in use. Run ..\scripts\stop-friday.ps1 first." -ForegroundColor Yellow
    exit 1
}

& ".venv\Scripts\python.exe" main.py
