@echo off
setlocal
chcp 936 >nul
cd /d "%~dp0.."
title 按键精灵 - 一键打包

rem ============================================================
rem  说明：本文件用 GBK 保存，兼容中文控制台。
rem  内部不使用任何 if(...) 括号块，路径里带空格或括号也不会崩。
rem  任何一步出错都会停下来，不会闪退。
rem ============================================================

echo ============================================================
echo   按键精灵 - Windows 一键打包
echo ============================================================
echo   项目目录：%CD%
echo.

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
echo [1/2] Python 版本：
%PY% --version
echo.
echo [2/2] 开始打包，首次会下载 PyInstaller，请耐心等待…
echo.
%PY% "build\build.py" %*
if errorlevel 1 goto :build_fail

echo.
echo ============================================================
echo   打包成功
echo ============================================================
echo   绿色版  ：dist\按键精灵\按键精灵.exe
echo   安装程序：dist\安装按键精灵.exe
echo.
echo   发给别人：只发 dist\安装按键精灵.exe 即可。
echo ============================================================
if exist "dist" start "" "dist"
goto :end

:no_python
echo.
echo [错误] 没有找到可用的 Python。
echo.
echo   1. 打开 https://www.python.org/downloads/ 下载 Python 3.9 或更高版本
echo   2. 安装时务必勾选 "Add python.exe to PATH"
echo   3. 装好后重新双击本文件
echo.
goto :end

:build_fail
echo.
echo [错误] 打包失败，请看上面的报错信息。
echo.
echo   常见原因：
echo     1. 杀毒软件拦截 —— 添加信任后重试
echo     2. 依赖缺失 —— 手动执行：%PY% -m pip install pynput pillow pyinstaller
echo     3. 路径含特殊字符 —— 可把整个文件夹移到 D:\macro 再试
echo.
echo   排查建议：先双击 build\自检.bat，把结果截图反馈。
echo.

:end
echo.
echo ============================================================
echo   按任意键关闭本窗口
echo ============================================================
pause
endlocal
exit /b
