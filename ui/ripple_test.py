"""涟漪测试页 —— 独立窗口，直接调 RippleFeedback，不走录制流程。

用途：排查「看不到涟漪」到底是哪一环失效（窗口无法创建 / 键控失效 /
      穿透失效 / 坐标不对 / 动画没跑）。

运行：python 涟漪测试.py
"""

from __future__ import annotations

import sys
import traceback
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, ".")

from ui.ripple import COLORS, KEY_COLOR, SYSTEM, WIN_SIZE, RippleFeedback  # noqa: E402


class RippleTestPanel:
    """可嵌入任意 Toplevel/Tk 的涟漪测试面板"""

    def __init__(self, parent: tk.Misc):
        self.parent = parent
        self.ripple = RippleFeedback(parent)
        self._build()
        self._log(f"平台：{SYSTEM}")
        self._log(f"模式：{self.ripple._mode}　启用：{self.ripple.enabled}")
        self._log(f"键控色：{KEY_COLOR}　窗口：{WIN_SIZE}×{WIN_SIZE}")
        parent.bind("<Button-1>", self._on_click)

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        bar = tk.Frame(self.parent, bg="#232a3c", height=120)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="涟漪测试页", bg="#232a3c", fg="#eef2fa",
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(bar, text="点下面按钮或窗口任意位置 → 应在鼠标处看到扩散圆环",
                 bg="#232a3c", fg="#8c98af",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16)

        row = tk.Frame(bar, bg="#232a3c")
        row.pack(anchor="w", padx=12, pady=8)
        for text, cmd in (
            ("屏幕中心涟漪", self._test_center),
            ("四角涟漪", self._test_corners),
            ("连续 5 次", self._test_burst),
            ("窗口内涟漪", self._test_local),
            ("闪烁模式", self._test_flash),
            ("诊断", self._diagnose),
        ):
            tk.Button(row, text=text, command=cmd, bg="#2e3750", fg="#eef2fa",
                      activebackground="#3a4460", activeforeground="#fff",
                      relief="flat", padx=12, pady=6,
                      font=("Microsoft YaHei UI", 9)).pack(side="left", padx=4)

        # 日志
        body = tk.Frame(self.parent, bg="#1c2130")
        body.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        tk.Label(body, text="诊断输出", bg="#1c2130", fg="#8c98af",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.log = tk.Text(body, bg="#12161f", fg="#c8d2e4", relief="flat",
                           font=("Consolas", 9), wrap="word", padx=10, pady=8)
        self.log.pack(fill="both", expand=True, pady=(4, 0))
        self.log.tag_configure("ok", foreground="#42d08a")
        self.log.tag_configure("warn", foreground="#ffa33d")
        self.log.tag_configure("err", foreground="#ff5c5c")

    def _log(self, msg: str, tag: str = "") -> None:
        self.log.insert("end", msg + "\n", tag or ())
        self.log.see("end")

    def after(self, ms, func=None, *args):
        """委托给父容器（面板不是 Tk 控件自身）"""
        return self.parent.after(ms, func, *args)

    # ------------------------------------------------------------ 测试
    def _test_center(self) -> None:
        x, y = self.parent.winfo_screenwidth() // 2, self.parent.winfo_screenheight() // 2
        self._log(f"→ 屏幕中心 ({x}, {y})")
        self._fire(x, y, "left")

    def _test_corners(self) -> None:
        sw, sh = self.parent.winfo_screenwidth(), self.parent.winfo_screenheight()
        pts = [(80, 80), (sw - 80, 80), (80, sh - 80), (sw - 80, sh - 80)]
        self._log(f"→ 四角 {pts}")
        for i, (x, y) in enumerate(pts):
            self.after(i * 260, lambda xx=x, yy=y: self._fire(xx, yy, "left"))

    def _test_burst(self) -> None:
        x, y = self.parent.winfo_rootx() + 200, self.parent.winfo_rooty() + 300
        self._log(f"→ 连续 5 次 @ ({x}, {y})")
        for i in range(5):
            self.after(i * 130, lambda xx=x, yy=y, i=i: self._fire(
                xx + i * 6, yy + i * 4,
                ["left", "right", "middle"][i % 3]))

    def _test_local(self) -> None:
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2
        y = self.parent.winfo_rooty() + self.parent.winfo_height() - 120
        self._log(f"→ 窗口内 ({x}, {y})")
        self._fire(x, y, "left")

    def _fire(self, x: int, y: int, button: str) -> None:
        try:
            self.ripple.show(x, y, button)
            self._log(f"  show({x}, {y}, {button}) → 已调用", "ok")
        except Exception:
            self._log("  show() 抛异常：\n" + traceback.format_exc(), "err")

    def _on_click(self, e) -> None:
        self._log(f"点击 ({e.x_root}, {e.y_root})")
        self._fire(e.x_root, e.y_root, "left")

    def _test_flash(self) -> None:
        """临时切到闪烁兼容模式打一发（不影响原模式）"""
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2
        y = self.parent.winfo_rooty() + 300
        old = self.ripple.mode_override
        self.ripple.mode_override = "flash"
        self._log(f"→ 闪烁兼容模式 @ ({x}, {y})（不依赖键控色，必显示）")
        self._fire(x, y, "left")
        self.ripple.mode_override = old

    # ------------------------------------------------------------ 诊断
    def _diagnose(self) -> None:
        self._log("\n===== 诊断 =====")
        rf = self.ripple
        self._log(f"enabled={rf.enabled}  mode={rf.mode}"
                  f"（override={rf.mode_override}）")

        if rf._mode == "off":
            self._log("⚠ 当前平台无涟漪实现（macOS 或键控不可用）", "warn")
            return

        # 1) 窗口能否创建
        try:
            rf._ensure_win()
        except Exception:
            self._log("✗ 创建窗口失败：\n" + traceback.format_exc(), "err")
            return
        w = rf._win
        if w is None:
            self._log("✗ 窗口对象为 None", "err")
            return
        self._log(f"✓ 窗口已创建  id={w.winfo_id()}", "ok")

        # 2) 键控属性
        if rf._mode == "key":
            try:
                tc = w.attributes("-transparentcolor")
                self._log(f"✓ -transparentcolor = {tc!r}", "ok"
                          if str(tc).lower() == KEY_COLOR.lower() else "warn")
            except tk.TclError as exc:
                self._log(f"✗ -transparentcolor 不可用：{exc}", "err")

        # 3) 穿透样式
        if SYSTEM == "Windows":
            try:
                import ctypes
                GWL_EXSTYLE = -20
                hwnd = ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
                st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                flags = {
                    "LAYERED": 0x00080000, "TRANSPARENT": 0x00000020,
                    "NOACTIVATE": 0x08000000, "TOOLWINDOW": 0x00000080,
                    "TOPMOST": 0x00000008,
                }
                got = [k for k, v in flags.items() if st & v]
                self._log(f"扩展样式：{hex(st)} → {got}", "ok" if "TRANSPARENT" in got else "warn")
                if "LAYERED" not in got:
                    self._log("⚠ 缺 LAYERED：键控透明不会生效（这是看不到的主要原因）", "warn")
            except Exception as exc:
                self._log(f"读扩展样式失败：{exc}", "err")

        # 4) 位置与可见性
        self._log(f"窗口几何：{w.winfo_geometry()}　可见：{w.winfo_viewable()}")
        # 5) 画布内容
        if rf._canvas is not None:
            self._log(f"画布项目数：{len(rf._canvas.find_all())}")

        # 6) 实际触发一次并复查
        self._log("触发一次涟漪并复查…")
        x, y = self.parent.winfo_rootx() + 300, self.parent.winfo_rooty() + 260
        self._fire(x, y, "left")

        def after_check():
            if rf._canvas is None or not w.winfo_exists():
                return
            n = len(rf._canvas.find_all())
            self._log(f"  160ms 后画布项目数：{n}", "ok" if n > 0 else "err")
            self._log(f"  窗口几何：{w.winfo_geometry()}")
            if n == 0:
                self._log("✗ 画布是空的 → 动画帧没画上去", "err")
                return
            self._log("✓ 画布已绘制", "ok")
            # 7) 键控生效性自检（实心块抓屏 —— 唯一可靠的判定）
            if SYSTEM == "Windows":
                ok = rf.self_test()
                self._log(f"键控色自检：{'通过' if ok else '失败'}", "ok" if ok else "err")
                if not ok:
                    self._log("  ✗ 键控色在你机器上不生效 → 圆环会被当成背景色一起抠掉", "err")
                    self._log("    解决：设置面板 → 涟漪模式 → 改选「闪烁（兼容模式）」", "warn")
                else:
                    self._log("  ✓ 键控正常，涟漪应可见；若仍看不到请加大屏幕缩放后再试", "ok")
            if rf._mode == "key":
                self._log("  排查建议：右键桌面 → 显示设置 → 关闭「透明效果」试试；"
                          "或显卡驱动开启「桌面合成」", "warn")

        self.after(160, after_check)


def open_ripple_test(master: tk.Misc) -> None:
    """在主程序里打开涟漪测试窗口（打包后也能用）"""
    win = tk.Toplevel(master)
    win.title("涟漪测试页 · 按键精灵")
    win.geometry("720x560+140+90")
    win.configure(bg="#1c2130")
    try:
        win.attributes("-topmost", False)
    except tk.TclError:
        pass
    RippleTestPanel(win)


def main() -> None:
    root = tk.Tk()
    root.title("涟漪测试页 · 按键精灵")
    root.geometry("720x560+120+80")
    root.configure(bg="#1c2130")
    RippleTestPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
