"""region_grab.py —— 屏幕框选截图

为什么不用「选文件」对话框
─────────────────────────────────────────
找图的目标本来就在屏幕上。让用户先自己截图、存成 png、再从文件对话框里
翻出来选，是全流程里最容易出错的一步（截错范围、存错格式、存到哪忘了）。
这里改成一步到位：

    全屏覆盖层 → 拖拽画框 → 直接抓这一块存成 png → 回调给调用方

实现要点
─────────────────────────────────────────
1. 覆盖层和 picker.py 一样：overrideredirect + topmost + 半透明 + 十字光标
2. 框选矩形用屏幕坐标（程序已声明 DPI 感知，tk 坐标 == 物理像素）
3. **必须先隐藏覆盖层再截屏**，否则截出来会蒙上一层黑色半透明
4. 截图落盘到 ~/.macro-sprite/images/，文件名带时间戳，不覆盖旧图
5. Esc / 右键 取消；框太小（< MIN_SIZE）当作误点，让你重画，不退出

用法
─────────────────────────────────────────
    grab_region(root, on_done)      # on_done(rect, path, err) 异步回调
    path = save_screen_region((x, y, w, h))     # 只要截图，不要界面
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from typing import Callable

from core.library import app_dir

MIN_SIZE = 8            # 小于这个边长算误点，不算框选
DIM_ALPHA = 0.20        # 覆盖层透明度：太黑看不清目标，太白看不到框
BAND = "#00e676"        # 选框颜色
LABEL_FG = "#9cff9c"
WARN_FG = "#ff8a80"


# ================================================================ 截图存盘
def images_dir() -> Path:
    """模板图目录（找图的 png 都放这里，和图省得用户自己找）"""
    d = app_dir() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique_path(width: int, height: int) -> Path:
    """不覆盖已有文件：find_20260101_120000_120x40.png / _2 / _3 …"""
    d = images_dir()
    base = f"find_{time.strftime('%Y%m%d_%H%M%S')}_{width}x{height}"
    p = d / f"{base}.png"
    n = 2
    while p.exists():
        p = d / f"{base}_{n}.png"
        n += 1
    return p


def save_screen_region(rect: tuple[int, int, int, int]) -> Path:
    """把屏幕矩形 (x, y, 宽, 高) 抓下来存成 png，返回路径"""
    x, y, w, h = (int(v) for v in rect)
    if w < MIN_SIZE or h < MIN_SIZE:
        raise ValueError(f"框选区域太小（{w}×{h}），至少 {MIN_SIZE}×{MIN_SIZE}")
    try:
        from PIL import ImageGrab
    except Exception as exc:                       # noqa: BLE001
        raise RuntimeError("缺少 Pillow，无法截图。请先：pip install pillow") from exc

    box = (x, y, x + w, y + h)
    try:
        im = ImageGrab.grab(bbox=box, all_screens=True)
    except TypeError:               # 老版本 Pillow 不认 all_screens
        im = ImageGrab.grab(bbox=box)
    if im is None or im.width < 1 or im.height < 1:
        raise RuntimeError("截屏返回了空图（远程桌面/虚拟机下偶尔会这样）")
    path = _unique_path(im.width, im.height)
    im.save(path, format="PNG")
    return path


# ================================================================ 框选覆盖层
class _RegionGrabOverlay(tk.Toplevel):
    """全屏覆盖层：拖一个矩形出来，松手就截这块"""

    def __init__(self, master: tk.Misc,
                 on_done: Callable[[tuple[int, int, int, int] | None, str, str],
                                   None] | None = None):
        super().__init__(master)
        self.on_done = on_done
        self._finished = False
        self._dragging = False
        self._x0 = self._y0 = 0
        self._band: int | None = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", DIM_ALPHA)
        except tk.TclError:
            pass
        self.configure(bg="#000000")

        # 只覆盖主屏（与 picker.py 保持一致）；多屏用户把目标拖到主屏上框
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.create_text(
            sw // 2, 40, text="拖拽框选要找的目标",
            fill="#ffffff", font=("Microsoft YaHei", 18, "bold"), tags="hint")
        self.canvas.create_text(
            sw // 2, 74,
            text="松手即自动添加「找图点击」 · Esc / 右键 取消",
            fill="#cccccc", font=("Microsoft YaHei", 12), tags="hint")

        self._label = self.canvas.create_text(
            0, 0, text="", anchor="nw", fill=LABEL_FG,
            font=("Consolas", 13, "bold"))

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda e: self._cancel())
        self.bind("<Escape>", lambda e: self._cancel())

        self.focus_force()
        self.grab_set()                 # 独占输入，避免误操作到底下的窗口

    # ---------------------------------------------------------- 事件
    def _on_press(self, event: tk.Event) -> None:
        if self._finished:
            return
        self._dragging = True
        self._x0, self._y0 = event.x, event.y
        if self._band is not None:
            self.canvas.delete(self._band)
        self._band = self.canvas.create_rectangle(
            self._x0, self._y0, self._x0, self._y0,
            outline=BAND, width=2, dash=(4, 2))
        self.canvas.itemconfigure(self._label, text="0 × 0", fill=LABEL_FG)
        self.canvas.coords(self._label, self._x0 + 12, self._y0 + 10)

    def _on_drag(self, event: tk.Event) -> None:
        if not self._dragging or self._band is None:
            return
        x1, y1 = event.x, event.y
        self.canvas.coords(self._band, self._x0, self._y0, x1, y1)
        w, h = abs(x1 - self._x0), abs(y1 - self._y0)
        self.canvas.itemconfigure(self._label, text=f"{w} × {h}", fill=LABEL_FG)
        # 标签跟着左上角走，拖到右下边缘时翻到框内，避免出屏
        lx = min(self._x0, x1) + 12
        ly = min(self._y0, y1) + 10
        if ly > self.winfo_screenheight() - 40:
            ly = max(self._y0, y1) - 30
        self.canvas.coords(self._label, lx, ly)

    def _on_release(self, event: tk.Event) -> None:
        if self._finished or not self._dragging:
            return
        self._dragging = False
        x1, y1 = event.x, event.y
        x, y = min(self._x0, x1), min(self._y0, y1)
        w, h = abs(x1 - self._x0), abs(y1 - self._y0)
        if w < MIN_SIZE or h < MIN_SIZE:
            # 误点：不退出，提示后让你重画（退出反而更烦）
            self.canvas.itemconfigure(
                self._label, text=f"框太小（{w}×{h}），请重新拖拽", fill=WARN_FG)
            if self._band is not None:
                self.canvas.delete(self._band)
                self._band = None
            return
        self._finish((x, y, w, h))

    # ---------------------------------------------------------- 结束
    def _finish(self, rect: tuple[int, int, int, int]) -> None:
        self._finished = True
        # 画布坐标 → 屏幕坐标（覆盖层贴在 0,0，但按 root 偏移算更稳）
        rx, ry = self.winfo_rootx(), self.winfo_rooty()
        screen_rect = (rx + rect[0], ry + rect[1], rect[2], rect[3])
        # 必须先把自己藏起来：半透明黑纱会被一起截进去，模板图就废了
        self.withdraw()
        self.update_idletasks()
        self.after(160, lambda: self._capture(screen_rect))

    def _capture(self, rect: tuple[int, int, int, int]) -> None:
        path = ""
        err = ""
        try:
            path = str(save_screen_region(rect))
        except Exception as exc:                   # noqa: BLE001
            err = str(exc)
        self._close()
        if self.on_done:
            self.on_done(rect if path else None, path, err)

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._close()
        if self.on_done:
            self.on_done(None, "", "")

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
def grab_region(master: tk.Misc,
                on_done: Callable[[tuple[int, int, int, int] | None, str, str],
                                  None] | None = None) -> _RegionGrabOverlay:
    """打开框选覆盖层（异步）

    on_done(rect, path, err)：
      rect = (x, y, 宽, 高) 屏幕物理像素；None 表示取消/失败
      path = 存好的 png 绝对路径；失败或取消时为空串
      err  = 失败原因；取消时为空串
    """
    return _RegionGrabOverlay(master, on_done=on_done)
