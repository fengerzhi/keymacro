"""calibrate.py —— 坐标校准工具

用途
──────────────────────────────────────────────────────────
即使开启了 DPI 感知，少数环境（远程桌面、虚拟机、
多显示器混合缩放、某些安全软件挂钩）仍可能产生坐标偏移。
本工具让用户「点两下」就能自动算出校准参数。

原理
──────────────────────────────────────────────────────────
用户在屏幕上实际点击两个目标点，程序同时记录：
    · 脚本认为的点  (target) —— 由程序移动到指定位置后读取
    · 真实落点      (actual) —— 用户手动点击的位置

两点即可解出：
    actual = target × scale + offset
  即：
    scale = (a2 - a1) / (t2 - t1)
    offset = a1 - t1 × scale

只有 scale ≠ 1 或 offset ≠ 0 时才需要校准。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


class _MarkOverlay(tk.Toplevel):
    """全屏半透明标记层：显示目标点，并捕获用户点击位置"""

    def __init__(self, master, points: list[tuple[int, int]],
                 on_done: Callable[[list[tuple[int, int]]], None]):
        super().__init__(master)
        self.points = points
        self.on_done = on_done
        self.picked: list[tuple[int, int]] = []

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.30)
        self.configure(bg="#000000")
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f"{sw}x{sh}+0+0")

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self._draw_targets()

        # 提示条
        self.hint = tk.Label(self, text="", bg="#111111", fg="#ffffff",
                             font=("Microsoft YaHei", 12), padx=18, pady=10)
        self.hint.place(relx=0.5, rely=0.06, anchor="center")
        self._update_hint()

        self.canvas.bind("<Button-1>", self._on_click)
        self.bind("<Escape>", lambda e: self._finish(None))
        self.focus_force()
        self.grab_set()

    def _draw_targets(self) -> None:
        """把目标点画成红色十字 + 圆圈"""
        self.canvas.delete("target")
        for i, (x, y) in enumerate(self.points):
            done = i < len(self.picked)
            color = "#4caf50" if done else "#ff1744"
            r = 22
            self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                    outline=color, width=3, tags="target")
            self.canvas.create_line(x - r - 12, y, x + r + 12, y,
                                    fill=color, width=2, tags="target")
            self.canvas.create_line(x, y - r - 12, x, y + r + 12,
                                    fill=color, width=2, tags="target")
            self.canvas.create_text(x, y - r - 26, text=f"目标点 {i+1}",
                                    fill=color,
                                    font=("Microsoft YaHei", 10, "bold"),
                                    tags="target")

    def _update_hint(self) -> None:
        n = len(self.points)
        i = len(self.picked)
        if i < n:
            self.hint.configure(
                text=f"请用鼠标【精确点击】红色标记的中心  （{i+1}/{n}）\n"
                     f"按 Esc 取消")
        else:
            self.hint.configure(text="已完成，正在计算…")

    def _on_click(self, e) -> None:
        if len(self.picked) >= len(self.points):
            return
        self.picked.append((e.x_root, e.y_root))
        self._draw_targets()
        self._update_hint()
        if len(self.picked) >= len(self.points):
            self.after(220, lambda: self._finish(self.picked))

    def _finish(self, result) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_done(result)


def run_calibration(master: tk.Misc,
                    targets: list[tuple[int, int]],
                    on_done: Callable[[list[tuple[int, int]] | None], None]) -> None:
    """显示全屏取点层

    :param targets: 要用户点击的目标点（屏幕绝对坐标）
    :param on_done: 回调，参数为用户实际点击的坐标列表；取消时传 None
    """
    _MarkOverlay(master, targets, on_done)


def compute_calibration(targets: list[tuple[int, int]],
                        actuals: list[tuple[int, int]]
                        ) -> tuple[float, int, int]:
    """根据「目标点」和「实际落点」计算 (scale, offset_x, offset_y)

    两点确定一条线性映射；只给一点时只算偏移。
    """
    if not targets or not actuals or len(targets) != len(actuals):
        return 1.0, 0, 0

    if len(targets) == 1:
        tx, ty = targets[0]
        ax, ay = actuals[0]
        return 1.0, ax - tx, ay - ty

    (t1x, t1y), (t2x, t2y) = targets[0], targets[1]
    (a1x, a1y), (a2x, a2y) = actuals[0], actuals[1]

    # 优先用 x 方向求缩放，x 无法确定时用 y
    if abs(t2x - t1x) >= 50:
        scale = (a2x - a1x) / (t2x - t1x)
    elif abs(t2y - t1y) >= 50:
        scale = (a2y - a1y) / (t2y - t1y)
    else:
        scale = 1.0

    # 缩放异常时放弃（用户点太随意）
    if not (0.5 <= scale <= 2.5):
        scale = 1.0

    ox = int(round(a1x - t1x * scale))
    oy = int(round(a1y - t1y * scale))
    return round(scale, 4), ox, oy


class CalibrationDialog:
    """坐标校准对话框"""

    def __init__(self, master: tk.Tk, script, on_apply=None):
        self.master = master
        self.script = script
        self.on_apply = on_apply
        self.targets: list[tuple[int, int]] = []
        self.actuals: list[tuple[int, int]] = []

        self.win = tk.Toplevel(master)
        self.win.title("坐标校准")
        self.win.transient(master)
        self.win.resizable(False, False)
        self.win.geometry("480x400")

        frm = ttk.Frame(self.win, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="鼠标定位不准？用两步校准",
                  font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, foreground="#555", justify="left", wraplength=440,
                  text=("程序会先把鼠标移到两个目标位置，你在屏幕上"
                        "精确点击红色标记的中心即可。\n"
                        "已自动开启 DPI 感知，只有在特殊环境下才需要校准。")
                  ).pack(anchor="w", pady=(4, 12))

        self.state_var = tk.StringVar(value="① 尚未校准")
        ttk.Label(frm, textvariable=self.state_var,
                  foreground="#c62828").pack(anchor="w")

        self.detail_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.detail_var, justify="left",
                  foreground="#333").pack(anchor="w", pady=(6, 12))

        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="开始校准", width=12,
                   command=self.start).pack(side="left")
        ttk.Button(btns, text="恢复默认", width=10,
                   command=self.reset).pack(side="left", padx=6)

        ttk.Separator(frm).pack(fill="x", pady=14)

        ttk.Label(frm, text="手动微调（一般不用）", foreground="#555").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="缩放").grid(row=0, column=0, sticky="w")
        self.scale_var = tk.StringVar(value=f"{self.script.coord_scale}")
        ttk.Entry(row, textvariable=self.scale_var, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(row, text="X 偏移").grid(row=0, column=2, padx=(12, 0))
        self.ox_var = tk.StringVar(value=str(self.script.coord_offset_x))
        ttk.Entry(row, textvariable=self.ox_var, width=7).grid(row=0, column=3, padx=4)
        ttk.Label(row, text="Y 偏移").grid(row=0, column=4, padx=(12, 0))
        self.oy_var = tk.StringVar(value=str(self.script.coord_offset_y))
        ttk.Entry(row, textvariable=self.oy_var, width=7).grid(row=0, column=5, padx=4)

        foot = ttk.Frame(frm)
        foot.pack(fill="x", side="bottom", pady=(14, 0))
        ttk.Button(foot, text="应用", width=8,
                   command=self.apply).pack(side="right", padx=4)
        ttk.Button(foot, text="关闭", width=8,
                   command=self.win.destroy).pack(side="right")

        self._refresh()

    def _refresh(self) -> None:
        s = self.script
        if s.has_calibration:
            self.state_var.set(
                f"已校准：缩放 {s.coord_scale}，偏移 ({s.coord_offset_x}, {s.coord_offset_y})")
        else:
            self.state_var.set("未校准（默认按原坐标回放）")

    def start(self) -> None:
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        # 取两个相距较远的点，缩放计算更准确
        self.targets = [(int(sw * 0.25), int(sh * 0.25)),
                        (int(sw * 0.75), int(sh * 0.75))]
        self.actuals = []

        self.win.withdraw()
        self.win.after(260, self._show_overlay)

    def _show_overlay(self) -> None:
        import time
        # 先把鼠标移到第一个目标点，方便用户判断
        try:
            from pynput import mouse as _m
            ctl = _m.Controller()
            ctl.position = self.targets[0]
            time.sleep(0.15)
        except Exception:
            pass

        def done(pts):
            self.win.deiconify()
            self.win.lift()
            if not pts:
                return
            self.actuals = list(pts)
            scale, ox, oy = compute_calibration(self.targets, self.actuals)
            self.scale_var.set(str(scale))
            self.ox_var.set(str(ox))
            self.oy_var.set(str(oy))
            self.detail_var.set(
                "目标点：" + "  ".join(f"({x},{y})" for x, y in self.targets) + "\n"
                "实际点：" + "  ".join(f"({x},{y})" for x, y in self.actuals) + "\n"
                f"→ 建议缩放 {scale}，偏移 ({ox}, {oy})")

        run_calibration(self.master, self.targets, done)

    def reset(self) -> None:
        self.scale_var.set("1.0")
        self.ox_var.set("0")
        self.oy_var.set("0")
        self.detail_var.set("")
        self.script.coord_scale = 1.0
        self.script.coord_offset_x = 0
        self.script.coord_offset_y = 0
        self._refresh()

    def apply(self) -> None:
        try:
            scale = float(self.scale_var.get())
            ox = int(float(self.ox_var.get()))
            oy = int(float(self.oy_var.get()))
        except ValueError:
            messagebox.showerror("参数错误", "缩放/偏移必须是数字。", parent=self.win)
            return
        if not (0.2 <= scale <= 5.0):
            messagebox.showerror("参数错误", "缩放应在 0.2 ~ 5.0 之间。", parent=self.win)
            return
        self.script.coord_scale = scale
        self.script.coord_offset_x = ox
        self.script.coord_offset_y = oy
        self._refresh()
        if self.on_apply:
            self.on_apply()
        messagebox.showinfo("已应用", "坐标校准已生效，回放时会自动换算。",
                            parent=self.win)
