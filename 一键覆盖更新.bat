@echo off
chcp 936 >nul
cd /d "%~dp0"
title 覆盖更新 · 按键精灵
python "一键覆盖更新.py"
if errorlevel 1 goto :end
goto :eof
:end
echo 更新未完成，请查看上面的提示。
pause
