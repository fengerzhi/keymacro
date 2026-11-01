#!/usr/bin/env python3
"""按键精灵 · 桌面版 —— 程序入口

直接运行：
    python main.py

打包为可执行文件：
    python build/build.py
"""

import sys
import os
import platform

# 确保能从任意目录导入 core / ui
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠️ DPI 感知必须在**导入 tkinter / 创建任何窗口之前**声明，
# 否则 Windows 会缓存旧缩放信息，导致录制/回放坐标偏移（高缩放屏必现）。
try:
    from core.dpi import enable_dpi_awareness
    _DPI_MODE = enable_dpi_awareness()
except Exception as exc:                     # noqa: BLE001
    _DPI_MODE = f"处理失败：{exc}"


def install_crash_handlers() -> None:
    """装上全局异常兜底：崩溃写日志 + 弹窗，而不是窗口直接消失"""
    try:
        from core.crash import install_handlers
        install_handlers()
    except Exception as exc:                     # noqa: BLE001
        print(f"崩溃兜底安装失败（不影响使用）：{exc}", file=sys.stderr)


def check_dependencies() -> bool:
    """检查关键依赖，缺失时给出可操作的提示

    PyInstaller 打包后（sys.frozen）依赖已随包分发，跳过检查——
    打包环境里 import 失败不代表运行时会失败，且报错信息会误导用户。
    """
    if getattr(sys, "frozen", False):
        return True
    missing = []
    try:
        import pynput  # noqa: F401
    except ImportError:
        missing.append("pynput")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        missing.append("tkinter")

    if not missing:
        return True

    msg = (
        "缺少必要依赖：" + "、".join(missing) + "\n\n"
        "请执行：\n"
        "    pip install -r requirements.txt\n"
    )
    if "tkinter" in missing:
        sysname = platform.system()
        if sysname == "Linux":
            msg += "\nLinux 用户还需安装：\n    sudo apt install python3-tk\n"
        elif sysname == "Darwin":
            msg += "\nmacOS 请安装带 Tk 支持的 Python（python.org 官方版本）。\n"

    print(msg, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("依赖缺失", msg)
        root.destroy()
    except Exception:
        pass
    return False


def main() -> int:
    install_crash_handlers()
    if not check_dependencies():
        return 1
    print(f"DPI 感知模式：{_DPI_MODE}", file=sys.stderr)
    from ui.app import MacroApp
    app = MacroApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
