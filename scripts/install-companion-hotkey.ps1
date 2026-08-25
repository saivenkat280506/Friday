# Install FRIDAY Alt+Space companion hotkey agent to run at Windows logon.
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$Root = Split-Path -Parent $ScriptDir
$FridayDataDir = Join-Path $env:PROGRAMDATA "FRIDAY"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunValueName = "FRIDAY Companion Hotkey"
$StartScript = Join-Path $ScriptDir "start-companion-hotkey.ps1"
$RunHiddenVbs = Join-Path $ScriptDir "run-hidden.vbs"
$SummonSrc = Join-Path $ScriptDir "summon-friday-companion.ps1"

New-Item -ItemType Directory -Path $FridayDataDir -Force | Out-Null

$rootFile = Join-Path $ScriptDir "friday-root.txt"
Set-Content -Path $rootFile -Value $Root -Encoding ASCII -Force
$destRootFile = Join-Path $FridayDataDir "friday-root.txt"
Set-Content -Path $destRootFile -Value $Root -Encoding ASCII -Force

foreach ($file in @($StartScript, $RunHiddenVbs, $SummonSrc)) {
    if (-not (Test-Path $file)) {
        Write-Host "Missing required file: $file" -ForegroundColor Red
        exit 1
    }
}

$destStart = Join-Path $FridayDataDir "start-companion-hotkey.ps1"
$destVbs = Join-Path $FridayDataDir "run-hidden.vbs"
$destSummon = Join-Path $FridayDataDir "summon-friday-companion.ps1"
Copy-Item $StartScript $destStart -Force
Copy-Item $RunHiddenVbs $destVbs -Force
Copy-Item $SummonSrc $destSummon -Force

$launchCmd = "wscript.exe //B `"$destVbs`" `"$destStart`""
New-ItemProperty -Path $RunKey -Name $RunValueName -Value $launchCmd -PropertyType String -Force | Out-Null

# Remove legacy global env that prevented the backend from registering Alt+Space
# when the background agent was not actually running.
Remove-ItemProperty -Path "HKCU:\Environment" -Name "FRIDAY_COMPANION_HOTKEY_AGENT" -ErrorAction SilentlyContinue

Write-Host "Installed FRIDAY companion hotkey autostart." -ForegroundColor Green
Write-Host "  Registry: $RunKey\$RunValueName" -ForegroundColor DarkGray
Write-Host "  Alt+Space will summon FRIDAY even when servers are stopped." -ForegroundColor Cyan

& $StartScript