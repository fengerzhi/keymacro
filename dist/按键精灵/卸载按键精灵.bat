@echo off
setlocal
chcp 936 >nul
title 卸载 按键精灵
set "D=%~dp0"
if "%D:~-1%"=="\" set "D=%D:~0,-1%"
echo ============================================================
echo    卸载 按键精灵
echo ============================================================
echo   安装目录：%D%
echo.
set /p OK=确认卸载？(Y/N): 
if /i not "%OK%"=="Y" goto :end
echo 正在删除快捷方式与配置...
del /f /q "%USERPROFILE%\Desktop\按键精灵.lnk" >nul 2>nul
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\按键精灵.lnk" >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyMacro" /f >nul 2>nul
echo 正在删除程序文件...
cd /d "%TEMP%"
> "%TEMP%\km_uninst.bat" echo ping 127.0.0.1 -n 3 ^>nul
>> "%TEMP%\km_uninst.bat" echo rmdir /s /q "%D%"
start "" /min cmd /c "%TEMP%\km_uninst.bat"
echo 已卸载。脚本和日志数据保留在 %%APPDATA%%\.macro-sprite（如需彻底清理请手动删除）。
:end
echo.
pause >nul
endlocal
exit /b
