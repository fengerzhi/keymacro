"""crash.py —— 全局异常兜底

目标：**任何崩溃都留痕**，不再出现「窗口一闪就没了，什么都不知道」。

三层覆盖
─────────────────────────────────────────
1. sys.excepthook           主线程未捕获异常
2. threading.excepthook     子线程未捕获异常（回放/录制跑在子线程）
3. Tk.report_callback_exception   tkinter 回调里的异常
   （默认只打到 stderr，用 pythonw 启动时等于彻底静默）

落盘位置
─────────────────────────────────────────
    ~/.macro-sprite/logs/crash_YYYYmmdd_HHMMSS.txt
写入失败时退回系统临时目录，并把最终路径返回给调用方用于提示。
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import threading
import time
import traceback

_installed = False
_shown = False              # 只弹一次窗，避免连环崩溃时刷屏


def _base_dir() -> str:
    """日志根目录；优先应用数据目录，失败则用临时目录"""
    try:
        from .library import app_dir
        root = app_dir()
    except Exception:                             # noqa: BLE001
        root = os.path.join(tempfile.gettempdir(), ".macro-sprite")
    path = os.path.join(str(root), "logs")
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:                             # noqa: BLE001
        path = os.path.join(tempfile.gettempdir(), "macro-sprite-crash")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:                         # noqa: BLE001
            pass
        return path


def _app_version() -> str:
    try:
        from .updater import APP_VERSION
        return APP_VERSION
    except Exception:                             # noqa: BLE001
        return "?"


def format_crash(exc_type, exc, tb, *, source: str = "main") -> str:
    """把异常整理成一段可直接贴给别人看的文本"""
    body = "".join(traceback.format_exception(exc_type, exc, tb))
    lines = [
        "按键精灵 · 崩溃报告",
        "=" * 58,
        f"时间      ：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"版本      ：v{_app_version()}",
        f"来源      ：{source}",
        f"系统      ：{platform.platform()}",
        f"Python    ：{sys.version.split()[0]}  ({platform.architecture()[0]})",
        f"打包运行  ：{'是' if getattr(sys, 'frozen', False) else '否'}",
        f"工作目录  ：{os.getcwd()}",
        f"程序位置  ：{getattr(sys, 'executable', '')}",
        f"线程      ：{threading.current_thread().name}",
        "=" * 58,
        body,
    ]
    return "\n".join(lines)


def write_crash(exc_type, exc, tb, *, source: str = "main") -> str:
    """写崩溃日志，返回文件路径（失败返回空串）"""
    text = format_crash(exc_type, exc, tb, source=source)
    # 带毫秒：同一秒内连续两次崩溃不会互相覆盖
    name = f"crash_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.txt"
    for d in (_base_dir(), tempfile.gettempdir()):
        try:
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except Exception:                         # noqa: BLE001
            continue
    # 连文件都写不了：至少把内容打到 stderr
    try:
        sys.stderr.write(text + "\n")
    except Exception:                             # noqa: BLE001
        pass
    return ""


def notify(path: str, exc: BaseException | None = None) -> None:
    """弹窗告知用户（只弹一次）。没有 GUI 环境时静默跳过。

    注意：tkinter 不是线程安全的，**子线程里绝不能弹窗**（会直接把进程弄崩）。
    回放/录制线程的异常由 core.player 自己 try/except 并回调 on_done，
    所以这里对子线程只负责写日志。
    """
    global _shown
    if _shown:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    _shown = True

    tail = ""
    if exc is not None:
        msg = str(exc).strip().splitlines()
        if msg:
            tail = msg[0][:200]

    text = "程序遇到了一个错误，已记录崩溃日志。\n\n"
    if tail:
        text += f"错误信息：{tail}\n\n"
    if path:
        text += f"日志文件：\n{path}\n\n"
        text += "把这个文件发给开发者，可以快速定位问题。\n"
    else:
        text += "日志写入失败，请把控制台输出发给开发者。\n"
    text += "\n程序会尝试继续运行；若窗口已消失，请重新启动。"

    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk._default_root                # 已有主窗口就复用，避免多开一个
        if root is not None:
            messagebox.showerror("程序错误", text, parent=root)
        else:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("程序错误", text)
            r.destroy()
    except Exception:                             # noqa: BLE001
        try:
            sys.stderr.write(text + "\n")
        except Exception:                         # noqa: BLE001
            pass


def handle(exc_type, exc, tb, *, source: str = "main", popup: bool = True) -> str:
    """统一处理：写盘 + 提示，返回日志路径"""
    if issubclass(exc_type, KeyboardInterrupt):
        return ""
    path = write_crash(exc_type, exc, tb, source=source)
    if popup:
        try:
            notify(path, exc)
        except Exception:                         # noqa: BLE001
            pass
    return path


def install_handlers() -> None:
    """安装 sys / threading 两级钩子（幂等）"""
    global _installed
    if _installed:
        return
    _installed = True

    def _sys_hook(exc_type, exc, tb):
        handle(exc_type, exc, tb, source="main")

    sys.excepthook = _sys_hook

    def _thread_hook(args):
        if getattr(args, "exc_type", None) is None:
            return
        handle(args.exc_type, args.exc_value, args.exc_traceback,
               source=f"thread:{getattr(args.thread, 'name', '?')}")

    try:
        threading.excepthook = _thread_hook
    except Exception:                             # noqa: BLE001
        pass
