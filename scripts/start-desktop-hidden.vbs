' Launch FRIDAY desktop (Next + Electron) with no visible console window.
Dim fso, sh, scriptDir, rootFile, root, frontend, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("Wscript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootFile = fso.BuildPath(scriptDir, "friday-root.txt")
If fso.FileExists(rootFile) Then
    root = Trim(CreateObject("Scripting.FileSystemObject").OpenTextFile(rootFile, 1).ReadAll())
Else
    root = fso.GetParentFolderName(scriptDir)
End If
frontend = fso.BuildPath(root, "frontend")
cmd = "powershell.exe -NoProfile -WindowStyle Hidden -Command ""Set-Location -LiteralPath '" & frontend & "'; npm run dev:desktop"""
sh.Run cmd, 0, False