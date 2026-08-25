# Summon FRIDAY companion from F12 listener (cold start or toggle).
$ErrorActionPreference = "SilentlyContinue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootFile = Join-Path $ScriptDir "friday-root.txt"
if (Test-Path $RootFile) {
    $Root = (Get-Content $RootFile -Raw).Trim()
} else {
    $Root = "C:\Users\saivenkat\Downloads\FRIDAY"
}

$Frontend = Join-Path $Root "frontend"
$BackendDir = Join-Path $Root "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$MainPy = Join-Path $BackendDir "main.py"
$BackendUrl = "http://127.0.0.1:8000"
$LockFile = Join-Path $env:TEMP "friday-f12-start.lock"
$LogFile = Join-Path $env:TEMP "friday-f12-summon.log"
$BackendLog = Join-Path $env:TEMP "friday-backend-f12.log"

function Write-Log([string]$Msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    try { Add-Content -Path $LogFile -Value $line } catch {}
    try { Add-Content -Path "C:\ProgramData\FRIDAY\summon.log" -Value $line } catch {}
}

function Show-CompanionNotice([string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information
        $n.Visible = $true
        $n.BalloonTipTitle = "F.R.I.D.A.Y."
        $n.BalloonTipText = $Text
        $n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
        $n.ShowBalloonTip(4500)
        Start-Sleep -Milliseconds 450
        $n.Visible = $false
        $n.Dispose()
    } catch {}
}

function Test-BackendHealthy {
    try {
        $r = Invoke-WebRequest -Uri "$BackendUrl/health" -Method GET -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Invoke-CompanionPostJson([string]$Path) {
    try {
        $r = Invoke-WebRequest -Uri ($BackendUrl + $Path) -Method POST -TimeoutSec 8 -UseBasicParsing
        if ($r.Content) { return ($r.Content | ConvertFrom-Json) }
        return @{ status = "ok" }
    } catch {
        return $null
    }
}

function Invoke-CompanionF12Toggle {
    $result = Invoke-CompanionPostJson "/companion/f12"
    if ($result -and $result.action) {
        return [string]$result.action
    }
    return "open"
}

function Test-PortListening([int]$Port) {
    try {
        $lines = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
        return [bool]$lines
    } catch {
        return $false
    }
}

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
        Write-Log "Stopping PID $procId on port $Port"
        taskkill /PID $procId /F 2>$null | Out-Null
    }
}

function Start-FridayBackend {
    if (Test-BackendHealthy) {
        Write-Log "Backend already healthy"
        return $true
    }
    if (-not (Test-Path $Python)) {
        Write-Log "Missing venv python: $Python"
        return $false
    }
    if (-not (Test-Path $MainPy)) {
        Write-Log "Missing main.py: $MainPy"
        return $false
    }

    if (Test-PortListening 8000) {
        Write-Log "Port 8000 listening but /health failed - clearing listeners"
        try {
            $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
            foreach ($c in $conns) {
                if ($c.OwningProcess -gt 0) {
                    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {}
        Start-Sleep -Seconds 1
    }

    Write-Log "Starting backend: $Python $MainPy"
    try {
        $errLog = Join-Path $env:TEMP "friday-backend-f12.err.log"
        $proc = Start-Process -FilePath $Python `
            -ArgumentList @($MainPy) `
            -WorkingDirectory $BackendDir `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput $BackendLog `
            -RedirectStandardError $errLog
        Write-Log "Backend process started pid=$($proc.Id)"
        return $true
    } catch {
        Write-Log "Backend start failed: $_"
        return $false
    }
}

function Start-FridayDesktopIfNeeded {
    $electron = Get-Process -Name "electron" -ErrorAction SilentlyContinue
    $webUp = Test-PortListening 3000
    if ($electron -and $webUp) {
        Write-Log "Electron + web already present"
        return
    }

    $now = Get-Date
    if (Test-Path $LockFile) {
        try {
            $age = $now - (Get-Item $LockFile).LastWriteTime
            if ($age.TotalSeconds -lt 90) {
                Write-Log "Desktop start already in progress"
                return
            }
        } catch {}
    }
    try { Set-Content -Path $LockFile -Value $PID -Force } catch {}

    $npm = $null
    foreach ($c in @("npm.cmd", "npm")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $npm = $cmd.Source; break }
    }
    if (-not $npm) {
        Write-Log "npm not found"
        return
    }

    Write-Log "Starting desktop (npm run dev:desktop)"
    try {
        $launch = "Set-Location -LiteralPath '$Frontend'; & '$npm' run dev:desktop"
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $launch) `
            -WindowStyle Hidden
        Write-Log "Launched desktop via hidden PowerShell"
    } catch {
        Write-Log "Desktop launch failed: $_"
    }
}

function Wait-ForBackend([int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendHealthy) { return $true }
        Start-Sleep -Milliseconds 600
    }
    return $false
}

function Wait-ForDesktopReady([int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Test-PortListening 3000) -and (Get-Process -Name "electron" -ErrorAction SilentlyContinue)) {
            Start-Sleep -Seconds 2
            return $true
        }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

function Notify-CompanionResult([string]$Action, [switch]$ColdStart) {
    if ($Action -eq "close") {
        Show-CompanionNotice "Companion closed. Press F12 to open again."
        return
    }
    if ($ColdStart) {
        Show-CompanionNotice "Companion running. Tap mic on the card to talk."
    } else {
        Show-CompanionNotice "Companion running."
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────
Write-Log "Summon requested (root=$Root)"

if (Test-BackendHealthy) {
    $electronUp = [bool](Get-Process -Name "electron" -ErrorAction SilentlyContinue)
    if (-not $electronUp) {
        Write-Log "Backend up but desktop missing - starting UI"
        Start-FridayDesktopIfNeeded
        $null = Wait-ForDesktopReady 120
        $action = Invoke-CompanionF12Toggle
        Write-Log "Companion toggled action=$action (backend was up, desktop started)"
        Notify-CompanionResult $action
        exit 0
    }

    # Backend and desktop both running — toggle companion (Dynamic Island)
    $action = Invoke-CompanionF12Toggle
    Write-Log "Companion toggled action=$action (backend + desktop running)"
    Notify-CompanionResult $action
    exit 0
}

Show-CompanionNotice "Starting FRIDAY companion..."
Write-Log "Cold start - backend offline"

if (-not (Start-FridayBackend)) {
    Show-CompanionNotice "Could not start FRIDAY. Check install path."
    exit 1
}

Start-FridayDesktopIfNeeded

if (Wait-ForBackend 120) {
    try { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue } catch {}
    Write-Log "Backend healthy after cold start"
    $null = Wait-ForDesktopReady 120
    Start-Sleep -Seconds 4
    $action = Invoke-CompanionF12Toggle
    Write-Log "Companion toggled action=$action after cold start"
    Notify-CompanionResult $action -ColdStart
} else {
    Write-Log "Backend unhealthy after wait"
    Show-CompanionNotice "FRIDAY backend failed to start. See logs in %TEMP%."
}

exit 0