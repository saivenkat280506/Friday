# Stop all FRIDAY desktop processes (backend, frontend, electron)
$ErrorActionPreference = "SilentlyContinue"

function Stop-PortListener([int]$Port) {
    $pids = @()
    netstat -ano | ForEach-Object {
        if ($_ -match "LISTENING" -and $_ -match ":$Port\s") {
            $parts = $_ -split "\s+"
            $procId = $parts[-1]
            if ($procId -match "^\d+$") { $pids += [int]$procId }
        }
    }
    foreach ($procId in ($pids | Select-Object -Unique)) {
        Write-Host "Stopping PID $procId on port $Port"
        taskkill /PID $procId /F 2>$null | Out-Null
    }
}

Write-Host "Stopping FRIDAY..." -ForegroundColor Yellow
Stop-PortListener 8000
Stop-PortListener 3000

# Keep the Alt+Space companion hotkey agent running so cold-start still works.

Get-Process -Name electron -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping electron (PID $($_.Id))"
    Stop-Process -Id $_.Id -Force
}

Start-Sleep -Seconds 1
Write-Host "FRIDAY stopped." -ForegroundColor Green