"""window.py —— 目标窗口管理（跨平台）

回放前把焦点切回录制时操作的窗口，否则键盘输入会打到工具自身。

实现方式：
  Windows  —— ctypes 调 user32.FindWindowW / SetForegroundWindow
  macOS    —— AppleScript activate（无需额外依赖）
  Linux    —— xdotool（X11）；Wayland 下不可用，退化为提示
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys

SYSTEM = platform.system()


def get_active_window() -> tuple[str | None, str]:
    """返回 (窗口标识, 人类可读标题)；无权限或不可用时返回 (None, "")"""
    try:
        if SYSTEM == "Windows":
            return _win_active()
        if SYSTEM == "Darwin":
            return _mac_active()
        return _linux_active()
    except Exception:
        return None, ""


# ---------------- Windows ----------------
def _win_active() -> tuple[str | None, str]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return str(hwnd), buf.value


def _win_focus(handle: str) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(handle)
    if not user32.IsWindow(hwnd):
        return False
    # 最小化状态先还原
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


# ---------------- macOS ----------------
def _mac_active() -> tuple[str | None, str]:
    script = (
        'tell application "System Events" to set frontApp to name of '
        "first application process whose frontmost is true"
    )
    out = subprocess.run(["osascript", "-e", script],
                         capture_output=True, text=True, timeout=3)
    name = out.stdout.strip()
    return (name or None), name


def _mac_focus(handle: str) -> bool:
    script = f'tell application "{handle}" to activate'
    out = subprocess.run(["osascript", "-e", script],
                         capture_output=True, text=True, timeout=3)
    return out.returncode == 0


# ---------------- Linux ----------------
def _linux_active() -> tuple[str | None, str]:
    if os.environ.get("WAYLAND_DISPLAY"):
        return None, "Wayland（不支持窗口聚焦）"
    out = subprocess.run(
        ["xdotool", "getactivewindow", "getwindowname"],
        capture_output=True, text=True, timeout=3,
    )
    if out.returncode != 0:
        return None, ""
    title = out.stdout.strip()
    wid = subprocess.run(["xdotool", "getactivewindow"],
                         capture_output=True, text=True, timeout=3).stdout.strip()
    return (wid or None), title


def _linux_focus(handle: str) -> bool:
    if os.environ.get("WAYLAND_DISPLAY"):
        return False
    out = subprocess.run(["xdotool", "windowactivate", "--sync", handle],
                         capture_output=True, text=True, timeout=3)
    return out.returncode == 0


# ---------------- 统一入口 ----------------
def focus_window(handle: str | None) -> bool:
    """把指定窗口切到前台"""
    if not handle:
        return False
    try:
        if SYSTEM == "Windows":
            return _win_focus(handle)
        if SYSTEM == "Darwin":
            return _mac_focus(handle)
        return _linux_focus(handle)
    except Exception:
        return False


def capability_report() -> str:
    """返回当前平台的输入/窗口能力说明，展示在 UI 上"""
    if SYSTEM == "Windows":
        return "Windows：全局录制与回放可用，支持自动切换目标窗口。"
    if SYSTEM == "Darwin":
        return ("macOS：需在 系统设置 → 隐私与安全性 → 辅助功能 中授权本程序，"
                "否则录制/回放无效。")
    if os.environ.get("WAYLAND_DISPLAY"):
        return ("Linux (Wayland)：输入注入受限，建议切换到 X11 会话；"
                "窗口聚焦需要 xdotool。")
    return "Linux (X11)：需安装 xdotool 以支持窗口聚焦（sudo apt install xdotool）。"
