@echo off
chcp 936 >nul
cd /d "%~dp0"
title 按键精灵 · 升级诊断

python -c "import sys" >nul 2>nul
if errorlevel 1 goto :try_py
python "诊断升级.py"
goto :end

:try_py
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python
py -3 "诊断升级.py"
goto :end

:no_python
echo [提示] 没有找到 Python，无法运行诊断。
echo.
echo   可以把下面这些信息直接发给维护者：
echo     - 程序文件夹里有哪些文件
echo     - 升级文件夹里有哪些文件
echo     - 点升级后出现了什么窗口
pause

:end
exit /b
