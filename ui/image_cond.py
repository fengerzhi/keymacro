"""image_cond.py —— 「如果 屏幕上有/没有这张图」的选图对话框

为什么要有它
─────────────────────────────────────────
条件判断要的图，绝大多数情况下就是**刚刚框选的那张**（找图点击用的模板）。
以前这里又弹一次文件对话框，让用户从 `~/.macro-sprite/images/` 里翻
`find_20261104_133913_120x58.png` —— 文件名认不出来，还容易选错。

所以改成一个列表：脚本里已经用过的图（最近的排最前）+ 模板目录里最新的
那几个，选中就能看到预览；要新图就选最后一项「重新框选屏幕上的目标…」。
一次点击同时定下「哪张图」和「有 / 没有」。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

# 列表里「现场框选」那一项的标记值（不是真实路径）
GRAB_ITEM = "__grab__"
GRAB_LABEL = "✂ 重新框选屏幕上的目标…"


def _item_label(path: str) -> str:
    """列表显示：文件名 + 尺寸（尺寸能帮用户认出是哪张）"""
    name = Path(path).name
    size = "?"
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = f"{im.width}×{im.height}"
    except Exception:                       # noqa: BLE001
        return f"{name}   （文件不存在）"
    return f"{name}   {size}"


class _ImageCondDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, items: list[str],
                 current: str = "", hit: bool = True):
        super().__init__(master)
        self.title("图片条件")
        # 允许拉大：全屏级的模板图，420 宽也就能看个大概，用户可自己拉宽
        self.resizable(True, True)
        self.minsize(760, 430)
        self.transient(master)

        self.result: tuple[str, bool] | None = None
        self._paths: list[str] = []
        self._photo = None
        self.var_hit = tk.BooleanVar(value=hit)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)   # 拉窗口时预览要和列表一起变宽

        ttk.Label(body, text="要判断的图片：").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="预览：").grid(row=0, column=1, sticky="w", padx=(12, 0))

        box = ttk.Frame(body)
        box.grid(row=1, column=0, sticky="nsew")
        self.list = tk.Listbox(box, height=15, width=46, activestyle="dotbox",
                               exportselection=False)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=sb.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 预览用 Canvas 而不是 Label：Tk 的 Label 一旦显示 image，
        # width/height 的单位就从「字符」变成「像素」，写 30 会缩成一条缝。
        # Canvas 的长宽永远是像素，算缩放也方便。
        self._pv_w, self._pv_h = 420, 300
        self._pv_job: str | None = None
        self.preview = tk.Canvas(body, width=self._pv_w, height=self._pv_h,
                                 bg="#fafafa", highlightthickness=1,
                                 highlightbackground="#c8c8c8")
        self.preview.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        self.preview.bind("<Configure>", self._on_preview_resize)

        opt = ttk.Frame(body)
        opt.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Radiobutton(opt, text="屏幕上 有 这张图",
                        value=True, variable=self.var_hit).pack(side="left")
        ttk.Radiobutton(opt, text="屏幕上 没有 这张图",
                        value=False, variable=self.var_hit).pack(side="left",
                                                                 padx=(20, 0))

        btns = ttk.Frame(body)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right",
                                                            padx=(0, 8))

        self._fill(items, current)

        self.list.bind("<<ListboxSelect>>", self._show_preview)
        self.list.bind("<Double-Button-1>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.grab_set()
        self.list.focus_set()

    # ---------------------------------------------------------- 内容
    def _fill(self, items: list[str], current: str) -> None:
        has_current = False
        for p in items:
            if not p:
                continue
            self._paths.append(p)
            self.list.insert("end", _item_label(p))
            if current and p == current:
                has_current = True
        if current and not has_current:
            # 老脚本里引用的图可能既不在列表也不在目录里，照样给个位置
            self._paths.append(current)
            self.list.insert("end", _item_label(current))
            has_current = True
        # 最后一项永远是「现场框选」
        self._paths.append(GRAB_ITEM)
        self.list.insert("end", GRAB_LABEL)

        idx = self._paths.index(current) if has_current else 0
        self.list.selection_clear(0, "end")
        self.list.selection_set(idx)
        self.list.see(idx)
        self._show_preview()

    def _selected(self) -> str | None:
        sel = self.list.curselection()
        if not sel:
            return None
        i = int(sel[0])
        if 0 <= i < len(self._paths):
            return self._paths[i]
        return None

    def _on_preview_resize(self, event: tk.Event) -> None:
        """窗口被拉大/缩小 → 延时重画（拖动过程中别每像素解一次图）"""
        if abs(event.width - self._pv_w) < 3 and abs(event.height - self._pv_h) < 3:
            return
        self._pv_w, self._pv_h = event.width, event.height
        if self._pv_job:
            self.after_cancel(self._pv_job)
        self._pv_job = self.after(120, self._show_preview)

    def _show_preview(self, _evt=None) -> None:
        """在画布上按「能看清」的方式画：大图缩小、小图放大"""
        c = self.preview
        c.delete("all")
        self._photo = None
        sel = self._selected()

        def hint(text: str, color: str = "#888") -> None:
            c.create_text(self._pv_w // 2, self._pv_h // 2, text=text,
                          fill=color, font=("Microsoft YaHei", 10),
                          width=self._pv_w - 40, justify="center")

        if not sel or sel == GRAB_ITEM:
            hint("去屏幕上框一下要判断的目标")
            return
        try:
            from PIL import Image, ImageTk

            with Image.open(sel) as im0:
                im = im0.convert("RGB")
        except Exception:                   # noqa: BLE001
            hint("（这张图读不出来，可能已被删除）", "#c62828")
            return

        w, h = im.size
        aw, ah = self._pv_w - 16, self._pv_h - 34       # 底部留一行放尺寸说明
        fit = min(aw / w, ah / h)
        if fit < 1:                          # 大图：等比缩小到框内
            im = im.resize((max(1, int(w * fit)), max(1, int(h * fit))),
                           Image.LANCZOS)
            note = f"{w}×{h}　缩小到 {int(fit * 100)}%"
        elif fit >= 2:                       # 小图：整数倍放大，像素块清清楚楚
            f = int(min(fit, 8))
            im = im.resize((w * f, h * f), Image.NEAREST)
            note = f"{w}×{h}　放大 {f}×"
        else:
            note = f"{w}×{h}　原始大小"

        self._photo = ImageTk.PhotoImage(im)
        c.create_image(self._pv_w // 2, (self._pv_h - 22) // 2,
                       image=self._photo, anchor="center")
        c.create_text(self._pv_w // 2, self._pv_h - 11, text=note,
                      fill="#666", font=("Consolas", 9))

    # ---------------------------------------------------------- 结束
    def _ok(self) -> None:
        sel = self._selected()
        if not sel:
            return                          # 什么都没选，别关
        self.result = (sel, bool(self.var_hit.get()))
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

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
def ask_image_condition(master: tk.Misc, items: list[str],
                        current: str = "", hit: bool = True
                        ) -> tuple[str, bool] | None:
    """弹出选图对话框（同步阻塞）

    返回 (path, is_hit)：
        path 为 GRAB_ITEM 表示用户选了「重新框选屏幕上的目标」；
        is_hit True = 屏幕上有这张图，False = 屏幕上没有。
    用户取消返回 None。
    """
    dlg = _ImageCondDialog(master, items, current=current, hit=hit)
    master.wait_window(dlg)
    return dlg.result
