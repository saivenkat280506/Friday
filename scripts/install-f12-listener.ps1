# DEPRECATED — system-wide F12 listener breaks ASUS Fn/volume/brightness keys.
Write-Host "This installer is disabled." -ForegroundColor Yellow
Write-Host "It previously hijacked volume, brightness, and other Fn keys on ASUS laptops." -ForegroundColor Yellow
Write-Host ""
Write-Host "To restore your keyboard:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\restore-keyboard.ps1`""
Write-Host ""
Write-Host "To use F12 with FRIDAY:" -ForegroundColor Cyan
Write-Host "  1. Start FRIDAY:  cd frontend && npm run dev:desktop"
Write-Host "  2. Press F12 while FRIDAY is running"
exit 1