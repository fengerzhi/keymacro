"""hud.py —— 录制 / 回放时的迷你控制条（方案 A：顶部居中细条）

设计要点
─────────────────────────────────────────────────────────
· 形态：430×48 圆角细条，贴屏幕顶部居中，始终置顶
· 状态：录制（红）/ 暂停（橙）/ 回放（蓝）/ 空闲（灰），颜色一眼区分
· 交互：左握把拖拽移动（位置记住）· 双击握把回主窗口
        · 悬停出现「⋯」更多菜单 · 滚轮调「保留移动点」数量
· 关键约束：
    1) 点击条上的按钮 **不能抢走目标窗口的焦点**，否则录制会中断
       → 用 -alpha + 分层窗口 + WS_EX_NOACTIVATE，按钮用 Canvas 手绘
    2) 这一条自己的矩形要加进 recorder.exclusion，避免把操作录进脚本
       → show() 时自动把几何矩形回调给外部

实现说明
─────────────────────────────────────────────────────────
窗口用 overrideredirect(True) 无边框 + attributes("-topmost", True) 置顶。
全部控件用 Canvas 手绘（不用 ttk），才能做出圆角胶囊与自定义配色，
同时便于精确控制命中区域（拖动 / 按钮 / 握把三分区）。
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import Callable

from core.window import SYSTEM

# ---------------------------------------------------------------- 外观常量
BAR_W = 430
BAR_H = 48
RADIUS = 24

C_BG = "#1c2130"          # 主体深色
C_BG_HOVER = "#232a3c"    # 悬停略微提亮
C_BORDER = "#333c52"
C_TEXT = "#eef2fa"
C_SUB = "#8c98af"
C_DIM = "#6b7690"
C_REC = "#ff4a4a"
C_PLAY = "#4aa0ff"
C_PAUSE = "#ffa33d"
C_IDLE = "#78849b"
C_ACCENT = "#ff8a3d"
C_OK = "#42d08a"

# 状态 → 主色
STATE_COLORS = {
    "recording": C_REC,
    "paused": C_PAUSE,
    "playback": C_PLAY,
    "idle": C_IDLE,
}

GRIP_W = 12               # 左侧拖拽握把宽度
BTN_R = 14                # 圆形按钮半径
BTN_GAP = 10              # 按钮间距


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(fg: str, bg: str, alpha: float) -> str:
    """在 bg 上按 alpha 混 fg，返回 #rrggbb（tkinter 无 rgba 支持）"""
    f, b = _hex_to_rgb(fg), _hex_to_rgb(bg)
    return "#%02x%02x%02x" % tuple(
        int(round(x * alpha + y * (1 - alpha))) for x, y in zip(f, b))


class MiniBar(tk.Toplevel):
    """录制 / 回放迷你控制条

    对外接口
    ─────────────────────────────────────────────
    show(state, **kw)     显示并切到指定状态
    hide()                隐藏
    set_state(state)      仅切状态
    update_stats(n, c, ms)  刷新计数与计时
    set_paused(bool)      记录暂停态（影响暂停按钮图标）
    on_stop / on_pause / on_play / on_restore /
    on_save / on_discard / on_wait / on_moves_change
                          外部注入的回调
    geometry_rect()       返回屏幕绝对矩形，用于 recorder.exclusion
    """

    def __init__(self, master: tk.Misc, *,
                 on_stop: Callable[[], None] | None = None,
                 on_pause: Callable[[], None] | None = None,
                 on_play: Callable[[], None] | None = None,
                 on_restore: Callable[[], None] | None = None,
                 on_save: Callable[[], None] | None = None,
                 on_discard: Callable[[], None] | None = None,
                 on_wait: Callable[[], None] | None = None,
                 on_moves_change: Callable[[int], None] | None = None):

        super().__init__(master)
        self.master_app = master
        self.on_stop = on_stop
        self.on_pause = on_pause
        self.on_play = on_play
        self.on_restore = on_restore
        self.on_save = on_save
        self.on_discard = on_discard
        self.on_wait = on_wait
        self.on_moves_change = on_moves_change

        self.state = "idle"
        self.paused = False
        self.events_n = 0
        self.clicks_n = 0
        self.elapsed_ms = 0
        self._blink_on = True
        self._hover = False
        self._menu_open = False
        self._moves_before = 2

        # 无边框、置顶、不进任务栏
        self.withdraw()
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        if SYSTEM == "Windows":
            try:
                self.attributes("-alpha", 0.97)     # 允许轻微透明，避免死板
            except tk.TclError:
                pass

        self.configure(bg=C_BG)

        # 菜单（先建好，展开时再 place）
        self.menu = tk.Frame(self, bg="#232a3c", highlightthickness=1,
                             highlightbackground=C_BORDER)

        self.canvas = tk.Canvas(self, width=BAR_W, height=BAR_H,
                                bg=C_BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.geometry(f"{BAR_W}x{BAR_H}")
        self._place_default()

        # 事件绑定
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<MouseWheel>", self._on_wheel)          # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel_step(1))   # Linux
        self.canvas.bind("<Button-5>", lambda e: self._wheel_step(-1))

        self._drag_origin: tuple[int, int] | None = None
        self._win_origin: tuple[int, int] = (0, 0)
        self._press_zone: str | None = None

        self._blink_job = None
        self._hover_job = None
        self._draw()

    # ============================================================ 位置
    def _screen_size(self) -> tuple[int, int]:
        try:
            from core.dpi import screen_size
            return screen_size()
        except Exception:
            return self.winfo_screenwidth(), self.winfo_screenheight()

    def _place_default(self) -> None:
        """首次出现：屏幕顶部居中（记忆上次拖放位置）"""
        saved = _load_pos()
        sw, sh = self._screen_size()
        if saved:
            x, y = saved
            # 位置失效（换了分辨率）就回退到顶部居中
            if not (0 <= x <= sw - 60 and 0 <= y <= sh - 40):
                saved = None
        if saved:
            x, y = saved
        else:
            x = (sw - BAR_W) // 2
            y = 6
        self.geometry(f"{BAR_W}x{BAR_H}+{x}+{y}")

    def geometry_rect(self) -> tuple[int, int, int, int]:
        """屏幕绝对矩形 (x, y, w, h)，用于录制排除区"""
        self.update_idletasks()
        try:
            x = self.winfo_rootx()
            y = self.winfo_rooty()
        except tk.TclError:
            x = y = 0
        return (x, y, BAR_W, BAR_H + (self.menu.winfo_height()
                                      if self._menu_open else 0))

    # ============================================================ 显示 / 隐藏
    def show(self, state: str = "recording", *, events: int = 0,
             clicks: int = 0, elapsed_ms: int = 0) -> None:
        self.state = state
        self.paused = False
        self.events_n = events
        self.clicks_n = clicks
        self.elapsed_ms = elapsed_ms
        self.deiconify()
        try:
            self.attributes("-topmost", True)
            self.lift()
        except tk.TclError:
            pass
        self._place_default()
        self._draw()
        self._start_blink()
        # Windows：确保不抢焦点
        if SYSTEM == "Windows":
            self.after(30, self._make_no_activate)

    def hide(self) -> None:
        self._stop_blink()
        self._close_menu()
        # 记住位置
        try:
            _save_pos(self.winfo_rootx(), self.winfo_rooty())
        except tk.TclError:
            pass
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def set_state(self, state: str) -> None:
        if self.state == state:
            return
        self.state = state
        if state == "recording":
            self._start_blink()
        else:
            self._stop_blink()
        self._draw()

    def update_stats(self, events: int, clicks: int, elapsed_ms: int) -> None:
        self.events_n = events
        self.clicks_n = clicks
        self.elapsed_ms = elapsed_ms
        self._draw()

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        self._draw()

    def _make_no_activate(self) -> None:
        """WS_EX_NOACTIVATE：点击窗口不夺取焦点（关键！否则录制会被打断）"""
        if SYSTEM != "Windows":
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_TOPMOST = 0x00000008
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            user32 = ctypes.windll.user32
            cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                cur | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        except Exception:
            pass

    # ============================================================ 闪烁
    def _start_blink(self) -> None:
        if self._blink_job:
            return
        self._tick_blink()

    def _tick_blink(self) -> None:
        if not self.winfo_exists():
            return
        if self.state == "recording" and not self.paused:
            self._blink_on = not self._blink_on
            self._draw_dot()
        self._blink_job = self.after(500, self._tick_blink)

    def _stop_blink(self) -> None:
        if self._blink_job:
            try:
                self.after_cancel(self._blink_job)
            except Exception:
                pass
            self._blink_job = None
        self._blink_on = True

    # ============================================================ 绘制
    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        base = C_BG_HOVER if self._hover else C_BG
        self.configure(bg=base)
        c.configure(bg=base)

        self._round_rect(0, 0, BAR_W, BAR_H, RADIUS, fill=base,
                         outline=_mix(C_BORDER, base, 0.85))

        # 左侧握把
        self._grip()

        self._draw_dot()
        self._draw_texts()
        self._draw_buttons()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _grip(self) -> None:
        c = self.canvas
        # 弧形贴合左端：用同色圆角块覆盖
        self._round_rect(1, 1, GRIP_W * 2, BAR_H - 1, RADIUS - 1,
                         fill=_mix(C_ACCENT, C_BG, 0.92), outline="")
        c.create_rectangle(GRIP_W, 1, GRIP_W * 2, BAR_H - 1,
                           fill=_mix(C_ACCENT, C_BG, 0.92), outline="")
        c.create_rectangle(GRIP_W * 2 - 1, 1, GRIP_W * 2 + 1, BAR_H - 1,
                           fill=_mix(C_ACCENT, C_BG, 0.92), outline="")
        # 三点纹
        for i in (-1, 0, 1):
            yy = BAR_H / 2 + i * 5
            c.create_oval(GRIP_W / 2 - 1.2, yy - 1.2, GRIP_W / 2 + 1.2, yy + 1.2,
                          fill=_mix("#ffffff", C_ACCENT, 0.62), outline="")

    def _draw_dot(self) -> None:
        c = self.canvas
        c.delete("dot")
        color = STATE_COLORS.get(self.state, C_IDLE)
        cx, cy, r = GRIP_W + 22, BAR_H / 2, 6
        if self.state == "paused":
            # 暂停态：两个小竖条代替圆点
            for dx in (-2.6, 2.6):
                c.create_rectangle(cx + dx - 1.6, cy - r + 1, cx + dx + 1.6,
                                   cy + r - 1, fill=color, outline="", tags="dot")
        elif self.state == "idle":
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color,
                          outline="", tags="dot")
        else:
            if self._blink_on:
                for rr, a in ((r * 1.9, 0.16), (r * 1.45, 0.26)):
                    c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                  fill=_mix(color, C_BG, a), outline="", tags="dot")
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color,
                              outline="", tags="dot")
            else:
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              fill=_mix(color, C_BG, 0.45), outline="", tags="dot")

    def _fmt_ms(self, ms: int) -> str:
        s = max(0, ms // 1000)
        return f"{s // 60:02d}:{s % 60:02d}"

    def _draw_texts(self) -> None:
        c = self.canvas
        c.delete("txt")
        base = C_BG_HOVER if self._hover else C_BG
        color = STATE_COLORS.get(self.state, C_IDLE)
        x = GRIP_W + 38

        if self.state == "recording":
            tag = "PAUSE" if self.paused else "REC"
            c.create_text(x, BAR_H / 2 - 0.5, text=tag, anchor="w",
                          fill=color, font=("Segoe UI", 9, "bold"), tags="txt")
            x += 34
            c.create_text(x, BAR_H / 2, text=self._fmt_ms(self.elapsed_ms),
                          anchor="w", fill=C_TEXT,
                          font=("Consolas", 12, "bold"), tags="txt")
            x += 52
            c.create_line(x, BAR_H / 2 - 9, x, BAR_H / 2 + 9,
                          fill=_mix(C_BORDER, base, 0.9), tags="txt")
            x += 12
            c.create_text(x, BAR_H / 2 - 7, text=f"事件 {self.events_n}",
                          anchor="w", fill=C_TEXT,
                          font=("Segoe UI", 8), tags="txt")
            c.create_text(x, BAR_H / 2 + 7, text=f"点击 {self.clicks_n}",
                          anchor="w", fill=C_SUB,
                          font=("Segoe UI", 8), tags="txt")
        elif self.state == "playback":
            c.create_text(x, BAR_H / 2 - 0.5, text="PLAY", anchor="w",
                          fill=color, font=("Segoe UI", 9, "bold"), tags="txt")
            x += 40
            c.create_text(x, BAR_H / 2, text=self._play_label,
                          anchor="w", fill=C_TEXT,
                          font=("Segoe UI", 9, "bold"), tags="txt")
            x += 52
            # 进度条
            bw = 54
            c.create_line(x, BAR_H / 2, x + bw, BAR_H / 2,
                          fill=_mix("#ffffff", base, 0.14), width=5,
                          capstyle="round", tags="txt")
            done = int(bw * max(0.0, min(1.0, self.play_progress)))
            if done > 0:
                c.create_line(x, BAR_H / 2, x + done, BAR_H / 2,
                              fill=color, width=5, capstyle="round", tags="txt")
            x += bw + 10
            c.create_text(x, BAR_H / 2, text=self._step_label, anchor="w",
                          fill=C_SUB, font=("Segoe UI", 8), tags="txt")
        else:
            c.create_text(x, BAR_H / 2 - 7, text="空闲", anchor="w",
                          fill=C_SUB, font=("Segoe UI", 9), tags="txt")
            c.create_text(x, BAR_H / 2 + 7, text="F11 开始录制", anchor="w",
                          fill=_mix(C_DIM, base, 0.9),
                          font=("Segoe UI", 8), tags="txt")

    # 回放态的文字（由 update_play 写入）
    _play_label = "回放中"
    _step_label = ""
    play_progress = 0.0

    def update_play(self, lap: int, total: int, step: int, steps: int) -> None:
        self._play_label = f"第 {lap}/{total} 轮"
        self._step_label = f"步骤 {step}/{steps}"
        self.play_progress = (step / steps) if steps else 0.0
        self._draw()

    # ------------------------------------------------------------ 按钮
    def _button_centers(self) -> list[tuple[str, int]]:
        """返回 [(zone, 圆心x)]，从右往左排布"""
        cy = BAR_H // 2
        out: list[tuple[str, int]] = []
        x = BAR_W - 24
        if self.state == "recording":
            out.append(("stop", x))            # 停止（红圆）
            x -= BTN_R * 2 + BTN_GAP
            out.append(("pause", x))           # 暂停 / 继续
            x -= BTN_R * 2 + BTN_GAP
        elif self.state == "playback":
            out.append(("stop", x))            # 停止回放
            x -= BTN_R * 2 + BTN_GAP
        else:
            out.append(("record", x))          # 开始录制
            x -= BTN_R * 2 + BTN_GAP
            out.append(("play", x))            # 回放
            x -= BTN_R * 2 + BTN_GAP
        out.append(("more", x))                # 更多
        return out

    def _draw_buttons(self) -> None:
        c = self.canvas
        c.delete("btn")
        cy = BAR_H // 2
        base = C_BG_HOVER if self._hover else C_BG
        color = STATE_COLORS.get(self.state, C_IDLE)

        for zone, cx in self._button_centers():
            r = BTN_R
            if zone == "stop":
                fill = _mix(C_REC, base, 0.92) if not self.paused else _mix(C_REC, base, 0.9)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                              outline="", tags="btn")
                s = 5.2
                c.create_rectangle(cx - s, cy - s, cx + s, cy + s,
                                   fill="#ffffff", outline="", tags="btn")
            elif zone == "pause":
                fill = _mix("#ffffff", base, 0.08)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                              outline=_mix("#ffffff", base, 0.16), tags="btn")
                if self.paused:
                    # 继续：三角形
                    c.create_polygon(cx - 4, cy - 6, cx - 4, cy + 6, cx + 6, cy,
                                     fill=C_OK, outline="", tags="btn")
                else:
                    for dx in (-3.4, 3.4):
                        c.create_rectangle(cx + dx - 2, cy - 6, cx + dx + 2,
                                           cy + 6, fill=C_TEXT, outline="",
                                           tags="btn")
            elif zone == "record":
                fill = _mix(C_ACCENT, base, 0.9)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                              outline="", tags="btn")
                c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#1e140a",
                              outline="", tags="btn")
            elif zone == "play":
                fill = _mix("#ffffff", base, 0.08)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                              outline=_mix("#ffffff", base, 0.16), tags="btn")
                c.create_polygon(cx - 4.5, cy - 6.5, cx - 4.5, cy + 6.5,
                                 cx + 6.5, cy, fill=C_PLAY, outline="",
                                 tags="btn")
            elif zone == "more":
                fill = _mix("#ffffff", base, 0.1 if self._menu_open else 0.06)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                              outline="", tags="btn")
                for i in (-1, 0, 1):
                    yy = cy + i * 4.6
                    c.create_oval(cx - 1.5, yy - 1.5, cx + 1.5, yy + 1.5,
                                  fill=C_TEXT, outline="", tags="btn")

    # ============================================================ 命中判断
    def _zone_at(self, x: int, y: int) -> str:
        cy = BAR_H // 2
        for zone, cx in self._button_centers():
            if (x - cx) ** 2 + (y - cy) ** 2 <= (BTN_R + 4) ** 2:
                return zone
        if x < GRIP_W + 2:
            return "grip"
        return "body"

    # ============================================================ 鼠标事件
    def _on_press(self, e) -> None:
        zone = self._zone_at(e.x, e.y)
        self._press_zone = zone
        self._drag_origin = (e.x_root, e.y_root)
        self._win_origin = (self.winfo_rootx(), self.winfo_rooty())
        self._dragged = False

    def _on_drag(self, e) -> None:
        if self._press_zone not in ("grip", "body") or not self._drag_origin:
            return
        dx = e.x_root - self._drag_origin[0]
        dy = e.y_root - self._drag_origin[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragged = True
        nx = self._win_origin[0] + dx
        ny = self._win_origin[1] + dy
        # 限制在屏幕内
        sw, sh = self._screen_size()
        nx = max(-BAR_W // 2, min(nx, sw - BAR_W // 2))
        ny = max(0, min(ny, sh - BAR_H))
        self.geometry(f"+{nx}+{ny}")
        self._position_menu()

    def _on_release(self, e) -> None:
        zone = self._press_zone
        dragged = getattr(self, "_dragged", False)
        self._press_zone = None
        self._drag_origin = None
        if dragged:
            try:
                _save_pos(self.winfo_rootx(), self.winfo_rooty())
            except tk.TclError:
                pass
            return
        if zone is None:
            return
        if zone == "stop":
            self._fire(self.on_stop)
        elif zone == "pause":
            self._fire(self.on_pause)
        elif zone == "record":
            self._fire(self.on_restore)      # 空闲态点「录制」→ 回主窗口开始录制
        elif zone == "play":
            self._fire(self.on_play)
        elif zone == "more":
            self._toggle_menu()

    def _on_double(self, e) -> None:
        if self._zone_at(e.x, e.y) == "grip":
            self._fire(self.on_restore)

    def _on_motion(self, e) -> None:
        zone = self._zone_at(e.x, e.y)
        cursor = "hand2" if zone in ("stop", "pause", "record", "play",
                                     "more") else (
            "fleur" if zone == "grip" else "arrow")
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            pass
        if not self._hover:
            self._hover = True
            self._draw()

    def _on_leave(self, e) -> None:
        if self._hover:
            self._hover = False
            if not self._menu_open:
                self._draw()

    def _fire(self, cb) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    # ============================================================ 滚轮调移动点
    def _on_wheel(self, e) -> None:
        delta = 1 if getattr(e, "delta", 0) > 0 else -1
        self._wheel_step(delta)

    def _wheel_step(self, delta: int) -> None:
        self._moves_before = max(0, min(10, self._moves_before + delta))
        self._fire_moves()
        self._toast(f"保留移动点：{self._moves_before}")

    def _fire_moves(self) -> None:
        if self.on_moves_change:
            try:
                self.on_moves_change(self._moves_before)
            except Exception:
                pass

    def set_moves_before(self, n: int) -> None:
        self._moves_before = max(0, min(10, int(n)))

    # ============================================================ 更多菜单
    def _toggle_menu(self) -> None:
        if self._menu_open:
            self._close_menu()
        else:
            self._open_menu()

    def _open_menu(self) -> None:
        self._menu_open = True
        self._build_menu()
        self.menu.update_idletasks()          # 先真实化，否则 reqheight=1
        menu_h = max(60, self.menu.winfo_reqheight())
        self.geometry(f"{BAR_W}x{BAR_H + menu_h}")
        self.menu.place(x=0, y=BAR_H, width=BAR_W)
        self._position_menu()
        self._draw()

    def _close_menu(self) -> None:
        if not self._menu_open:
            return
        self._menu_open = False
        try:
            self.menu.place_forget()
            self.geometry(f"{BAR_W}x{BAR_H}")
        except tk.TclError:
            pass
        self._draw()

    def _position_menu(self) -> None:
        """菜单始终贴条的下沿（条本身已用 geometry 撑高，无需额外偏移）"""
        if self._menu_open:
            self.menu.place(x=0, y=BAR_H, width=BAR_W)
            self.menu.lift()          # 菜单创建早于 canvas，必须抬到最上层

    def _build_menu(self) -> None:
        for w in self.menu.winfo_children():
            w.destroy()

        def item(text, cb, *, danger=False, hint=""):
            row = tk.Frame(self.menu, bg="#232a3c", cursor="hand2")
            row.pack(fill="x")
            lbl = tk.Label(row, text=f"  {text}", bg="#232a3c",
                           fg=C_REC if danger else C_TEXT,
                           font=("Microsoft YaHei UI", 9), anchor="w",
                           padx=10, pady=7)
            lbl.pack(side="left", fill="x", expand=True)
            if hint:
                tk.Label(row, text=hint, bg="#232a3c", fg=C_DIM,
                         font=("Segoe UI", 8), padx=10).pack(side="right")

            def enter(_=None):
                for w in (row, lbl):
                    w.configure(bg="#2e3750")
                for c in row.winfo_children():
                    if isinstance(c, tk.Label):
                        c.configure(bg="#2e3750")

            def leave(_=None):
                for w in (row, lbl):
                    w.configure(bg="#232a3c")
                for c in row.winfo_children():
                    if isinstance(c, tk.Label):
                        c.configure(bg="#232a3c")

            def click(_=None):
                self._close_menu()
                self._fire(cb)

            for w in (row, lbl):
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", click)

        item("💾  保存到脚本库", self.on_save)
        item("⏱  插入等待 1 秒", self.on_wait)
        item("✖  放弃本次录制", self.on_discard, danger=True)
        tk.Frame(self.menu, bg=C_BORDER, height=1).pack(fill="x")
        item("🖥  回到主窗口", self.on_restore, hint="双击握把")
        item("🎚  保留移动点", None, hint="滚轮调整")
        # 移动点滑杆
        slider = tk.Scale(self.menu, from_=0, to=10, orient="horizontal",
                          bg="#232a3c", fg=C_TEXT, troughcolor="#161a26",
                          highlightthickness=0, bd=0, sliderrelief="flat",
                          activebackground=C_ACCENT, showvalue=True,
                          font=("Segoe UI", 8),
                          command=lambda v: self._on_moves_scale(int(float(v))))
        slider.set(self._moves_before)
        slider.pack(fill="x", padx=12, pady=(0, 8))

    def _on_moves_scale(self, v: int) -> None:
        if v == self._moves_before:
            return
        self._moves_before = v
        self._fire_moves()

    # ============================================================ 轻提示
    def _toast(self, text: str, ms: int = 1200) -> None:
        """在条下方短暂显示一行提示，不改动布局"""
        if not hasattr(self, "_toast_win"):
            self._toast_win = None
        if self._toast_win and self._toast_win.winfo_exists():
            self._toast_win.destroy()
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg="#2e3750")
        try:
            top.attributes("-topmost", True)
            top.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        tk.Label(top, text=f" {text} ", bg="#2e3750", fg=C_TEXT,
                 font=("Microsoft YaHei UI", 9), padx=8, pady=4).pack()
        top.update_idletasks()
        x = self.winfo_rootx() + (BAR_W - top.winfo_width()) // 2
        y = self.winfo_rooty() + BAR_H + 8
        top.geometry(f"+{x}+{y}")
        self._toast_win = top
        self.after(ms, lambda: top.destroy() if top.winfo_exists() else None)


# ================================================================ 位置记忆
def _pos_file() -> str:
    try:
        from core.library import app_dir
        base = app_dir()
    except Exception:
        base = os.path.expanduser("~/.按键精灵")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "hud_pos.json")


def _load_pos() -> tuple[int, int] | None:
    import json
    try:
        with open(_pos_file(), encoding="utf-8") as fh:
            d = json.load(fh)
        return int(d["x"]), int(d["y"])
    except Exception:
        return None


def _save_pos(x: int, y: int) -> None:
    import json
    try:
        with open(_pos_file(), "w", encoding="utf-8") as fh:
            json.dump({"x": int(x), "y": int(y)}, fh)
    except Exception:
        pass
