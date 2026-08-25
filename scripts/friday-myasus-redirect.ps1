# Silent IFEO redirect: MyASUS launch -> summon FRIDAY companion (no CMD window).
$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Summon = Join-Path $ScriptDir "summon-friday-companion.ps1"
if (-not (Test-Path $Summon)) {
    $Summon = Join-Path $env:PROGRAMDATA "FRIDAY\summon-friday-companion.ps1"
}
if (Test-Path $Summon) {
    & $Summon
}