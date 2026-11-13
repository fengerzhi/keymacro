"""clipboard.py —— 剪贴板纯文本读写

为什么自己写而不装 pyperclip
─────────────────────────────────────────
Windows 上 pyperclip 也是走 ctypes，但它要额外装一个包；这里的实现已经
处理好 64 位的坑：不声明 restype 时 HGLOBAL / 指针会被截成 32 位，
GlobalLock 拿到的地址是坏的，SetClipboardData 会静默失败。

macOS / Linux 用系统命令（pbpaste / xclip / xsel），不引入依赖。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import time

SYSTEM = platform.system()
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


# ================================================================ Windows
def _win_api():
    """配置好参数类型的剪贴板 API（user32 / kernel32）"""
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.OpenClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.restype = wintypes.BOOL
    u.CloseClipboard.restype = wintypes.BOOL
    u.GetClipboardData.argtypes = [wintypes.UINT]
    u.GetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HANDLE
    k.GlobalLock.argtypes = [wintypes.HANDLE]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HANDLE]
    k.GlobalUnlock.restype = wintypes.BOOL
    return u, k


def _open(u) -> bool:
    """剪贴板常被别的程序占着，重试几次再放弃"""
    for _ in range(4):
        if u.OpenClipboard(None):
            return True
        time.sleep(0.03)
    return False


def _win_get() -> str:
    import ctypes

    try:
        u, k = _win_api()
        if not _open(u):
            return ""
        try:
            h = u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = k.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                k.GlobalUnlock(h)
        finally:
            u.CloseClipboard()
    except Exception:
        return ""


def _win_set(text: str) -> bool:
    import ctypes

    try:
        data = text.encode("utf-16-le") + b"\x00\x00"
        u, k = _win_api()
        if not _open(u):
            return False
        try:
            u.EmptyClipboard()
            h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            p = k.GlobalLock(h)
            if not p:
                return False
            ctypes.memmove(p, data, len(data))
            k.GlobalUnlock(h)
            if not u.SetClipboardData(CF_UNICODETEXT, h):
                return False
        finally:
            u.CloseClipboard()
        return True
    except Exception:
        return False


# ================================================================ macOS / Linux
def _cmd_tool(kind: str) -> list[str] | None:
    """返回可用的剪贴板命令（kind = 'get' | 'set'）"""
    if SYSTEM == "Darwin":
        return ["pbpaste"] if kind == "get" else ["pbcopy"]
    if shutil.which("xclip"):
        base = ["xclip", "-selection", "clipboard"]
        return base + ["-o"] if kind == "get" else base
    if shutil.which("xsel"):
        return (["xsel", "--clipboard", "--output"] if kind == "get"
                else ["xsel", "--clipboard", "--input"])
    if shutil.which("wl-paste") and kind == "get":
        return ["wl-paste", "--no-newline"]
    if shutil.which("wl-copy") and kind == "set":
        return ["wl-copy"]
    return None


def _run_get() -> str:
    cmd = _cmd_tool("get")
    if not cmd:
        return ""
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=5)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _run_set(text: str) -> bool:
    cmd = _cmd_tool("set")
    if not cmd:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), timeout=5, check=False)
        return True
    except Exception:
        return False


# ================================================================ 对外
def get_text() -> str:
    """读剪贴板里的纯文本（读不到返回空串，绝不抛异常）"""
    return _win_get() if SYSTEM == "Windows" else _run_get()


def set_text(text: str) -> bool:
    """把纯文本写进剪贴板（成功返回 True）"""
    return _win_set(text) if SYSTEM == "Windows" else _run_set(text)


def has_text(min_chars: int = 1) -> bool:
    return len(get_text().strip()) >= max(1, min_chars)
