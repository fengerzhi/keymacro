#!/bin/bash
# 双击运行：在 macOS 上一键生成 按键精灵.app 和 按键精灵-<版本>-macOS.dmg
# 如果双击提示「权限不足」，先在终端执行一次：
#     chmod +x "build/打包苹果版.command"
cd "$(dirname "$0")/.." || exit 1

echo "============================================================"
echo "  按键精灵 · macOS 打包"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3。"
    echo "请先安装 Python 3.11+（推荐 python.org 官方安装包，自带 tkinter），"
    echo "然后重新双击本脚本。"
    read -r -p "按回车键关闭…" _
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "当前 Python 缺少 tkinter（界面库），无法打包。"
    echo "请改用 python.org 官方安装包，或执行：brew install python-tk"
    read -r -p "按回车键关闭…" _
    exit 1
fi

python3 build/build.py || { echo "主程序打包失败，详见 打包日志.txt"; read -r -p "按回车键关闭…" _; exit 1; }
python3 build/make_macos.py || { echo "dmg 生成失败"; read -r -p "按回车键关闭…" _; exit 1; }

echo ""
echo "产物目录：$(pwd)/dist"
ls -lh dist
read -r -p "按回车键关闭…" _
