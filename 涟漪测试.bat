@echo off
rem ============================================================
rem  涟漪测试 启动器（双击运行）
rem  依次尝试 py 启动器 / python / python3
rem ============================================================
setlocal
title 涟漪测试 - 按键精灵
cd /d "%~dp0"

echo 正在启动涟漪测试页...
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "涟漪测试.py"
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "涟漪测试.py"
    goto :end
)

where python3 >nul 2>nul
if %errorlevel%==0 (
    python3 "涟漪测试.py"
    goto :end
)

echo [错误] 找不到 Python。请先安装 Python 3 并勾选 "Add to PATH"。
echo.

:end
if %errorlevel% neq 0 (
    echo.
    echo [程序已退出，退出码 %errorlevel%]
    echo 如果上方有红色报错，请把本窗口完整截图发给开发者。
)
echo.
pause
endlocal
