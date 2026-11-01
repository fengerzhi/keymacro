"""dpi.py —— 高 DPI 屏幕适配

问题背景
──────────────────────────────────────────────────────────
Windows 在「显示设置 → 缩放」不是 100% 时（常见 125% / 150% / 175%），
一个没有声明 DPI 感知的程序会看到**被系统缩放过的逻辑坐标**：

    实际屏幕 2560×1440，缩放 150%
    → 程序以为屏幕只有 1706×960
    → 鼠标在物理点 (1200, 900)，程序读到的是 (800, 600)

而 pynput 注入鼠标位置时，两种可能性都存在：
  · 进程未声明 DPI 感知 → 注入值被系统再缩放一次 → 点歪
  · 进程已声明 DPI 感知 → 注入值就是物理坐标 → 准确

因此修复办法只有一条：**在进程启动最早期声明 DPI 感知**，
让录制读坐标与回放写坐标都统一在物理像素坐标系里。

声明时机极其关键：必须在任何窗口创建（Tk 初始化）之前调用，
否则 Windows 已经按旧模式缓存了缩放信息，声明不再生效。
"""

from __future__ import annotations

import platform
import sys

SYSTEM = platform.system()

# 记录本进程生效的 DPI 模式，供 UI 展示与日志排查
_active_mode: str = "未处理"


def enable_dpi_awareness() -> str:
    """开启 DPI 感知，返回实际生效的模式描述

    必须在创建任何窗口之前调用。重复调用是安全的。
    """
    global _active_mode

    if SYSTEM != "Windows":
        _active_mode = "非 Windows，无需处理"
        return _active_mode

    import ctypes

    # ---- 首选：Per-Monitor V2（Win10 1703+，多显示器各自缩放都能正确处理）
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctx = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctx):
            _active_mode = "Per-Monitor V2"
            return _active_mode
    except Exception:
        pass

    # ---- 次选：Per-Monitor（Win8.1+）
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            _active_mode = "Per-Monitor"
            return _active_mode
    except Exception:
        pass

    # ---- 兜底：System DPI Aware（Vista+，主屏缩放能正确处理）
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            _active_mode = "System"
            return _active_mode
    except Exception:
        pass

    _active_mode = "失败（坐标可能按缩放比例偏移）"
    return _active_mode


def get_scale_factor() -> float:
    """当前主屏缩放比例（1.0 = 100%，1.5 = 150%）

    Windows 用 GetDpiForSystem，其它平台恒为 1.0。
    仅用于诊断提示，坐标换算本身不需要它（因为已统一为物理像素）。
    """
    if SYSTEM != "Windows":
        return 1.0
    import ctypes
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def screen_size() -> tuple[int, int]:
    """物理屏幕尺寸（宽, 高）"""
    if SYSTEM == "Windows":
        import ctypes
        try:
            # SM_CXSCREEN=0, SM_CYSCREEN=1
            return (ctypes.windll.user32.GetSystemMetrics(0),
                    ctypes.windll.user32.GetSystemMetrics(1))
        except Exception:
            pass
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        size = (r.winfo_screenwidth(), r.winfo_screenheight())
        r.destroy()
        return size
    except Exception:
        return (0, 0)


def diagnostic_report() -> str:
    """给用户看的一行诊断信息"""
    flag = "OK" if _active_mode not in ("未处理",) and "失败" not in _active_mode else "注意"
    parts = [f"DPI {flag}：{_active_mode}"]
    if SYSTEM == "Windows":
        scale = get_scale_factor()
        w, h = screen_size()
        parts.append(f"缩放 {scale*100:.0f}%  屏幕 {w}×{h}")
    return "　·　".join(parts)
