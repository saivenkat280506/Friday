# Remove CMD popups from old FRIDAY F12 install (watchdog, agent, IFEO cmd chain).
# Run once (admin recommended for IFEO registry fix):
#   powershell -ExecutionPolicy Bypass -File "C:\Users\saivenkat\Downloads\FRIDAY\scripts\fix-f12-popups.ps1"

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FridayDir = Join-Path $env:PROGRAMDATA "FRIDAY"
$HiddenVbs = Join-Path $ScriptDir "run-hidden.vbs"
$RedirectPs1 = Join-Path $ScriptDir "friday-myasus-redirect.ps1"
$RedirectDest = Join-Path $FridayDir "friday-myasus-redirect.ps1"
$SummonSrc = Join-Path $ScriptDir "summon-friday-companion.ps1"
$SummonDest = Join-Path $FridayDir "summon-friday-companion.ps1"
$HiddenVbsDest = Join-Path $FridayDir "run-hidden.vbs"
$TaskNames = @("FRIDAY-F12-Watchdog", "FRIDAY-F12-Hotkey")
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunValueName = "FRIDAY F12 Hotkey"
$IfeoTargets = @("AsusMyASUS.exe", "AsusHotkey.exe")
$IfeoRoot = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Stop-F12Processes {
    $n = 0
    foreach ($name in @("FridayF12Agent", "FridayF12Intercept")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            $n++
        }
    }
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'f12-hotkey-agent\.ps1|summon-friday-companion\.ps1' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $n++
        }
    return $n
}

function Remove-F12Tasks {
    foreach ($task in $TaskNames) {
        schtasks /Delete /TN $task /F 2>$null | Out-Null
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Install-SilentRedirectScripts {
    if (-not (Test-Path $FridayDir)) {
        New-Item -ItemType Directory -Path $FridayDir -Force | Out-Null
    }
    if (Test-Path $SummonSrc) { Copy-Item $SummonSrc $SummonDest -Force }
    if (Test-Path $RedirectPs1) { Copy-Item $RedirectPs1 $RedirectDest -Force }
    if (Test-Path $HiddenVbs) { Copy-Item $HiddenVbs $HiddenVbsDest -Force }
}

function Set-SilentIfeo {
    if (-not (Test-IsAdmin)) { return $false }
    if (-not (Test-Path $RedirectDest)) { return $false }
    $debugger = "wscript.exe //B //Nologo `"$HiddenVbsDest`" `"$RedirectDest`""
    foreach ($name in $IfeoTargets) {
        $key = Join-Path $IfeoRoot $name
        if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
        New-ItemProperty -Path $key -Name "Debugger" -Value $debugger -PropertyType String -Force | Out-Null
    }
    return $true
}

Write-Host "Stopping FRIDAY F12 background processes..." -ForegroundColor Cyan
$stopped = Stop-F12Processes
Write-Host "  Stopped $stopped process(es)" -ForegroundColor Green

Write-Host "Removing watchdog / logon scheduled tasks..." -ForegroundColor Cyan
Remove-F12Tasks
Remove-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction SilentlyContinue
Remove-Item (Join-Path $FridayDir "f12-agent-installed") -Force -ErrorAction SilentlyContinue
Write-Host "  Scheduled tasks and startup entry removed" -ForegroundColor Green

Write-Host "Installing silent redirect scripts..." -ForegroundColor Cyan
Install-SilentRedirectScripts
Write-Host "  Copied to $FridayDir" -ForegroundColor Green

if (Test-IsAdmin) {
    if (Set-SilentIfeo) {
        Write-Host "IFEO updated to silent launcher (no CMD window)" -ForegroundColor Green
    }
} else {
    Write-Host "IFEO not updated (need Administrator). Re-run this script as admin to finish." -ForegroundColor Yellow
    Write-Host "  Right-click PowerShell -> Run as administrator, then:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. CMD popups from FRIDAY F12 watchdog/agent should be gone." -ForegroundColor Green
Write-Host "F12 works inside FRIDAY while the app is running (no background agent)." -ForegroundColor Cyan