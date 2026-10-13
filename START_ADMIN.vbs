Option Explicit

Dim shell, fileSystem, repoRoot, backendRoot, pythonwPath, command, exitCode

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

repoRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
backendRoot = fileSystem.BuildPath(repoRoot, "backend")
pythonwPath = fileSystem.BuildPath(backendRoot, ".venv\Scripts\pythonw.exe")

If Not fileSystem.FileExists(pythonwPath) Then
    MsgBox "Project .venv not found: backend\.venv", vbCritical, "Anna Video Admin"
    WScript.Quit 1
End If

shell.CurrentDirectory = backendRoot
command = Chr(34) & pythonwPath & Chr(34) & " -m anna_backend.admin_app"
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    MsgBox "Anna Video Admin could not be started.", vbCritical, "Anna Video Admin"
End If

WScript.Quit exitCode
