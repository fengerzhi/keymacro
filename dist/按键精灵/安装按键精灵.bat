@echo off
chcp 936 >nul
cd /d "%~dp0"
title 按键精灵 安装

echo ============================================================
echo    按键精灵 安装程序
echo ============================================================
echo.
echo   当前目录：%CD%
echo.

python -c "import sys" >nul 2>nul
if errorlevel 1 goto :try_py
echo   使用 Python：python
echo.
python install.py
goto :done

:try_py
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python
echo   使用 Python 启动器：py -3
echo.
py -3 install.py
goto :done

:no_python
echo [错误] 没有找到 Python。
echo.
echo   办法一：安装 Python 3.9 或更高版本
echo           下载地址 https://www.python.org/downloads/
echo           安装时务必勾选 Add python.exe to PATH
echo.
echo   办法二：如果只是想用软件，直接双击 启动按键精灵.bat
echo           （那个文件同样需要 Python）
echo.
echo   办法三：双击 生成安装包.bat 打出 exe，之后不再依赖 Python

:done
echo.
echo ============================================================
echo   按任意键关闭本窗口
echo ============================================================
pause >nul
exit /b
