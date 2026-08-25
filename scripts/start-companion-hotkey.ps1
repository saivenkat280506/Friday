# Start the FRIDAY Alt+Space companion hotkey agent (hidden, single instance).
$ErrorActionPreference = "SilentlyContinue"

function Resolve-FridayRoot {
    param([string]$ScriptRoot)
    $rootFile = Join-Path $ScriptRoot "friday-root.txt"
    if (Test-Path $rootFile) {
        return (Get-Content $rootFile -Raw).Trim()
    }
    $parent = Split-Path -Parent $ScriptRoot
    if (Test-Path (Join-Path $parent "backend\main.py")) {
        return $parent
    }
    if ($env:FRIDAY_ROOT -and (Test-Path (Join-Path $env:FRIDAY_ROOT "backend\main.py"))) {
        return $env:FRIDAY_ROOT.Trim()
    }
    return $null
}

$Root = Resolve-FridayRoot -ScriptRoot $PSScriptRoot
if (-not $Root) {
    Write-Host "Could not resolve FRIDAY project root (missing friday-root.txt)" -ForegroundColor Red
    exit 1
}

$BackendDir = Join-Path $Root "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Agent = Join-Path $BackendDir "scripts\companion_hotkey_agent.py"
$LogFile = Join-Path $env:TEMP "friday-companion-hotkey-agent.log"

if (-not (Test-Path $Python)) {
    Write-Host "Missing backend venv: $Python" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Agent)) {
    Write-Host "Missing hotkey agent: $Agent" -ForegroundColor Red
    exit 1
}

$existing = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'companion_hotkey_agent\.py' })
if ($existing.Count -eq 1) {
    Write-Host "Companion hotkey agent already running (PID $($existing[0].ProcessId))" -ForegroundColor Green
    exit 0
}
foreach ($proc in $existing) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($existing.Count -gt 0) {
    Start-Sleep -Milliseconds 800
}

$env:FRIDAY_COMPANION_HOTKEY_AGENT = "1"
$env:FRIDAY_ROOT = $Root

Write-Host "Starting FRIDAY companion hotkey agent (Alt+Space) from $Root..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $Python `
    -ArgumentList @("-u", $Agent) `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 1
$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($alive) {
    Write-Host "Companion hotkey agent started (PID $($proc.Id))" -ForegroundColor Green
    Write-Host "Press Alt+Space to open FRIDAY companion." -ForegroundColor Cyan
    exit 0
}

Write-Host "Hotkey agent failed to start. See $LogFile" -ForegroundColor Red
exit 1