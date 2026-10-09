Option Explicit
Dim fileSystem, shell, folder
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
shell.Run "pyw -3 """ & fileSystem.BuildPath(folder, "app.pyw") & """", 0, False
