# DEPRECATED — this listener broke ASUS volume/brightness/Fn keys.
# Use:  scripts\restore-keyboard.ps1
# F12 works inside FRIDAY while the desktop app is running (Electron globalShortcut).
$ErrorActionPreference = "SilentlyContinue"
Write-Output "FRIDAY F12 system listener is disabled to protect Fn/volume/brightness keys."
Write-Output "Start FRIDAY with:  cd frontend && npm run dev:desktop"
Write-Output "Then press F12 to open the companion."
exit 0