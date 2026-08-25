# Stop the FRIDAY Alt+Space companion hotkey agent and remove logon autostart.
$ErrorActionPreference = "SilentlyContinue"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunValueName = "FRIDAY Companion Hotkey"

$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'companion_hotkey_agent\.py' }
$stopped = 0
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    $stopped++
}

Remove-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Environment" -Name "FRIDAY_COMPANION_HOTKEY_AGENT" -ErrorAction SilentlyContinue
# Legacy cleanup — same key as above

Write-Host "Stopped $stopped companion hotkey agent process(es)." -ForegroundColor Green
Write-Host "Logon autostart removed." -ForegroundColor Green