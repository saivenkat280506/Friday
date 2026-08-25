# Stop FRIDAY F12 background agent, watchdog task, and logon hooks.
$ErrorActionPreference = "SilentlyContinue"

$TaskNames = @("FRIDAY-F12-Watchdog", "FRIDAY-F12-Hotkey", "FRIDAY-F12-Listener")
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunNames = @("FRIDAY F12 Hotkey", "FRIDAY F12 Listener")
$Markers = @(
    (Join-Path $env:PROGRAMDATA "FRIDAY\f12-agent-installed"),
    (Join-Path $env:PROGRAMDATA "FRIDAY\f12-listener-installed")
)

function Stop-F12AgentProcesses {
    $stopped = 0
    foreach ($name in @("FridayF12Agent", "FridayF12Intercept")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    }
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'f12-hotkey-agent|start-listener\.vbs|FridayF12' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    return $stopped
}

foreach ($task in $TaskNames) {
    schtasks /Delete /TN $task /F 2>$null | Out-Null
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}

foreach ($name in $RunNames) {
    Remove-ItemProperty -Path $RunKey -Name $name -ErrorAction SilentlyContinue
}
foreach ($marker in $Markers) {
    Remove-Item -Path $marker -Force -ErrorAction SilentlyContinue
}

$count = Stop-F12AgentProcesses
Write-Output "Stopped $count F12 process(es); watchdog + autostart removed."