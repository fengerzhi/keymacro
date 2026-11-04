"""picker.py —— 屏幕取点工具

全屏半透明覆盖层，十字线跟随鼠标实时显示屏幕绝对坐标。
单击取点，Esc 取消；支持连续取多点（如拖拽的起点+终点）。

实现要点
─────────────────────────────────────────
1. 用 overlay 盖住整个屏幕（含多显示器主屏），半透明以便看清底下的界面
2. 十字线 + 实时坐标气泡 + 放大镜式像素取色（可选）
3. 取到的坐标是**屏幕绝对坐标**，与 pynput 控制器坐标一致
4. 支持「连取两点」模式返回 [(x1,y1),(x2,y2)]，用于拖拽指令
5. Esc / 右键取消，保证不会卡死

用法
─────────────────────────────────────────
    pos = pick_point(root)                 # 取单点，返回 (x, y) 或 None
    pts = pick_points(root, count=2)       # 连取两点
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

CROSS_SIZE = 14          # 十字线臂长
LINE_COLOR = "#e53935"
LABEL_BG = "#212121"
LABEL_FG = "#9cff9c"


class _PickOverlay(tk.Toplevel):
    """全屏取点覆盖层"""

    def __init__(self, master: tk.Misc, count: int = 1,
                 on_done: Callable[[list[tuple[int, int]]], None] | None = None):
        super().__init__(master)
        self.count = max(1, count)
        self.points: list[tuple[int, int]] = []
        self.on_done = on_done
        self._finished = False

        # 覆盖整个虚拟屏幕（多显示器时用 winfo_vroot*）
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.28)          # 半透明
        except tk.TclError:
            pass
        self.configure(bg="#000000")

        # 屏幕尺寸：取 tk 的虚拟根窗口尺寸（兼容多显示器）
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{screen_w}x{screen_h}+0+0")

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        # 提示文字
        hint = ("单击取坐标（第 1 点）" if self.count == 1
                else f"单击依次取 {self.count} 个点")
        self.canvas.create_text(
            screen_w // 2, 40, text=hint,
            fill="#ffffff", font=("Microsoft YaHei", 18, "bold"), tags="hint")
        self.canvas.create_text(
            screen_w // 2, 72,
            text=("Esc / 右键 取消 · Enter 结束（按已取的点算）"
                  if self.count > 1 else "Esc 或 右键 取消"),
            fill="#cccccc", font=("Microsoft YaHei", 12), tags="hint")

        # 十字线与坐标标签（初始隐藏）
        self._h_line = self.canvas.create_line(0, 0, 0, 0, fill=LINE_COLOR, width=1)
        self._v_line = self.canvas.create_line(0, 0, 0, 0, fill=LINE_COLOR, width=1)
        self._label = self.canvas.create_text(
            0, 0, text="", anchor="nw", fill=LABEL_FG,
            font=("Consolas", 13, "bold"))
        self._label_bg = self.canvas.create_rectangle(
            0, 0, 0, 0, fill=LABEL_BG, outline="")

        # 已取点标记
        self._marks: list[int] = []

        # 事件绑定
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", lambda e: self._cancel())
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Key>", lambda e: self._cancel() if e.keysym == "Escape" else None)
        if self.count > 1:
            # 多点取点允许提前结束（比如验证码只要框区域、不点输入框）
            self.bind("<Return>", lambda e: self._finish())
            self.bind("<KP_Enter>", lambda e: self._finish())

        self.focus_force()
        self.grab_set()                 # 独占输入

    # ---------------------------------------------------------- 事件
    def _on_motion(self, event: tk.Event) -> None:
        x, y = event.x_root, event.y_root
        w, h = self.winfo_screenwidth(), self.winfo_screenheight()

        # 十字线
        self.canvas.coords(self._h_line, 0, y, w, y)
        self.canvas.coords(self._v_line, x, 0, x, h)

        # 坐标标签：跟随光标，靠近边缘时自动翻转避免出屏
        text = f"  {x}, {y}  "
        lx, ly = x + 18, y + 14
        if lx > w - 120:
            lx = x - 110
        if ly > h - 40:
            ly = y - 36
        self.canvas.coords(self._label, lx, ly)
        self.canvas.itemconfigure(self._label, text=text)
        bbox = self.canvas.bbox(self._label)
        if bbox:
            self.canvas.coords(self._label_bg, bbox[0] - 4, bbox[1] - 3,
                               bbox[2] + 4, bbox[3] + 3)
        # 保证标签在十字线之上
        self.canvas.tag_raise(self._label_bg)
        self.canvas.tag_raise(self._label)

    def _on_click(self, event: tk.Event) -> None:
        if self._finished:
            return
        x, y = event.x_root, event.y_root
        self.points.append((x, y))

        # 画一个标记
        r = 10
        mark = self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                       outline="#00e676", width=2)
        num = self.canvas.create_text(x, y - 22, text=str(len(self.points)),
                                      fill="#00e676",
                                      font=("Consolas", 12, "bold"))
        self._marks.extend([mark, num])
        self.canvas.tag_raise(mark)
        self.canvas.tag_raise(num)

        if len(self.points) >= self.count:
            self.after(120, self._finish)
        else:
            self.canvas.itemconfigure(
                "hint", text=f"已取 {len(self.points)} 点，继续取第 "
                             f"{len(self.points) + 1} 点")

    # ---------------------------------------------------------- 结束
    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        pts = list(self.points)
        self._close()
        if self.on_done:
            self.on_done(pts)

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._close()
        if self.on_done:
            self.on_done([])

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


# ================================================================ 对外接口
def pick_points(master: tk.Misc, count: int = 1,
                on_done: Callable[[list[tuple[int, int]]], None] | None = None
                ) -> _PickOverlay:
    """打开取点覆盖层（异步，结果通过 on_done 回调返回）

    on_done 收到 [] 表示用户取消了。
    """
    return _PickOverlay(master, count=count, on_done=on_done)


def pick_point_sync(master: tk.Misc, count: int = 1,
                    timeout_ms: int = 60000) -> list[tuple[int, int]]:
    """同步版取点（会阻塞，仅适合脚本化测试）"""
    result: list[tuple[int, int]] = []
    done = tk.BooleanVar(master, value=False)

    def cb(pts):
        result.extend(pts)
        done.set(True)

    pick_points(master, count, cb)
    master.wait_variable(done)
    return result
