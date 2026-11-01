@echo off
rem ============================================================
rem  排错专用：环境自检
rem  双击运行，会把 Python / pip / 依赖状态全部打印出来并停住
rem  若 build_windows.bat 一闪而过，先跑这个，把结果截图发我
rem ============================================================
setlocal
title 按键精灵 - 环境自检
cd /d "%~dp0.."
echo ============================================================
echo   环境自检
echo ============================================================
echo 当前目录：%CD%
echo.

echo --- 1. 检查 python ---
python --version 2>&1
echo   退出码：%errorlevel%
echo.

echo --- 2. 检查 py 启动器 ---
py -3 --version 2>&1
echo   退出码：%errorlevel%
echo.

echo --- 3. python 所在位置 ---
where python 2>&1
echo.

echo --- 4. pip 版本 ---
python -m pip --version 2>&1
echo   退出码：%errorlevel%
echo.

echo --- 5. 已装的关键依赖 ---
python -c "import pynput; v=getattr(pynput,'__version__','(新版未提供)'); print('pynput OK', v)" 2>&1
python -c "import PIL; print('pillow OK', PIL.__version__)" 2>&1
python -c "import PyInstaller; print('pyinstaller OK', PyInstaller.__version__)" 2>&1
python -c "import openpyxl; print('openpyxl OK', openpyxl.__version__)" 2>&1
echo   （openpyxl 未装只影响 Excel 数据源，CSV 不受影响）
echo.

echo --- 6. 关键文件是否存在 ---
if exist "main.py" (echo   main.py        [找到]) else (echo   main.py        [缺失!])
if exist "build\build.py" (echo   build\build.py [找到]) else (echo   build\build.py [缺失!])
if exist "core\recorder.py" (echo   core\           [找到]) else (echo   core\          [缺失!])
echo.

echo ============================================================
echo   自检结束，请把以上内容截图发给我
echo ============================================================
pause
endlocal
