Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strCurDir = fso.GetParentFolderName(WScript.ScriptFullName)
strRoot = fso.GetParentFolderName(strCurDir)
strPython = strRoot & "\services\orchestrator\.venv\Scripts\python.exe"
strScript = strCurDir & "\run_daemon.py"
WshShell.CurrentDirectory = strRoot
WshShell.Run """" & strPython & """ """ & strScript & """", 0, False
