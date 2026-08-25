@echo off
REM Double-click this file, then click Yes on the Administrator prompt.
REM Removes FRIDAY IFEO blocks and re-enables MyASUS.

net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore-myasus.ps1"
echo.
pause