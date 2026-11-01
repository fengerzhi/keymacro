rem ============================================================
rem  按键精灵 · 安装启动器（双击本文件即可安装）
rem  自动查找本机 Python；找不到会提示安装
rem ============================================================
Option Explicit
Dim fso, shell, here, py, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

If Not fso.FileExists(here & "\install.py") Then
    MsgBox "安装包不完整：找不到 install.py。请重新解压整个压缩包再运行。", 16, "按键精灵"
    WScript.Quit 1
End If

If Not fso.FileExists(here & "\main.py") And Not fso.FileExists(here & "\按键精灵.exe") Then
    MsgBox "安装包不完整：找不到 main.py 或 按键精灵.exe。请重新解压整个压缩包再运行。", 16, "按键精灵"
    WScript.Quit 1
End If

py = FindPython()
If py = "" Then
    MsgBox "没有找到可用的 Python。" & vbCrLf & vbCrLf & _
           "源码版安装需要 Python 3.9 或更高版本：" & vbCrLf & _
           "1) 打开 https://www.python.org/downloads/" & vbCrLf & _
           "2) 安装时务必勾选 Add python.exe to PATH" & vbCrLf & _
           "3) 装好后重新双击本文件" & vbCrLf & vbCrLf & _
           "（如果拿到的是打包好的 exe 版，直接运行 按键精灵.exe 即可）", 48, "按键精灵"
    WScript.Quit 1
End If

cmd = """" & py & """ -X utf8 """ & here & "\install.py""
shell.Run "cmd /c title 按键精灵 安装 && " & cmd, 1, True
WScript.Quit 0

Function FindPython()
    Dim c, r
    FindPython = ""
    c = "pythonw.exe"
    On Error Resume Next
    r = shell.RegRead("HKLM\SOFTWARE\Python\PythonCore\3.13\InstallPath\WindowedExecutablePath")
    If Err.Number = 0 And r <> "" Then FindPython = r : Exit Function
    Err.Clear
    For Each c In Array("pythonw.exe", "python.exe")
        r = Resolve(c)
        If r <> "" Then FindPython = r : Exit Function
    Next
    On Error GoTo 0
End Function

Function Resolve(name)
    Dim p, paths, i, f
    Resolve = ""
    On Error Resume Next
    p = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
    If fso.FolderExists(p) Then
        For Each f In fso.GetFolder(p).SubFolders
            If fso.FileExists(f.Path & "\" & name) Then Resolve = f.Path & "\" & name : Exit Function
        Next
    End If
    paths = Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
    For i = 0 To UBound(paths)
        If paths(i) <> "" And fso.FileExists(paths(i) & "\" & name) Then
            Resolve = paths(i) & "\" & name
            Exit Function
        End If
    Next
    On Error GoTo 0
End Function
