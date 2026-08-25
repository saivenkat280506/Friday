# Restore MyASUS app and ASUS hotkey services blocked by FRIDAY F12 rewire.
# MUST run as Administrator:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\saivenkat\Downloads\FRIDAY\scripts\restore-myasus.ps1"

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$IfeoTargets = @("AsusMyASUS.exe", "AsusHotkey.exe")
$IfeoRoot = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
$AsusServices = @(
    "ASUSOptimization",
    "AsusAppService",
    "ASUSSystemAnalysis",
    "ASUSSoftwareManager",
    "AsusSystemDiagnosis",
    "AsusSwitch",
    "LightingService"
)

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-IfeoRedirects {
    foreach ($name in $IfeoTargets) {
        $key = Join-Path $IfeoRoot $name
        if (-not (Test-Path $key)) { continue }
        Remove-ItemProperty -Path $key -Name "Debugger" -Force -ErrorAction SilentlyContinue
        Write-Host "  Removed IFEO redirect: $name" -ForegroundColor Green
    }
}

function Enable-AsusServices {
    foreach ($name in $AsusServices) {
        $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $svc) { continue }
        if ($svc.StartType -eq "Disabled") {
            Set-Service -Name $name -StartupType Manual -ErrorAction SilentlyContinue
            Write-Host "  Re-enabled service: $name" -ForegroundColor Green
        }
        if ($svc.Status -ne "Running") {
            try {
                Start-Service -Name $name -ErrorAction Stop
                Write-Host "  Started service: $name" -ForegroundColor Green
            } catch {
                Write-Host "  Could not start $name (may start after reboot)" -ForegroundColor Yellow
            }
        }
    }
}

function Find-MyAsusExe {
    $candidates = @(
        "${env:ProgramFiles}\ASUS\Armoury Crate Service\AsusMyASUS\AsusMyASUS.exe",
        "${env:ProgramFiles}\ASUS\ASUS Framework\AsusMyASUS\AsusMyASUS.exe",
        "${env:ProgramFiles(x86)}\ASUS\AsusMyASUS\AsusMyASUS.exe",
        "${env:ProgramFiles}\ASUS\AsusMyASUS\AsusMyASUS.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    $found = Get-ChildItem -Path "${env:ProgramFiles}", "${env:ProgramFiles(x86)}" -Recurse -Filter "AsusMyASUS.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

if (-not (Test-IsAdmin)) {
    Write-Host "Run as Administrator to restore MyASUS." -ForegroundColor Red
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    exit 1
}

Write-Host "Restoring MyASUS..." -ForegroundColor Cyan

$restoreKb = Join-Path $ScriptDir "restore-keyboard.ps1"
if (Test-Path $restoreKb) {
    & $restoreKb
}

Write-Host ""
Write-Host "Removing FRIDAY -> MyASUS IFEO blocks..." -ForegroundColor Cyan
Remove-IfeoRedirects

Write-Host ""
Write-Host "Re-enabling ASUS services..." -ForegroundColor Cyan
Enable-AsusServices

$myasus = Find-MyAsusExe
if ($myasus) {
    Write-Host ""
    Write-Host "Launching MyASUS: $myasus" -ForegroundColor Cyan
    Start-Process -FilePath $myasus
} else {
    Write-Host ""
    Write-Host "MyASUS exe not found - open it from Start Menu after reboot." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. MyASUS should open normally now." -ForegroundColor Green
Write-Host "If it still fails, reboot once and try MyASUS from the Start Menu." -ForegroundColor Cyan