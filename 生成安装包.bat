@echo off
setlocal
chcp 936 >nul
cd /d "%~dp0"
title 按键精灵 · 生成安装包

echo ============================================================
echo   生成 Windows 安装包（安装按键精灵.exe）
echo ============================================================
echo.
echo   本文件会把源码打包成 exe，并生成双击即装的
echo   「安装按键精灵.exe」，产物在 dist 文件夹。
echo.
echo   需要本机已安装 Python 3.9 以上。
echo.

if not exist "main.py" goto :no_main

python -c "import sys" >nul 2>nul
if errorlevel 1 goto :try_py
set "PY=python"
goto :build

:try_py
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=py -3"
goto :build

:build
echo 开始打包，首次会下载 PyInstaller，需要几分钟…
echo.
%PY% "build\build.py" %*
if errorlevel 1 goto :build_fail

echo.
echo ============================================================
echo   打包完成
echo ============================================================
echo   安装程序：dist\安装按键精灵.exe
echo   绿色版  ：dist\按键精灵\按键精灵.exe
echo ============================================================
if exist "dist" start "" "dist"
goto :end

:no_main
echo [错误] 找不到 main.py
echo.
echo   请把本文件和 main.py 放在同一个文件夹里运行。
echo   当前目录：%CD%
echo.
goto :end

:no_python
echo [错误] 没有找到可用的 Python。
echo.
echo   请安装 https://www.python.org/downloads/
echo   安装时务必勾选 "Add python.exe to PATH"。
echo.
goto :end

:build_fail
echo.
echo [错误] 打包失败，请看上面的报错。
echo   也可先双击 build\自检.bat 检查环境。
echo.

:end
echo.
echo 按任意键关闭本窗口
pause >nul
endlocal
exit /b
