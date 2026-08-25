# Restore laptop Fn keys (volume, brightness, F1-F12) by removing FRIDAY hotkey hooks.
# Run now (no admin needed for listener removal):
#   powershell -ExecutionPolicy Bypass -File "C:\Users\saivenkat\Downloads\FRIDAY\scripts\restore-keyboard.ps1"
# For full MyASUS IFEO cleanup, re-run as Administrator.

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StopScript = Join-Path $ScriptDir "stop-f12-agent.ps1"
$IfeoTargets = @("AsusMyASUS.exe", "AsusHotkey.exe")
$IfeoRoot = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "Restoring keyboard / Fn key behavior..." -ForegroundColor Cyan

if (Test-Path $StopScript) {
    & $StopScript
}

# Overwrite ProgramData copies so logon cannot restart the old hook agent
$FridayDir = Join-Path $env:PROGRAMDATA "FRIDAY"
if (Test-Path $FridayDir) {
    $stub = Join-Path $ScriptDir "f12-hotkey-agent.ps1"
    if (Test-Path $stub) {
        Copy-Item $stub (Join-Path $FridayDir "f12-hotkey-agent.ps1") -Force -ErrorAction SilentlyContinue
    }
    $vbs = Join-Path $ScriptDir "start-listener.vbs"
    if (Test-Path $vbs) {
        Copy-Item $vbs (Join-Path $FridayDir "start-listener.vbs") -Force -ErrorAction SilentlyContinue
    }
}

# Kill any compiled agent binaries
foreach ($name in @("FridayF12Agent", "FridayF12Intercept")) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

# Stop FRIDAY backend keyboard hooks if running (port 8000)
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:8000/companion/dismiss" -Method POST -TimeoutSec 2 -UseBasicParsing
} catch {}

if (Test-IsAdmin) {
    foreach ($target in $IfeoTargets) {
        $key = Join-Path $IfeoRoot $target
        if (Test-Path $key) {
            Remove-ItemProperty -Path $key -Name "Debugger" -ErrorAction SilentlyContinue
            Write-Host "  Removed IFEO redirect for $target" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  Tip: run as Administrator to also clear MyASUS IFEO redirects." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Volume, brightness, and Fn keys should work again." -ForegroundColor Green
Write-Host "F12 opens companion only while FRIDAY is running (npm run dev:desktop)." -ForegroundColor Cyan