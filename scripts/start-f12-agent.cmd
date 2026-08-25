@echo off
REM Deprecated — FridayF12Agent is no longer used. Run scripts\stop-f12-agent.ps1 to clean up.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0stop-f12-agent.ps1"
exit /b 0
