' Run a PowerShell script with zero visible console window.
If WScript.Arguments.Count < 1 Then WScript.Quit 1
script = WScript.Arguments(0)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
CreateObject("Wscript.Shell").Run cmd, 0, False