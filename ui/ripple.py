"""ripple.py —— 录制时的点击涟漪特效

效果
──────────────────────────────────────────────
录制中每次按下鼠标，在光标位置扩散水波纹（呼应程序图标的靶心），
让用户明确看到「这一下被录进去了」。左/右/中键不同颜色。

关键约束：特效层绝不能挡住或吃掉真实点击。

实现（Windows / Linux 统一「小窗口 + 键控镂空」）
──────────────────────────────────────────────
一个 180×180 的无边界小窗贴在点击位置：
· 窗口与画布背景统一用键控色 #010203
· Windows：-transparentcolor 键控 → 该颜色像素完全透明，
  只剩下画上去的圆环可见；再加 WS_EX_TRANSPARENT|LAYERED|NOACTIVATE 穿透
· Linux：tkinter 无 transparentcolor，改用 XShape 环形遮罩（等效）
· macOS：无对应机制，自动禁用

为什么不用全屏键控层：整屏 layered 窗口在部分 Windows 环境
（DWM 合成 / 远程桌面 / 某些显卡驱动）下不渲染，导致「看不到」；
180×180 小窗创建快、移动快、不盖任务栏、不抢焦点，稳定得多。
"""

from __future__ import annotations

import platform
import tkinter as tk

SYSTEM = platform.system()

# 按键配色（与 UI 主题一致）
COLORS = {
    "left": "#ffa25c",        # 亮橙（呼应图标靶心，比原 #ff8a3d 更亮堂）
    "right": "#66a8ff",       # 亮蓝
    "middle": "#c08cff",      # 亮紫
}

WIN_SIZE = 240                       # 涟漪小窗口边长（MAX_R*2 + 余量）
KEY_COLOR = "#010203"                # 键控色（近黑，非纯黑避免撞色）
# 描边色：淡淡的中性灰（原来的 #101010 近乎纯黑，在浅色界面上过于扎眼）
HALO_COLOR = "#6b6b6b"


# ---------------------------------------------------------------- 模式持久化
def _cfg_path() -> "Path":
    from pathlib import Path
    import sys
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "ripple_mode.txt"


def load_ripple_mode() -> str:
    """读上次保存的模式：auto / key / flash"""
    try:
        v = _cfg_path().read_text(encoding="utf-8").strip().lower()
        return v if v in ("auto", "key", "flash") else "auto"
    except Exception:
        return "auto"


def save_ripple_mode(mode: str) -> None:
    try:
        _cfg_path().write_text(mode, encoding="utf-8")
    except Exception:
        pass


def key_transparency_available() -> bool:
    """静态判断键控透明（-transparentcolor）当前是否可用。

    Windows 的 -transparentcolor 依赖 DWM 合成；关掉合成（部分远程桌面、
    精简系统）时不会生效。这里只用系统 API 判断，**不建窗口、不画探针**，
    避免启动瞬间在屏幕中央闪一个橙色方块。
    """
    if SYSTEM != "Windows":
        return True
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        hr = ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
        if hr != 0:
            return True        # 查不到就别擅自降级
        return bool(enabled.value)
    except Exception:
        return True


def _click_through_windows(win: tk.Toplevel) -> bool:
    """Windows：设置扩展样式让整窗穿透 + 不抢焦点"""
    import ctypes
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


def _flash_no_activate(win: tk.Toplevel) -> bool:
    """Windows：NOACTIVATE + TOPMOST（仅样式位，不动 LAYERED）"""
    import ctypes
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TRANSPARENT = 0x00000020     # 鼠标穿透（不依赖 LAYERED）
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT)
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x2; SWP_NOSIZE = 0x1
        SWP_NOACTIVATE = 0x10; SWP_SHOWWINDOW = 0x40
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


class RippleFeedback:
    """点击涟漪管理器

    用法：
        ripple = RippleFeedback(root)
        ripple.show(x, y, "left")     # 屏幕绝对坐标
        ripple.enabled = False
    """

    DURATION_MS = 620
    STEPS = 16
    MAX_R = 96                        # 最大扩散半径（足够醒目）

    def __init__(self, master: tk.Misc):
        self.master = master
        self.enabled = SYSTEM != "Darwin"   # macOS 无实现，自动禁用
        self._mode = {"Windows": "key", "Linux": "shape"}.get(SYSTEM, "off")
        self.mode_override: str | None = None   # "flash" = 兼容闪烁模式
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._anim_job = None
        self._last_color = COLORS["left"]
        self._shape_ok = False
        self._last_pos = (0, 0)

    def available(self) -> bool:
        """这台机器上能不能显示涟漪（macOS / 键控不可用时为 False）"""
        return self._mode != "off"

    # ============================================================ 小窗（共用）
    def _ensure_win(self) -> None:
        if self._win is not None:
            return
        w = tk.Toplevel(self.master)
        w.overrideredirect(True)
        w.resizable(False, False)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        w.geometry(f"{WIN_SIZE}x{WIN_SIZE}-2000-2000")   # 先放到屏幕外
        w.configure(bg=KEY_COLOR)
        c = tk.Canvas(w, bg=KEY_COLOR, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)

        if self._mode == "key":
            try:
                w.attributes("-transparentcolor", KEY_COLOR)
            except tk.TclError:
                self._mode = "off"              # 键控不可用则整体禁用
            # 穿透样式需要等窗口句柄就绪；越早越好
            w.after(30, lambda: _click_through_windows(w))
            w.after(300, lambda: _click_through_windows(w))   # 兜底再来一次

        self._win, self._canvas = w, c

    def _place(self, x: int, y: int) -> None:
        half = WIN_SIZE // 2
        self._win.geometry(f"{WIN_SIZE}x{WIN_SIZE}+{x - half}+{y - half}")

    def _park(self) -> None:
        """把复用窗口挪到屏幕外（自检/动画结束后回收，避免留一块底色）"""
        try:
            if self._win is not None:
                self._canvas.delete("all")
                self._win.geometry(f"{WIN_SIZE}x{WIN_SIZE}-4000-4000")
        except tk.TclError:
            pass

    # ============================================================ Linux 遮罩
    def _xshape_ring(self, half: int, r_outer: int, thickness: int) -> bool:
        """把 Bounding 设为以窗口中心为圆心的圆环；Input 置空穿透"""
        try:
            from Xlib import display
            from Xlib.ext import shape
            d = display.Display()
            wid = self._win.winfo_id()
            w = d.create_resource_object("window", wid)
            # winfo_id 指向 Tk 内部子窗口，顶层是它的父
            tree = w.query_tree()
            root_id = d.screen().root.id
            if tree.parent and tree.parent.id != root_id:
                w = tree.parent

            size = half * 2
            pm = d.screen().root.create_pixmap(size, size, 1)
            gc0 = pm.create_gc(foreground=0)
            pm.fill_rectangle(gc0, 0, 0, size, size)
            gc1 = pm.create_gc(foreground=1)
            # 外圆填充
            pm.poly_fill_arc(gc1, [(half - r_outer, half - r_outer,
                                    r_outer * 2, r_outer * 2, 0, 360 * 64)])
            # 内圆挖空（形成环）
            r_in = max(0, r_outer - thickness)
            if r_in > 0:
                gc0 = pm.create_gc(foreground=0)
                pm.poly_fill_arc(gc0, [(half - r_in, half - r_in,
                                        r_in * 2, r_in * 2, 0, 360 * 64)])
            shape.mask(w, shape.SO.Set, shape.SK.Bounding, 0, 0, pm)
            shape.rectangles(w, shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
            d.sync()
            return True
        except Exception:
            return False

    # ============================================================ 对外
    @property
    def mode(self) -> str:
        """生效模式：override 优先（flash 兼容模式不依赖键控色）"""
        if self.mode_override == "flash":
            return "flash"
        return self._mode

    def set_mode(self, m: str | None) -> None:
        """None=自动（键控/XShape）；'flash'=兼容闪烁模式"""
        self.mode_override = None if m not in ("flash",) else m

    def self_test(self) -> bool:
        """自检：真实画一发并抓屏，判断键控色在当前机器是否生效。

        返回 True=键控模式可正常显示；False=应降级到闪烁模式。
        仅在 Windows 有效；其他平台直接返回 True。
        """
        if SYSTEM != "Windows" or self._mode != "key":
            return True
        try:
            import time

            from PIL import ImageGrab
            import ctypes
            import ctypes.wintypes as wt

            self._ensure_win()
            if self._win is None:
                return False
            w = self._win
            # 取屏幕中央一小块做实验（避开任务栏）
            sw = w.winfo_screenwidth()
            sh = w.winfo_screenheight()
            tx, ty = sw // 2, sh // 3
            self._place(tx, ty)
            c = self._canvas
            if c is None:
                return False
            c.delete("probe")
            # 画一个纯色大实心块（若键控生效，块会显示；若整块消失说明被当键控色）
            c.create_rectangle(20, 20, WIN_SIZE - 20, WIN_SIZE - 20,
                               fill="#ff8a3d", outline="#ff8a3d", tags="probe")
            w.update_idletasks()
            w.update()
            time.sleep(0.35)          # 等 DWM 合成
            w.update()

            hwnd = ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
            rc = wt.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.right <= rc.left:
                return False
            img = ImageGrab.grab(bbox=(rc.left, rc.top, rc.right, rc.bottom))
            px = list(img.convert("RGB").getdata())
            # 统计接近橙色的像素（说明块真的显示了）
            orange = sum(1 for (r, g, b) in px
                         if r > 180 and 70 < g < 190 and b < 110)
            c.delete("probe")
            ratio = orange / max(1, len(px))
            return ratio > 0.2        # 实心块应占 ~70% 面积
        except Exception:
            return True               # 自检本身出错就不擅自降级
        finally:
            # 自检结束后必须彻底收起窗口：清空画布 + 挪到屏幕外
            # （否则会在屏幕中央留一块橙块，且干扰首次点击的涟漪）
            self._park()

    def show(self, x: int, y: int, button: str = "left") -> None:
        """在屏幕绝对坐标 (x, y) 播放涟漪（按下瞬间调用）"""
        if not self.enabled or self.mode == "off":
            return
        color = COLORS.get(button, COLORS["left"])
        self._last_color = color
        self._last_pos = (x, y)

        if self.mode == "flash":
            self._show_flash(x, y, color)
        elif self.mode in ("key", "shape"):
            self._show_ring(x, y, color)

    # ---------- 兼容闪烁模式（不依赖键控色，任何环境都能显示） ----------
    FLASH_SIZE = 132
    FLASH_MS = 150

    def _show_flash(self, x: int, y: int, color: str) -> None:
        half = self.FLASH_SIZE // 2
        try:
            w = tk.Toplevel(self.master)
            w.overrideredirect(True)
            w.resizable(False, False)
            try:
                w.attributes("-topmost", True)
            except tk.TclError:
                pass
            w.geometry(f"{self.FLASH_SIZE}x{self.FLASH_SIZE}+{x - half}+{y - half}")
            w.configure(bg="#1b1b1b")
            # 不抢焦点（不用 LAYERED/TRANSPARENT —— 兼容模式追求最大兼容）
            try:
                _flash_no_activate(w)
            except Exception:
                pass
            c = tk.Canvas(w, bg="#1b1b1b", highlightthickness=0, bd=0)
            c.pack(fill="both", expand=True)
        except tk.TclError:
            return

        steps, dur = 4, self.FLASH_MS
        cx = cy = half

        def frame(step: int) -> None:
            if not w.winfo_exists():
                return
            t = step / steps
            r = int(46 * (1 - (1 - t) ** 2)) + 6
            width = max(3, int(8 * (1 - t)) + 3)
            c.delete("ring")
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          outline=color, width=width, tags="ring")
            if step < steps:
                w.after(dur // steps, frame, step + 1)
            else:
                w.destroy()

        frame(0)

    # ---------- 动画主体（两平台共用，遮罩逻辑按平台分派） ----------
    def _show_ring(self, x, y, color):
        first_time = self._win is None
        self._ensure_win()
        if self._win is None or self._canvas is None:
            return
        w, c = self._win, self._canvas
        self._place(x, y)
        try:
            w.lift()
            if self._mode == "key":
                w.configure(bg=KEY_COLOR)
                c.configure(bg=KEY_COLOR)
            else:
                w.configure(bg=color)
                c.configure(bg=color)
        except tk.TclError:
            return

        if first_time:
            # 首次创建：分层/键控样式要等窗口映射后才是真的生效，
            # 此时立刻跑动画会在合成完成前就播完（表现为「第一次点击没涟漪」）。
            # 先把窗口推上去 + 画一帧空画面逼出合成，稍等再起动画。
            try:
                w.update_idletasks()
                w.update()
            except tk.TclError:
                return
            self._place(x, y)
            w.after(45, lambda: self._run_ring(w, c, x, y, color))
            return
        self._run_ring(w, c, x, y, color)

    def _run_ring(self, w, c, x, y, color):
        if not w.winfo_exists():
            return
        try:
            self._place(x, y)
            w.lift()
        except tk.TclError:
            return

        def frame(step: int) -> None:
            if not w.winfo_exists():
                return
            t = step / self.STEPS
            r_visible = int(self.MAX_R * (1 - (1 - t) ** 2))
            # 线宽：最粗 14px，最细也有 4px（保证末帧仍可见）
            width = max(4, int(14 * (1 - t)) + 4)
            c.delete("ripple")

            if self._mode == "key":
                # 键控模式：窗口已贴到点击位置，圆环以画布中心为圆心
                # 造型对齐程序图标（靶心），无深色描边，整体高亮度
                cx = cy = WIN_SIZE // 2

                def hsl_lighten(rgb, k):
                    """按比例调亮：向白靠拢，避免变暗发灰"""
                    return tuple(min(255, int(c + (255 - c) * k)) for c in rgb)

                base = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

                # 外圈：橙色主环，向外扩散并提亮
                ow = max(5, int(11 * (1 - t)) + 4)
                col = hsl_lighten(base, t * 0.32)
                c.create_oval(cx - r_visible, cy - r_visible,
                              cx + r_visible, cy + r_visible,
                              outline="#%02x%02x%02x" % col,
                              width=ow, tags="ripple")
                # 内圈：同色系浅色环
                r2 = int(r_visible * 0.58)
                if r2 > 8:
                    gw = max(3, int(8 * (1 - t)) + 3)
                    col2 = hsl_lighten(base, 0.45 + t * 0.25)
                    c.create_oval(cx - r2, cy - r2, cx + r2, cy + r2,
                                  outline="#%02x%02x%02x" % col2,
                                  width=gw, tags="ripple")
                # 中心点：亮色实心，前 78% 保留
                if t <= 0.78:
                    rr = 10
                    c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                  fill="#%02x%02x%02x" % hsl_lighten(base, 0.12),
                                  outline="", tags="ripple")
            else:
                # XShape 模式：无描边，直接镂出彩色环
                half = WIN_SIZE // 2
                thick = max(5, int(13 * (1 - t)))
                self._shape_ok = self._xshape_ring(
                    half, r_visible + thick // 2, thick) or self._shape_ok

            if step < self.STEPS:
                self._anim_job = w.after(
                    self.DURATION_MS // self.STEPS, frame, step + 1)
            else:
                c.delete("ripple")
                if self._mode == "shape":
                    self._hide_shape_win()

        frame(0)

    def _hide_shape_win(self) -> None:
        try:
            from Xlib import display
            from Xlib.ext import shape
            d = display.Display()
            w = d.create_resource_object("window", self._win.winfo_id())
            tree = w.query_tree()
            root_id = d.screen().root.id
            if tree.parent and tree.parent.id != root_id:
                w = tree.parent
            shape.rectangles(w, shape.SO.Set, shape.SK.Bounding, 0, 0, 0, [])
            shape.rectangles(w, shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
            d.sync()
        except Exception:
            pass

    def close(self) -> None:
        if self._anim_job and self._win:
            try:
                self._win.after_cancel(self._anim_job)
            except Exception:
                pass
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None
        self._canvas = None
