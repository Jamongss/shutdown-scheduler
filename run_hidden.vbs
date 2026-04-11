Set WshShell = CreateObject("WScript.Shell")
ScriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = ScriptDir
WshShell.Run "cmd /c python3.13 """ & ScriptDir & "\shutdown_scheduler.py"" > """ & ScriptDir & "\error.log"" 2>&1", 0, False
