@echo off
chcp 936 >nul
cd /d "%~dp0"
title 按键精灵

if not exist "main.py" goto :no_main

pythonw -c "import sys" >nul 2>nul
if errorlevel 1 goto :use_python
start "" pythonw "main.py"
exit /b

:use_python
python -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python
start "" python "main.py"
exit /b

:no_main
echo [错误] 当前目录没有 main.py
echo.
echo   当前目录：%CD%
echo.
echo   请确认已经完整解压压缩包，并且本文件和 main.py 在同一个文件夹里。
echo.
pause >nul
exit /b

:no_python
echo [错误] 没有找到 Python。
echo.
echo   请安装 Python 3.9 或更高版本：https://www.python.org/downloads/
echo   安装时务必勾选 Add python.exe to PATH。
echo.
pause >nul
exit /b
