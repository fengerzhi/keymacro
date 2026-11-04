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


# ---------------- 屏幕上「这块是什么」 ----------------
# 找图时用来区分「任务栏里的图标」和「桌面上的同款图标」：
# 桌面上那处往往是图片文件缩略图或快捷方式，点了没反应/点错地方。
_DESKTOP_CLASSES = ("Progman", "WorkerW", "SysListView32")
_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
_TB_CACHE: tuple[float, list[tuple[int, int, int, int]]] = (0.0, [])
_U32 = None
_DPI_READY = False


def _user32():
    """带 argtypes 的 user32

    64 位下 restype 默认是 32 位 int，HWND 会被截断，类名就查不出来了，
    所以这里必须显式声明返回类型。
    """
    global _U32
    if _U32 is not None:
        return _U32
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.EnumWindows.restype = wintypes.BOOL
    _U32 = u
    return u


def _ensure_dpi_aware() -> None:
    """本模块给出的矩形/命中测试都是物理像素，进程必须先声明 DPI 感知（幂等）"""
    global _DPI_READY
    if _DPI_READY:
        return
    _DPI_READY = True
    try:
        from .dpi import enable_dpi_awareness
        enable_dpi_awareness()
    except Exception:
        pass


def _win_class(hwnd) -> str:
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(64)
    _user32().GetClassNameW(wintypes.HWND(hwnd), buf, 64)
    return buf.value


def taskbar_rects() -> list[tuple[int, int, int, int]]:
    """任务栏矩形列表 [(x, y, 宽, 高)]（多屏时有多条）；非 Windows 返回 []"""
    if SYSTEM != "Windows":
        return []
    global _TB_CACHE
    import time
    now = time.time()
    if now - _TB_CACHE[0] < 3.0:                 # 3 秒内复用，避免每次找图都枚举窗口
        return _TB_CACHE[1]
    _ensure_dpi_aware()
    out: list[tuple[int, int, int, int]] = []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            if _win_class(hwnd) in _TASKBAR_CLASSES:
                r = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                    w, h = r.right - r.left, r.bottom - r.top
                    if w > 0 and h > 0:
                        out.append((int(r.left), int(r.top), int(w), int(h)))
            return True

        cb = proc(_cb)                           # 必须持有引用，否则回调被回收
        user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
        user32.EnumWindows(cb, 0)
    except Exception:
        out = []
    _TB_CACHE = (now, out)
    return out


def point_where(x: int, y: int) -> str:
    """这个屏幕坐标上是什么：'taskbar' / 'desktop' / ''（普通窗口或判断不了）

    只在 Windows 上能给准确答案；其他平台一律返回 ''（调用方按原逻辑处理）。
    """
    try:
        if SYSTEM != "Windows":
            return ""
        for rx, ry, rw, rh in taskbar_rects():
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return "taskbar"
        return _win_point_class(x, y)
    except Exception:
        return ""


def _win_point_class(x: int, y: int) -> str:
    """用 WindowFromPoint 判断该点属于桌面还是普通窗口"""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return ""
    handles = [hwnd]
    root = user32.GetAncestor(wintypes.HWND(hwnd), 2)     # GA_ROOT
    if root and root != hwnd:
        handles.append(root)
    for h in handles:
        if _win_class(h) in _DESKTOP_CLASSES:
            return "desktop"
    return ""


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
