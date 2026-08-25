# Rewire ASUS F12 (MyASUS) -> FRIDAY companion while FRIDAY is running.
#
# Run once as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\rewire-f12-friday.ps1
#
# What this does:
#   1. IFEO: AsusMyASUS.exe / AsusHotkey.exe -> silent FRIDAY summon (no CMD flash)
#   2. Removes the old always-on FridayF12Agent background process + logon hooks
#   3. F12 is handled by FRIDAY itself while the app is open (backend + Electron)
#
# Undo:
#   powershell -ExecutionPolicy Bypass -File scripts\rewire-f12-friday.ps1 -Undo

[CmdletBinding()]
param(
    [switch]$Hard,
    [switch]$Undo
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$RedirectPs1 = Join-Path $ScriptDir "friday-myasus-redirect.ps1"
$SummonSrc = Join-Path $ScriptDir "summon-friday-companion.ps1"
$StopAgentPs1 = Join-Path $ScriptDir "stop-f12-agent.ps1"
$TaskNames = @("FRIDAY-F12-Hotkey", "FRIDAY-F12-Watchdog")
$HiddenVbs = Join-Path $ScriptDir "run-hidden.vbs"
$HiddenVbsDest = Join-Path $FridayDataDir "run-hidden.vbs"
$RunValueName = "FRIDAY F12 Hotkey"
$IfeoTargets = @(
    "AsusMyASUS.exe",
    "AsusHotkey.exe"
)
$IfeoRoot = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$FridayDataDir = Join-Path $env:PROGRAMDATA "FRIDAY"
$SummonDest = Join-Path $FridayDataDir "summon-friday-companion.ps1"
$RedirectDest = Join-Path $FridayDataDir "friday-myasus-redirect.ps1"

function Show-F12SetupBalloon([string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        [System.Media.SystemSounds]::Asterisk.Play()
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.Visible = $true
        $notify.BalloonTipTitle = "F.R.I.D.A.Y."
        $notify.BalloonTipText = $Text
        $notify.ShowBalloonTip(6000)
        Start-Sleep -Seconds 2
        $notify.Dispose()
    } catch {}
}

function Install-FridayRedirectScripts {
    if (-not (Test-Path $FridayDataDir)) {
        New-Item -ItemType Directory -Path $FridayDataDir -Force | Out-Null
    }
    if (Test-Path $SummonSrc) {
        Copy-Item -Path $SummonSrc -Destination $SummonDest -Force
    }
    if (Test-Path $RedirectPs1) {
        Copy-Item -Path $RedirectPs1 -Destination $RedirectDest -Force
    }
    if (Test-Path $HiddenVbs) {
        Copy-Item -Path $HiddenVbs -Destination $HiddenVbsDest -Force
    }
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-IfeoRedirect {
    if (-not (Test-Path $RedirectDest)) {
        throw "Missing redirect script: $RedirectDest"
    }
    if (-not (Test-Path $HiddenVbsDest)) {
        throw "Missing silent launcher: $HiddenVbsDest"
    }
    $debugger = "wscript.exe //B //Nologo `"$HiddenVbsDest`" `"$RedirectDest`""
    foreach ($name in $IfeoTargets) {
        $key = Join-Path $IfeoRoot $name
        if (-not (Test-Path $key)) {
            New-Item -Path $key -Force | Out-Null
        }
        New-ItemProperty -Path $key -Name "Debugger" -Value $debugger -PropertyType String -Force | Out-Null
        Write-Host "  IFEO Debugger set for $name" -ForegroundColor Green
    }
}

function Remove-IfeoRedirect {
    foreach ($name in $IfeoTargets) {
        $key = Join-Path $IfeoRoot $name
        if (Test-Path $key) {
            Remove-ItemProperty -Path $key -Name "Debugger" -ErrorAction SilentlyContinue
            Write-Host "  IFEO Debugger removed for $name" -ForegroundColor Yellow
        }
    }
}

function Remove-F12AgentAutostart {
    if (Test-Path $StopAgentPs1) {
        & $StopAgentPs1 | Out-Null
        Write-Host "  F12 background agent stopped and autostart removed" -ForegroundColor Green
        return
    }

    Remove-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction SilentlyContinue
    foreach ($task in $TaskNames) {
        schtasks /Delete /TN $task /F 2>$null | Out-Null
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    }
    foreach ($name in @("FridayF12Agent", "FridayF12Intercept")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'f12-hotkey-agent\.ps1' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "  F12 background agent cleanup complete" -ForegroundColor Green
}

function Stop-MyAsusUi {
    foreach ($n in @("AsusMyASUS", "MyASUS")) {
        Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "  Stopping process $($_.ProcessName) (pid $($_.Id))"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Set-HardAsusServices([bool]$Disable) {
    $services = @(
        "ASUSOptimization",
        "AsusAppService",
        "ASUSSystemAnalysis",
        "ASUSSoftwareManager"
    )
    foreach ($name in $services) {
        $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $svc) { continue }
        if ($Disable) {
            if ($svc.Status -eq "Running") {
                Write-Host "  Stopping service: $name"
                Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
            }
            if ($svc.StartType -ne "Disabled") {
                Write-Host "  Disabling service: $name"
                Set-Service -Name $name -StartupType Disabled -ErrorAction SilentlyContinue
            }
        } else {
            if ($svc.StartType -eq "Disabled") {
                Write-Host "  Re-enabling service: $name (Manual)"
                Set-Service -Name $name -StartupType Manual -ErrorAction SilentlyContinue
            }
        }
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────
if (-not (Test-IsAdmin)) {
    Write-Host "This script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as administrator, then:" -ForegroundColor Yellow
    Write-Host "  cd `"$Root`""
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    exit 1
}

if ($Undo) {
    Write-Host "Restoring MyASUS / removing FRIDAY F12 mapping..." -ForegroundColor Cyan
    Remove-IfeoRedirect
    Remove-F12AgentAutostart
    if ($Hard) {
        Set-HardAsusServices $false
    }
    Write-Host "Done." -ForegroundColor Green
    exit 0
}

Write-Host "Rewiring F12 -> FRIDAY companion (no background agent)..." -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/4] Installing F12 listener (cold-start + silent)..."
$InstallListener = Join-Path $ScriptDir "install-f12-listener.ps1"
if (Test-Path $InstallListener) {
    & $InstallListener
} else {
    Remove-F12AgentAutostart
}

Write-Host "[2/4] Skipping MyASUS IFEO redirect (keeps MyASUS app working)..."
Install-FridayRedirectScripts
Remove-IfeoRedirect

Write-Host "[3/4] Done configuring F12 inside FRIDAY..."
Write-Host "  (Start FRIDAY once; F12 is handled by the app - no extra agent in background)"

if ($Hard) {
    Write-Host ""
    Write-Host "Hard mode: disabling ASUS hotkey-related services..."
    Set-HardAsusServices $true
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "How F12 works now:" -ForegroundColor Cyan
Write-Host "  - Start FRIDAY (npm run dev:desktop). No FridayF12Agent runs in the background."
Write-Host "  - Press F12 to open the companion card (idle until you tap mic)."
Write-Host "  - Press F12 again to close companion and quit FRIDAY completely."
Write-Host "  - MyASUS app opens normally (IFEO redirect is not installed)."
Show-F12SetupBalloon "F12 mapped inside FRIDAY. No background agent. Press F12 to open; press again to quit."
Write-Host ""
Write-Host "Undo:  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Undo"
Write-Host "Logs:  %TEMP%\friday-f12-summon.log"