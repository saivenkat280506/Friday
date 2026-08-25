@echo off
REM Legacy launcher — rewire-f12-friday.ps1 now points IFEO at friday-myasus-redirect.ps1 (no CMD flash).
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%friday-myasus-redirect.ps1"
exit /b 0
