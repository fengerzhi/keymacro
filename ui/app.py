"""app.py —— 桌面版按键精灵主窗口（tkinter）

布局
┌─────────────────────────────────────────────────────────────┐
│ 工具栏  ●录制 ■停止 ▶回放 ⏹中断 │ 💾保存 📂打开 ✖清空        │
├───────────────────────────┬─────────────────────────────────┤
│ 事件时间轴（可双击编辑）     │ 指令库                           │
│  时间 | 动作                │  鼠标：左键/右键/双击/拖拽        │
│                           │  编辑：全选/复制/剪切/粘贴/删除…   │
│                           │  键盘：输入文本/组合键/回车        │
│                           │  控制：等待/循环/找图/注释         │
│                           ├─────────────────────────────────┤
│                           │ 脚本设置：名称/速度/循环/抖动/重试 │
│                           │ 统计信息 + 平台提示               │
├───────────────────────────┴─────────────────────────────────┤
│ 状态栏                                                       │
└─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import crash, script_package
from core.hotkey import HOTKEY_LABELS, HotkeyManager
from core.library import (ScriptLibrary, app_dir, load_config, save_config)
from core.logger import RunLogger, list_logs, read_log
from core.player import Player
from core.recorder import Recorder
from core.script_model import EDIT_ACTIONS, Event, Script
from core.window import SYSTEM, capability_report, focus_window, get_active_window
from ui.image_cond import GRAB_ITEM, ask_image_condition
from ui.picker import pick_points
from ui.region_grab import grab_region, images_dir

APP_TITLE = "按键精灵 · 桌面版"
HOTKEY_HINT = "F9 停止录制 · F8 暂停 · F10 中断回放"

# 录制 / 回放时是否把主窗口完全收起（只留迷你控制条）。
# True：withdraw 主窗口，屏幕上只剩迷你条，最不挡视野（推荐）
# False：iconify 到任务栏，任务栏仍能看到程序
HUD_ONLY_RECORD = True

# 指令库定义：(分组, [(显示名, 工厂函数或指令标识, 说明)])
def _mk(kind: str, **kw):
    return lambda: (kind, kw)


def _rect_of(points: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    """取点前两个（左上、右下）→ 屏幕矩形 (x, y, 宽, 高)"""
    xs = [p[0] for p in points[:2]]
    ys = [p[1] for p in points[:2]]
    x, y = min(xs), min(ys)
    return x, y, max(1, max(xs) - x), max(1, max(ys) - y)


class _EditKeyDialog(tk.Toplevel):
    """编辑 edit 类型事件的动作选择框（下拉选择语义指令）"""

    def __init__(self, master, choices: list[str], current: str):
        super().__init__(master)
        self.title("编辑语义指令")
        self.result: str | None = None
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="选择语义指令：").pack(anchor="w")
        var = tk.StringVar(value=current if current in choices else choices[0])
        cb = ttk.Combobox(frm, textvariable=var, values=choices,
                          state="readonly", width=24)
        cb.pack(pady=(4, 10))
        cb.current(choices.index(current) if current in choices else 0)

        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        self._var = var
        cb.bind("<Double-Button-1>", lambda e: self._ok())
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def _ok(self) -> None:
        self.result = self._var.get()
        self.destroy()


class _RecordModeDialog(tk.Toplevel):
    """开始录制时询问：清空重录 / 追加到末尾 / 取消

    老版本是直接清空且不提示——录完一段想补录，之前的全没了。
    """

    def __init__(self, master, n_events: int):
        super().__init__(master)
        self.title("开始录制")
        self.result: str | None = None          # replace / append / None
        self.remember = False
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text=f"当前已有 {n_events} 条事件，这次录制要怎么处理？",
            font=("", 10, "bold")).pack(anchor="w", pady=(0, 10))

        ttk.Button(frm, text="清空重录（丢掉现有事件）", width=34,
                   command=lambda: self._pick("replace")).pack(fill="x", pady=2)
        ttk.Button(frm, text="追加到末尾（保留现有，接在后面录）", width=34,
                   command=lambda: self._pick("append")).pack(fill="x", pady=2)
        ttk.Button(frm, text="取消", width=34,
                   command=self.destroy).pack(fill="x", pady=(10, 0))

        self.remember_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="记住我的选择，以后不再询问",
                        variable=self.remember_var).pack(anchor="w", pady=(10, 0))
        ttk.Label(frm, text="（想改回来：删掉 ~/.macro-sprite/config.json 里的 record_mode）",
                  foreground="#888").pack(anchor="w")

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        # 居中到主窗口
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:                      # noqa: BLE001
            pass

    def _pick(self, mode: str) -> None:
        self.result = mode
        self.remember = bool(self.remember_var.get())
        self.destroy()


class MacroApp(tk.Tk):
    # 开始录制时对已有脚本的处理方式（工具栏下拉用）
    _REC_MODE_LABELS = {"ask": "询问我", "replace": "总是清空重录",
                        "append": "总是追加"}

    def __init__(self):
        super().__init__()
        # 标题栏带版本号，方便确认当前装的是哪一版
        try:
            from core.updater import APP_VERSION as _ver
            self.title(f"{APP_TITLE}   v{_ver}")
        except Exception:
            self.title(APP_TITLE)
        self.geometry("1320x760")
        self.minsize(1120, 640)

        self.script = Script()
        self.recorder = Recorder(
            on_stop=lambda: self.after(0, self.stop_recording),
            on_event=lambda ev: self.after(0, self._on_record_event, ev),
            # 按下瞬间触发涟漪：不能等 mousedown 落盘（那要等松手）
            on_press=lambda x, y, b: self.after(
                0, self._ripple_at, x, y, b) if x is not None else None,
        )
        self.player = Player()
        # 回放时也画涟漪：player 在回放线程里回调，切回主线程再画
        self.player.on_action = lambda x, y, b: self.after(
            0, self._ripple_play, x, y, b)
        self.script_path: str | None = None
        self.target_window: str | None = None
        self._last_row_count = 0

        # ---- 崩溃兜底：tkinter 回调里的异常默认只打到 stderr，
        #      pythonw 启动时等于彻底静默（用户只看到窗口消失）
        self.report_callback_exception = self._on_tk_error

        # ---- 撤销 / 重做（快照式：每次修改前存一份脚本 JSON）
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._undo_limit = 60
        self._dirty = False                 # 有未保存改动
        self._restoring = False             # 正在撤销/重做，避免重复快照

        # ---- 列表视图状态
        self._filter_text = ""              # 搜索关键字（空 = 不过滤）
        self._folded: set[int] = set()      # 被折叠的循环起始行索引

        # ---- 回放倒计时
        self._cd_job = None
        self._cd_left = 0

        # ---- 录制模式与追加偏移
        self._record_mode = "replace"
        self._record_offset = 0

        try:
            self.cfg = load_config()
        except Exception:                   # noqa: BLE001
            self.cfg = {"countdown": 3, "record_mode": "ask", "last_dir": ""}

        # 工具栏 / 列表条上的两个全局设置（控件在 _build_ui 里创建）
        self.countdown_var = tk.StringVar(value=str(self.cfg.get("countdown", 3)))
        self.record_mode_var = tk.StringVar(
            value=self._REC_MODE_LABELS.get(
                str(self.cfg.get("record_mode", "ask")), "询问我"))

        # 点击涟漪特效（录制时的视觉反馈）
        from ui.ripple import RippleFeedback
        self.ripple = RippleFeedback(self)
        # 首次启动自检键控色是否生效：不生效则静默降级为闪烁模式
        self.after(600, self._autocheck_ripple)

        # 「在此处插入」：右键菜单指定的插入位置（用完即清）
        self._insert_index: int | None = None

        # 录制 / 回放迷你控制条（方案 A：顶部居中细条）
        self.hud = None                 # 延迟创建，避免启动时闪一下
        self._hud_tick_job = None
        self._hud_play_info = None      # (lap, total, step, steps)

        self._set_app_icon()

        # 脚本库与日志
        self.library = ScriptLibrary()
        self.current_filename: str | None = None    # 当前关联的库条目
        self._run_logger: RunLogger | None = None

        # 全局热键：F9 停止录制 / F10 中断回放 / F11 一键启停
        self.hotkeys = HotkeyManager(scheduler=self.after)
        self._register_hotkeys()

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_list()
        self._update_undo_ui()
        self._refresh_title()
        self._set_status(capability_report())
        self._update_hotkey_hint()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 上一轮升级有没有生效（升级时程序会退出，只能下次启动再核对）
        try:
            self.after(900, self._check_pending_update)
        except Exception:
            pass
        # 启动 4 秒后静默检查一次更新（已配置升级来源时才有动作）
        try:
            self.after(4000, self._auto_check_update)
        except Exception:
            pass

    # ============================================================ 崩溃兜底
    def _on_tk_error(self, exc_type, exc, tb) -> None:
        """tkinter 回调里抛出的异常：写日志 + 弹窗（默认实现只打 stderr）"""
        try:
            crash.handle(exc_type, exc, tb, source="tk-callback")
        except Exception:                      # noqa: BLE001
            pass

    # ============================================================ 撤销 / 重做
    def _push_undo(self, before: str) -> None:
        """把「修改前」的脚本快照压入撤销栈"""
        if self._restoring or self.recorder.running:
            return
        if self._undo_stack and self._undo_stack[-1] == before:
            return                             # 内容没变，不占栈
        self._undo_stack.append(before)
        if len(self._undo_stack) > self._undo_limit:
            del self._undo_stack[0]
        self._redo_stack.clear()
        self._mark_dirty()
        self._update_undo_ui()

    def _snapshot(self) -> None:
        """修改脚本前存一份快照（最多 60 步）。录制中不压栈。"""
        if self._restoring or self.recorder.running:
            return
        try:
            snap = json.dumps(self.script.to_dict(), ensure_ascii=False)
        except Exception:                      # noqa: BLE001
            return
        self._push_undo(snap)

    def _with_undo(self, fn, *args, **kwargs):
        """执行可能修改脚本的操作；真的改了才记一步撤销（应对「打开又取消」）"""
        try:
            before = json.dumps(self.script.to_dict(), ensure_ascii=False)
        except Exception:                      # noqa: BLE001
            before = None
        result = fn(*args, **kwargs)
        if before is not None:
            try:
                after = json.dumps(self.script.to_dict(), ensure_ascii=False)
                if after != before:
                    self._push_undo(before)
            except Exception:                  # noqa: BLE001
                pass
        return result

    def _restore_snapshot(self, data: str) -> None:
        """把快照还原成当前脚本"""
        self._restoring = True
        try:
            s = Script.from_dict(json.loads(data))
            s.created_at = self.script.created_at      # 保留原始创建时间
            self.script = s
            self._load_script_vars(s)
            self._folded.clear()                       # 结构可能已变
            self._refresh_list()
        finally:
            self._restoring = False
        self._update_undo_ui()

    def _load_script_vars(self, s: Script) -> None:
        """脚本 → 界面控件（_sync_settings 的逆操作）"""
        try:
            self.name_var.set(s.name)
            self.speed_var.set(str(s.speed))
            self.loop_var.set(str(s.loop))
            self.jitter_var.set(str(s.jitter_ms))
            self.retry_var.set(str(s.retry))
            self.data_var.set(s.data_file)
            self.data_mode_var.set(s.data_mode)
            self.data_start_var.set(str(s.data_start_row))
        except Exception:                      # noqa: BLE001
            pass

    def undo(self) -> None:
        if not self._undo_stack:
            self._set_status("没有可撤销的操作")
            return
        try:
            self._redo_stack.append(
                json.dumps(self.script.to_dict(), ensure_ascii=False))
        except Exception:                      # noqa: BLE001
            return
        self._restore_snapshot(self._undo_stack.pop())
        self._mark_dirty()
        self._set_status(f"已撤销（还可撤销 {len(self._undo_stack)} 步 · "
                         f"Ctrl+Y 重做）")

    def redo(self) -> None:
        if not self._redo_stack:
            self._set_status("没有可重做的操作")
            return
        try:
            self._undo_stack.append(
                json.dumps(self.script.to_dict(), ensure_ascii=False))
        except Exception:                      # noqa: BLE001
            return
        self._restore_snapshot(self._redo_stack.pop())
        self._mark_dirty()
        self._set_status(f"已重做（还可重做 {len(self._redo_stack)} 步）")

    def _update_undo_ui(self) -> None:
        try:
            self.btn_undo.configure(
                state="normal" if self._undo_stack else "disabled")
            self.btn_redo.configure(
                state="normal" if self._redo_stack else "disabled")
        except Exception:                      # noqa: BLE001
            pass

    # ------------------------------------------------------ 未保存标记
    def _mark_dirty(self) -> None:
        if self._dirty:
            return
        self._dirty = True
        self._refresh_title()

    def _mark_clean(self) -> None:
        self._dirty = False
        self._refresh_title()

    def _refresh_title(self) -> None:
        try:
            from core import updater
            mark = " ● 未保存" if self._dirty else ""
            self.title(f"{APP_TITLE}   v{updater.APP_VERSION}{mark}")
        except Exception:                      # noqa: BLE001
            pass

    # ============================================================ 搜索与折叠
    def _apply_filter(self, *_) -> None:
        """按关键字过滤列表（匹配动作描述 / 类型 / 输入文本 / 注释）"""
        q = self.filter_var.get().strip()
        self._filter_text = q.lower()
        self._refresh_list()
        if q:
            n = len(self.tree.get_children())
            self._set_status(
                f"筛选「{q}」：{n} 条匹配" if n else f"筛选「{q}」：没有匹配的步骤")

    def _clear_filter(self, *_) -> None:
        if self.filter_var.get():
            self.filter_var.set("")
            self._apply_filter()
        self._set_status("已清除筛选")

    def _jump_to_first_hit(self, *_) -> None:
        """回车：选中并滚动到第一个匹配行（再按回车跳到下一个）"""
        rows = self.tree.get_children()
        if not rows:
            return
        cur = self.tree.selection()
        start = rows.index(cur[0]) + 1 if cur and cur[0] in rows else 0
        target = rows[start % len(rows)]
        self.tree.selection_set(target)
        self.tree.see(target)
        self.tree.focus(target)

    def _match(self, ev: Event, q: str) -> bool:
        hay = " ".join(str(x) for x in (
            ev.detail(), ev.type, ev.key or "", ev.text or "",
            ev.note or "", ev.cond_text() if ev.type in ("if", "else") else ""))
        return q in hay.lower()

    def _loop_pairs(self) -> dict[int, int]:
        """循环配对：{loop_start 索引: loop_end 索引}（未闭合的丢弃）"""
        pairs: dict[int, int] = {}
        stack: list[int] = []
        for i, ev in enumerate(self.script.events):
            if ev.type == "loop_start":
                stack.append(i)
            elif ev.type == "loop_end" and stack:
                pairs[stack.pop()] = i
        return pairs

    def _hidden_rows(self) -> set[int]:
        """被折叠隐藏的行索引"""
        if not self._folded:
            return set()
        hidden: set[int] = set()
        for start, end in self._loop_pairs().items():
            if start in self._folded:
                hidden.update(range(start + 1, end + 1))
        return hidden

    def toggle_fold(self, idx: int | None = None) -> None:
        """折叠 / 展开循环块（默认作用于选中行所在的循环）"""
        pairs = self._loop_pairs()
        if not pairs:
            self._set_status("脚本里没有循环块")
            return
        if idx is None:
            idx = self._selected_index()
        if idx is None:
            self._set_status("请先选中循环里的某一行")
            return
        target = idx
        if target not in pairs:                # 选中行在循环内部 → 折叠它所属的循环
            for s, e in pairs.items():
                if s < idx <= e:
                    target = s
                    break
            else:
                self._set_status("这一行不在循环里")
                return
        if target in self._folded:
            self._folded.discard(target)
            self._set_status(f"已展开第 {target + 1} 行的循环")
        else:
            self._folded.add(target)
            self._set_status(
                f"已折叠第 {target + 1} 行的循环（{pairs[target] - target - 1} 步）")
        self._refresh_list()
        if self.tree.exists(str(target)):
            self.tree.see(str(target))
            self.tree.selection_set(str(target))

    def fold_all(self) -> None:
        pairs = self._loop_pairs()
        if not pairs:
            self._set_status("脚本里没有循环块")
            return
        if self._folded:
            self._folded.clear()
            self._set_status("已展开全部循环")
        else:
            self._folded = set(pairs.keys())
            self._set_status(f"已折叠 {len(pairs)} 个循环块")
        self._refresh_list()

    # ============================================================ 回放倒计时
    def _countdown_seconds(self) -> int:
        try:
            return max(0, min(10, int(self.cfg.get("countdown", 3))))
        except Exception:                      # noqa: BLE001
            return 3

    def _start_countdown(self, on_done) -> None:
        """3-2-1 倒计时，给用户时间切到目标窗口；F10 或再点按钮可取消"""
        self._cd_done = on_done
        self._cd_left = self._countdown_seconds()
        self._cd_job = None
        self._cd_tick()

    def _cd_tick(self) -> None:
        if self._cd_left <= 0:
            self._cd_job = None
            done, self._cd_done = getattr(self, "_cd_done", None), None
            self.btn_play.configure(state="normal", text="▶ 回放")
            if done:
                done()
            return
        self.btn_play.configure(
            state="normal", text=f"▶ {self._cd_left} 秒后…")
        self._set_status(
            f"{self._cd_left} 秒后开始回放…（点「回放」或按 F10 取消）")
        self._cd_left -= 1
        self._cd_job = self.after(1000, self._cd_tick)

    def cancel_countdown(self) -> bool:
        """取消倒计时；返回是否确实取消了"""
        if getattr(self, "_cd_done", None) is None:
            return False
        if self._cd_job is not None:
            try:
                self.after_cancel(self._cd_job)
            except Exception:                  # noqa: BLE001
                pass
        self._cd_job = None
        self._cd_done = None
        self.btn_play.configure(state="normal", text="▶ 回放")
        self._set_status("已取消回放")
        return True

    # ============================================================ 快捷键
    def _bind_shortcuts(self) -> None:
        """全局快捷键。输入框内保留系统默认行为（如文本撤销）。"""
        self.bind_all("<Control-z>", self._on_ctrl_z)
        self.bind_all("<Control-Z>", self._on_ctrl_z)
        self.bind_all("<Control-y>", self._on_ctrl_y)
        self.bind_all("<Control-Y>", self._on_ctrl_y)
        self.bind_all("<Control-s>", self._on_ctrl_s)
        self.bind_all("<Control-S>", self._on_ctrl_s)
        self.bind_all("<Control-o>", self._on_ctrl_o)
        self.bind_all("<Control-f>", self._on_ctrl_f)
        self.bind_all("<Control-F>", self._on_ctrl_f)

    def _focus_in_text(self) -> bool:
        """焦点是否落在可输入控件里（此时不抢 Ctrl+Z 等）"""
        try:
            w = self.focus_get()
            if w is None:
                return False
            return isinstance(w, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox))
        except Exception:                      # noqa: BLE001
            return False

    def _on_ctrl_z(self, event=None):
        if self._focus_in_text():
            return None
        self.undo()
        return "break"

    def _on_ctrl_y(self, event=None):
        if self._focus_in_text():
            return None
        self.redo()
        return "break"

    def _on_ctrl_s(self, event=None):
        self.save_script()
        return "break"

    def _on_ctrl_o(self, event=None):
        self.open_script()
        return "break"

    def _on_ctrl_f(self, event=None):
        try:
            self.filter_entry.focus_set()
            self.filter_entry.select_range(0, "end")
        except Exception:                      # noqa: BLE001
            pass
        return "break"

    # ============================================================ 全局热键
    def _set_app_icon(self) -> None:
        """设置窗口与应用图标（打包后 PyInstaller 也会用同一份）"""
        import sys
        candidates = []
        base = getattr(sys, "_MEIPASS", None)     # PyInstaller 解包目录
        if base:
            candidates.append(os.path.join(base, "icon.png"))
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(here, "assets", "icon.png"))
        candidates.append(os.path.join(os.getcwd(), "assets", "icon.png"))

        for path in candidates:
            if os.path.exists(path):
                try:
                    from PIL import Image, ImageTk
                    img = Image.open(path)
                    self._icon_img = ImageTk.PhotoImage(img)   # 防被回收
                    self.iconphoto(True, self._icon_img)
                except Exception:
                    pass
                return

    def _register_hotkeys(self) -> None:
        self.hotkeys.register("stop_record", self._hk_stop_record)
        self.hotkeys.register("toggle_pause", self._hk_toggle_pause)
        self.hotkeys.register("abort_play", self._hk_abort_play)
        self.hotkeys.register("toggle_run", self._hk_toggle_run)
        self.hotkeys.start()

    def _hk_stop_record(self) -> None:
        """F9：停止录制（仅录制中才响应）"""
        if self.recorder.running:
            self.stop_recording()

    def _hk_toggle_pause(self) -> None:
        """F8：暂停 / 继续录制"""
        self.toggle_pause_recording()

    def _hk_abort_play(self) -> None:
        """F10：中断回放（倒计时中也用来取消）"""
        if self.cancel_countdown():
            return
        if self.player.running:
            self.abort_play()

    def _hk_toggle_run(self) -> None:
        """F11：一键开始/停止脚本（核心便利功能）"""
        if self.cancel_countdown():
            self._set_status("已通过热键取消回放")
            return
        if self.player.running:
            self.abort_play()
            self._set_status("已通过热键中断回放")
            return
        if self.recorder.running:
            return
        if not self.script.active_events:
            self._set_status("没有可执行的脚本")
            return
        self.start_play(hotkey_mode=True)

    # ============================================================ 构建 UI
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Rec.TButton", foreground="#c62828")
        style.configure("Play.TButton", foreground="#2e7d32")

        self._build_toolbar()

        body = ttk.Frame(self, padding=(10, 0))
        body.pack(fill="both", expand=True)

        # 最左：脚本库
        lib_frame = ttk.Frame(body, width=330)
        lib_frame.pack(side="left", fill="y", padx=(0, 8))
        lib_frame.pack_propagate(False)
        self._build_library(lib_frame)

        # 右侧：指令库 + 设置（须在中间 expand 之前打包，否则会被挤成 0 宽）
        right = ttk.Frame(body, width=430)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)
        # 右列内容较长（指令库 + 脚本设置 + 数据源），窗口不高时会被裁掉，
        # 因此放进可滚动容器——否则「数据源」整块看不见。
        r_canvas = tk.Canvas(
            right, highlightthickness=0, bd=0,
            background=style.lookup("TFrame", "background") or "#f0f0f0")
        r_vsb = ttk.Scrollbar(right, orient="vertical", command=r_canvas.yview)
        r_inner = ttk.Frame(r_canvas)
        r_win = r_canvas.create_window((0, 0), window=r_inner, anchor="nw")

        def _sync_right_scroll(_event=None):
            # 滚动范围：宽度必须锁到可视宽度（否则横向也能滚，右边露出白边）；
            # 高度取「内容请求高度」和「视口高度」的较大值，不要用 bbox("all")——
            # bbox 会随重排瞬时变大，滚动范围超出内容时滚到顶端/底端会各露一条白边。
            # 内容比视口矮时（窗口最大化后最明显）只用请求高度，画布底部会空出
            # 一块底色，看上去就是滚动区上下留白；这里把窗口项一起撑满视口。
            vw = r_canvas.winfo_width() or 1
            vh = r_canvas.winfo_height() or 1
            h = max(r_inner.winfo_reqheight(), vh)
            r_canvas.itemconfigure(r_win, height=h)
            r_canvas.configure(scrollregion=(0, 0, vw, h))

        def _on_right_resize(event):
            # 内容宽度跟着可视宽度走：写死宽度会让右侧空出一条竖白边
            r_canvas.itemconfigure(r_win, width=event.width)
            _sync_right_scroll()

        r_canvas.configure(yscrollcommand=r_vsb.set)
        r_canvas.bind("<Configure>", _on_right_resize)
        r_inner.bind("<Configure>", _sync_right_scroll)
        # 先放滚动条再放画布：反过来的话画布 expand 会占满宽度，
        # 把滚动条挤成 0 宽（面板能滚却看不到、拖不到）。
        r_vsb.pack(side="right", fill="y")
        r_canvas.pack(side="left", fill="both", expand=True)
        self._right_canvas = r_canvas          # 供测试/定位使用

        def _r_wheel(event):
            delta = -1 if getattr(event, "delta", 0) > 0 else 1
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            r_canvas.yview_scroll(delta, "units")

        self._build_command_panel(r_inner)
        self._build_settings(r_inner)

        # 只在这个区域滚轮才滚动右侧面板（不能用 bind_all，否则会抢走列表滚动）
        def _wheel_all(w):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                w.bind(seq, _r_wheel)
            for ch in w.winfo_children():
                _wheel_all(ch)

        _wheel_all(r_inner)
        _wheel_all(r_canvas)

        # 中间：事件列表 + 执行日志（上下分栏）
        center = ttk.PanedWindow(body, orient="vertical")
        center.pack(side="left", fill="both", expand=True)

        left = ttk.Frame(center)
        center.add(left, weight=3)
        self._build_event_list(left)

        log_box = ttk.Frame(center)
        center.add(log_box, weight=1)
        self._build_log_panel(log_box)

        self._build_statusbar()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x")

        self.btn_rec = ttk.Button(bar, text="● 开始录制", width=13,
                                  style="Rec.TButton", command=self.start_recording)
        self.btn_stop = ttk.Button(bar, text="■ 停止", width=9,
                                   command=self.stop_recording, state="disabled")
        self.btn_play = ttk.Button(bar, text="▶ 回放", width=9,
                                   style="Play.TButton", command=self.start_play)
        self.btn_abort = ttk.Button(bar, text="⏹ 中断", width=9,
                                    command=self.abort_play, state="disabled")
        for b in (self.btn_rec, self.btn_stop, self.btn_play, self.btn_abort):
            b.pack(side="left", padx=(0, 6))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="💾 保存", width=9,
                   command=self.save_script).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="📂 打开", width=9,
                   command=self.open_script).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="✖ 清空", width=9,
                   command=self.clear_script).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        # 调试
        self.btn_step = ttk.Button(bar, text="⏭ 单步", width=9,
                                   command=self.step_selected)
        self.btn_step_to = ttk.Button(bar, text="⤓ 执行到此", width=11,
                                      command=self.run_to_selected)
        self.btn_step.pack(side="left", padx=(0, 6))
        self.btn_step_to.pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        # 坐标校准（DPI 偏移的补救手段）
        ttk.Button(bar, text="🎯 校准坐标", width=11,
                   command=self.show_calibration).pack(side="left")

        # 涟漪特效测试（排查「看不到涟漪」；打包后同样可用）
        ttk.Button(bar, text="✨ 涟漪测试", width=10,
                   command=self.show_ripple_test).pack(side="left", padx=(8, 0))

        # 自动升级：点一下直接检查 → 有新版即下载安装（无需再点确认）
        ttk.Button(bar, text="🔄 升级", width=8,
                   command=self.upgrade_now).pack(side="left", padx=(8, 0))

        # 工具栏右侧空着会看起来像一条留白，这里放版本号和热键提示填满
        from core.updater import APP_VERSION as _ver
        info = ttk.Frame(bar)
        info.pack(side="right", padx=(12, 0))
        ttk.Label(info, text=f"v{_ver}",
                  foreground="#666").pack(side="right", padx=(12, 0))
        ttk.Label(info, text="F9 停止 · F10 中断 · F11 开始/停止",
                  foreground="#8a8a8a").pack(side="right")

    # ============================================================ 一键升级
    def show_ripple_test(self) -> None:
        """打开涟漪测试窗口"""
        try:
            from ui.ripple_test import open_ripple_test
            open_ripple_test(self)
        except Exception as exc:
            messagebox.showerror("打开失败", f"涟漪测试页启动失败：\n{exc}")

    def _check_pending_update(self) -> None:
        """核对上一轮升级是否真的生效。

        升级必须先把程序关掉才能覆盖 exe/文件，所以「成功与否」只能等
        下次启动才知道。这里读升级前留下的待办标记：
          · 当前版本已经 >= 目标版本 → 成功，静默清掉标记
          · 还是旧版本            → 明确告诉用户没生效，并给出日志位置
        """
        from core import updater
        try:
            m = updater.read_pending()
        except Exception:
            return
        if not m:
            return
        target = str(m.get("version") or "").strip()
        cur = updater.APP_VERSION
        try:
            updater.clear_pending()
        except Exception:
            pass
        if not target:
            return
        try:
            done = not updater.is_newer(target, cur)
        except Exception:
            done = True
        if done:
            if updater.parse_version(cur) > updater.parse_version(
                    str(m.get("from_version") or "")):
                self._set_status(f"已升级到 v{cur}")
            return

        res = updater.read_last_result()
        log = res.get("log") or m.get("log") or updater.recent_log()
        msg = (f"上次升级没有生效。\n\n"
               f"目标版本：v{target}\n"
               f"当前版本：v{cur}\n\n")
        if res.get("error"):
            msg += f"升级脚本报告：{res['error']}\n\n"
        msg += ("常见原因：程序没有真正退出、文件夹没有写入权限，"
                "或被杀毒软件拦住了替换。\n\n"
                "可以点「是」查看升级日志；也可以把升级包解压后，"
                "手动覆盖到程序文件夹，或用「一键覆盖更新」。\n\n"
                "现在打开升级日志吗？")
        if messagebox.askyesno("升级没有生效", msg, parent=self):
            self._open_path(log or updater.log_dir())

    def _open_path(self, path: str) -> None:
        """用系统默认方式打开文件 / 文件夹"""
        if not path:
            return
        try:
            if os.name == "nt":
                os.startfile(path)                     # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            messagebox.showinfo("文件位置", path, parent=self)

    def _autosave_before_update(self) -> str:
        """升级前把当前脚本另存一份，避免用户为了保住改动而不让程序退出"""
        try:
            d = app_dir() / "autosave"
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"升级前自动保存_{time.strftime('%Y%m%d_%H%M%S')}.ajjl"
            script_package.pack(self.script, str(p))
            return str(p)
        except Exception:
            return ""

    def _quit_for_update(self) -> None:
        """升级专用退出：绝不弹「要保存吗」，也绝不留在前台。

        旧版这里调的是 _on_close，只要脚本有未保存改动就会弹确认框；
        用户一点「取消」程序就不退，而升级脚本已经在覆盖文件了——
        结果就是「升级按钮点了没反应 / 版本没变」。
        """
        from core import updater
        try:
            if self._dirty and self.script.events:
                bak = self._autosave_before_update()
                if bak:
                    updater.upgrade_log(f"升级前已自动备份脚本：{bak}")
        except Exception:
            pass
        for stop in (
            lambda: self.recorder.running and self.recorder.stop(),
            lambda: self.player.running and self.player.stop(),
            lambda: self.hotkeys.stop(),
        ):
            try:
                stop()
            except Exception:
                pass
        try:
            self._hide_hud()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        # 必须确保进程真的退出：文件被占用时升级脚本会覆盖失败
        try:
            os._exit(0)
        except Exception:
            pass

    def _auto_check_update(self) -> None:
        """启动后台静默检查：有新版才提示，不弹任何其他窗口"""
        try:
            from core import updater
            if not updater.has_source():
                return
            import threading

            def work():
                try:
                    m = updater.active_manifest()
                except Exception:
                    return
                if updater.is_newer(m["version"], updater.APP_VERSION):
                    self.after(0, lambda: self._prompt_update(m))
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _prompt_update(self, m) -> None:
        from core import updater
        notes = (m.get("notes") or "").strip()
        msg = (f"发现新版本 v{m['version']}（当前 v{updater.APP_VERSION}）。\n\n")
        if notes:
            msg += notes[:300] + "\n\n"
        msg += "现在升级吗？"
        if messagebox.askyesno("有新版本", msg, parent=self):
            try:
                zp = updater.fetch_package(m)
                plan = updater.plan_update(zp, str(m.get("version") or ""))
            except Exception as exc:
                messagebox.showerror("升级失败", f"准备升级包失败：\n{exc}",
                                     parent=self)
                return
            updater.write_pending(str(m.get("version") or ""),
                                  str(m.get("package") or ""),
                                  str(m.get("source") or ""))
            try:
                updater.apply_update(plan)
            except Exception as exc:
                messagebox.showerror("升级失败", f"启动升级失败：\n{exc}",
                                     parent=self)
                return
            self._quit_for_update()

    def upgrade_now(self, silent: bool = False) -> None:
        """工具栏「升级」：一键完成检查 + 安装。

        升级来源按优先级：升级文件夹 → 网址（含 GitHub）。
        都没配置就打开升级窗口引导设置。
        """
        from core import updater

        if not updater.has_source():
            self.show_update_dialog()
            return

        prog = tk.Toplevel(self)
        prog.title("正在升级 · 按键精灵")
        prog.geometry("430x150")
        prog.resizable(False, False)
        prog.transient(self)
        box = ttk.Frame(prog, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=f"当前版本 v{updater.APP_VERSION} · 正在检查更新…",
                  font=("", 10, "bold")).pack(anchor="w")
        bar = ttk.Progressbar(box, mode="indeterminate", length=390)
        bar.pack(fill="x", pady=10)
        bar.start(12)
        src = updater.update_dir() or updater.update_url()
        hint = ttk.Label(box, text=src, foreground="#888", wraplength=390)
        hint.pack(anchor="w")

        def close_prog():
            try:
                bar.stop()
                prog.destroy()
            except Exception:
                pass

        def finish(msg=None):
            close_prog()
            if msg and not silent:
                messagebox.showinfo("升级", msg, parent=self)

        def work():
            try:
                m = updater.active_manifest()
            except Exception as exc:
                self.after(0, lambda e=exc: finish(
                    f"检查升级失败：\n{e}\n\n可在升级窗口里重新设置升级文件夹。"))
                return
            self.after(0, lambda: decide(m))

        def prepare(m):
            try:
                zp = updater.fetch_package(m)
                plan = updater.plan_update(zp, str(m.get("version") or ""))
            except Exception as exc:
                self.after(0, lambda e=exc: finish(f"准备升级包失败：\n{e}"))
                return
            self.after(0, lambda: self._install_update(plan, m, prog, bar))

        def decide(m):
            """在主线程判定版本：只有确认「包比本机旧或相同」才提示已是最新"""
            ver = (m.get("version") or "").strip()
            note = (m.get("note") or "").strip()
            if note:
                hint.configure(text=note)
            if ver and not updater.is_newer(ver, updater.APP_VERSION):
                finish(f"已经是最新版本（v{updater.APP_VERSION}）。")
                return
            if not ver:
                # 读不出版本号 → 没法判断新旧，按「已是最新」处理。
                # 不能直接问「要不要强行覆盖」：装下去可能比现在更旧，
                # 用户还以为是升级成功了。原因写进状态栏，弹窗只留一句话。
                if not silent:
                    name = Path(str(m.get("package") or "")).name or "升级包"
                    try:
                        self.status_var.set(
                            f"升级源里的「{name}」没有版本号"
                            f"（缺 version.txt / version.json），已忽略")
                    except Exception:              # noqa: BLE001
                        pass
                finish(f"已经是最新版本（v{updater.APP_VERSION}）。")
                return
            hint.configure(text=f"发现新版本 v{ver}，正在准备…")
            threading.Thread(target=prepare, args=(m,), daemon=True).start()

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _install_update(self, plan, manifest, prog, bar) -> None:
        """确认并执行升级，然后退出程序让升级脚本接管"""
        from core import updater

        notes = (manifest.get("notes") or "").strip()
        ver = (manifest.get("version") or "").strip()
        if ver:
            msg = f"发现新版本 v{ver}（当前 v{updater.APP_VERSION}）。\n\n"
        else:
            msg = f"即将用这个升级包覆盖安装（当前 v{updater.APP_VERSION}）。\n\n"
        if notes:
            msg += "更新内容：\n" + notes[:500] + "\n\n"
        hint = getattr(plan, "hint", "") or ""
        if hint:
            msg += "⚠ 注意：\n" + hint + "\n\n"
        msg += ("现在升级？程序会关闭，约 10 秒后自动重启。\n\n"
                "如果升级后没有自动打开，请到程序文件夹里手动双击"
                "「启动按键精灵.bat」。\n\n"
                "升级失败的详细原因会写进日志，下次启动也会提示你。")
        ok = messagebox.askyesno("立即升级", msg, parent=self)
        try:
            bar.stop()
            prog.destroy()
        except Exception:
            pass
        if not ok:
            return
        updater.write_pending(ver, str(manifest.get("package") or ""),
                              str(manifest.get("source") or ""))
        try:
            updater.apply_update(plan)
        except Exception as exc:
            updater.clear_pending()
            messagebox.showerror("升级失败", f"启动升级失败：\n{exc}", parent=self)
            return
        self._quit_for_update()

    def show_update_dialog(self, start_check: bool = False) -> None:
        """升级窗口：默认走「文件夹 / 本地 zip」两条最简单的路，GitHub 收进高级。"""
        from core import updater

        win = tk.Toplevel(self)
        win.title("升级 · 按键精灵")
        win.geometry("660x660")
        win.minsize(620, 580)
        win.transient(self)
        try:
            self.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - 660) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - 660) // 2)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"当前版本 v{updater.APP_VERSION}",
                  font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(frm, text="升级不需要 GitHub：把新版本的 zip 放进一个文件夹，"
                            "程序自己去那里取；也可以直接选文件。",
                  foreground="#666", wraplength=600, justify="left"
                  ).pack(anchor="w", pady=(2, 8))

        # ---------------- 方式一：升级文件夹 ----------------
        f1 = ttk.LabelFrame(frm, text="方式一 · 升级文件夹（推荐，设置一次就好）",
                            padding=10)
        f1.pack(fill="x")
        r1 = ttk.Frame(f1)
        r1.pack(fill="x")
        dir_var = tk.StringVar(value=updater.update_dir())
        ttk.Entry(r1, textvariable=dir_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(r1, text="浏览…", width=8,
                   command=lambda: pick_dir()).pack(side="left", padx=(6, 0))
        r1b = ttk.Frame(f1)
        r1b.pack(fill="x", pady=(6, 0))
        ttk.Button(r1b, text="生成升级包到这个文件夹",
                   command=lambda: export_zip()).pack(side="left")
        ttk.Label(r1b, text="（把当前版本打成 zip，再复制到其他电脑的同一文件夹）",
                  foreground="#888").pack(side="left", padx=6)
        ttk.Label(f1, text="以后有新版本，把 zip 丢进这个文件夹，点「立即升级」即可。"
                           "文件夹可以是：本机目录、U 盘、局域网共享（\\\\电脑名\\共享）"
                           "或坚果云/OneDrive 的同步目录。",
                  foreground="#888", wraplength=590, justify="left"
                  ).pack(anchor="w", pady=(6, 0))

        # ---------------- 方式二：直接选包 ----------------
        f2 = ttk.LabelFrame(frm, text="方式二 · 直接选升级包", padding=10)
        f2.pack(fill="x", pady=(10, 0))
        ttk.Button(f2, text="选择 zip 文件并升级",
                   command=lambda: pick_zip()).pack(side="left")
        ttk.Label(f2, text="拿到新版本 zip 时用这个，选完自动检查并升级。",
                  foreground="#888").pack(side="left", padx=10)

        # ---------------- 高级：网址 / GitHub ----------------
        adv_row = ttk.Frame(frm)
        adv_row.pack(fill="x", pady=(10, 0))
        adv = ttk.Frame(frm)

        def toggle_adv():
            if adv.winfo_manager():
                adv.pack_forget()
                btn_adv.configure(text="▸ 高级：网址 / GitHub 自动更新")
            else:
                build_adv()
                adv.pack(fill="x", pady=(6, 0))
                btn_adv.configure(text="▾ 高级：网址 / GitHub 自动更新")

        btn_adv = ttk.Button(adv_row, text="▸ 高级：网址 / GitHub 自动更新",
                             command=toggle_adv)
        btn_adv.pack(side="left")

        # ---------------- 日志 ----------------
        info = tk.Text(frm, height=8, relief="flat", bg="#f5f5f5",
                       font=("Microsoft YaHei UI", 9), wrap="word",
                       state="disabled")
        info.pack(fill="both", expand=True, pady=(10, 0))

        def log(msg, tag=""):
            info.configure(state="normal")
            info.insert("end", msg + "\n", tag or ())
            info.see("end")
            info.configure(state="disabled")
        info.tag_configure("ok", foreground="#2e7d32")
        info.tag_configure("new", foreground="#c62828")
        info.tag_configure("dim", foreground="#888")

        # ---------------- 公共流程 ----------------
        def run_check(get_manifest, tip=""):
            log("检查中…" + (f"（{tip}）" if tip else ""), "dim")

            def work():
                try:
                    m = get_manifest()
                except Exception as exc:
                    self.after(0, lambda: log(f"✗ 检查失败：{exc}", "new"))
                    return
                self.after(0, lambda: on_manifest(m))
            import threading
            threading.Thread(target=work, daemon=True).start()

        def on_manifest(m):
            ver = (m.get("version") or "").strip()
            newer = bool(ver) and updater.is_newer(ver, updater.APP_VERSION)
            if newer:
                log(f"✓ 发现新版本 v{ver}（当前 v{updater.APP_VERSION}）", "new")
            elif ver:
                log(f"已是最新版本（来源里是 v{ver}）", "ok")
            else:
                log("已是最新版本（升级源里的包没有版本号，已忽略）", "ok")
            if m.get("note"):
                log(f"! {m['note']}", "dim")
            if m.get("package"):
                log(f"  升级包：{Path(str(m['package'])).name}", "dim")
            notes = (m.get("notes") or "").strip()
            if notes:
                for line in notes.splitlines()[:8]:
                    log("  " + line)
            if not newer:
                return
            msg = f"发现新版本 v{ver}（当前 v{updater.APP_VERSION}）。"
            if notes:
                msg += "\n\n更新内容：\n" + notes[:400]
            msg += "\n\n现在升级？程序会关闭，随后自动替换文件并重启。"
            if not messagebox.askyesno("立即升级", msg, parent=win):
                log("已取消升级。", "dim")
                return
            install(m)

        def install(m):
            log("准备升级包…", "dim")

            def work():
                try:
                    zp = updater.fetch_package(m)
                    plan = updater.plan_update(zp, str(m.get("version") or ""))
                except Exception as exc:
                    self.after(0, lambda: log(f"✗ 准备失败：{exc}", "new"))
                    return
                self.after(0, lambda: go(plan, m))
            import threading
            threading.Thread(target=work, daemon=True).start()

        def go(plan, m):
            if getattr(plan, "hint", ""):
                log(f"⚠ {plan.hint}", "new")
                if not messagebox.askyesno(
                        "升级包和当前版本对不上", plan.hint + "\n\n仍然继续吗？",
                        parent=win):
                    log("已取消升级。", "dim")
                    return
            try:
                updater.write_pending(str(m.get("version") or ""),
                                      str(m.get("package") or ""),
                                      str(m.get("source") or ""))
                updater.apply_update(plan)
            except Exception as exc:
                updater.clear_pending()
                log(f"✗ 启动升级失败：{exc}", "new")
                return
            log(f"程序即将关闭，升级脚本接管…（日志：{plan.log}）", "ok")
            self.after(300, self._quit_for_update)

        # ---------------- 方式一 / 二 回调 ----------------
        def pick_dir():
            import tkinter.filedialog as fd
            d = fd.askdirectory(title="选择放升级包的文件夹",
                                initialdir=dir_var.get() or None)
            if d:
                dir_var.set(d)
                save_dir()

        def save_dir():
            d = dir_var.get().strip()
            updater.set_update_dir(d)
            log(f"已记住升级文件夹：{d}", "ok")
            return bool(d)

        def check_dir():
            d = dir_var.get().strip()
            if not d:
                log("✗ 请先选择或填写升级文件夹", "new")
                return None
            try:
                updater.set_update_dir(d)
            except Exception:
                pass
            return d

        def do_check_dir():
            d = check_dir()
            if d:
                run_check(lambda: updater.check_update_dir(d), d)

        def export_zip():
            import tkinter.filedialog as fd
            d = dir_var.get().strip()
            if not d:
                d = fd.askdirectory(title="选择导出到哪个文件夹")
                if not d:
                    return
                dir_var.set(d)
                updater.set_update_dir(d)
            try:
                out = Path(d) / f"keymacro-{updater.APP_VERSION}.zip"
                zp = updater.build_update_zip(out)
            except Exception as exc:
                log(f"✗ 生成失败：{exc}", "new")
                return
            log(f"✓ 已生成：{zp}", "ok")
            log("把这个 zip 放到其他电脑设置的升级文件夹里，对方点升级即可。", "dim")

        def pick_zip():
            import tkinter.filedialog as fd
            path = fd.askopenfilename(
                title="选择升级包（zip）",
                filetypes=[("升级包", "*.zip"), ("所有文件", "*.*")])
            if not path:
                return
            log(f"已选择：{path}", "dim")
            on_manifest(updater.manifest_from_zip(path))

        # ---------------- 高级区（延迟构建） ----------------
        def build_adv():
            if adv.winfo_children():
                return
            g = ttk.LabelFrame(adv, text="网络升级（网址 / GitHub）", padding=10)
            g.pack(fill="x")
            urow = ttk.Frame(g)
            urow.pack(fill="x")
            url_var = tk.StringVar(value=updater.update_url())
            ttk.Label(urow, text="地址", width=5).pack(side="left")
            ttk.Entry(urow, textvariable=url_var).pack(
                side="left", fill="x", expand=True, padx=6)
            ttk.Button(urow, text="检查", width=6,
                       command=lambda: do_check_url(url_var)).pack(side="left")

            gh = ttk.Frame(g)
            gh.pack(fill="x", pady=(8, 0))
            ttk.Label(gh, text="GitHub 仓库", width=10).pack(side="left")
            repo_var = tk.StringVar(value=updater.github_repo())
            ttk.Entry(gh, textvariable=repo_var).pack(
                side="left", fill="x", expand=True, padx=6)
            row2 = ttk.Frame(g)
            row2.pack(fill="x", pady=(4, 0))
            ttk.Label(row2, text="Token", width=10).pack(side="left")
            token_var = tk.StringVar(value=updater.github_token())
            tok = ttk.Entry(row2, textvariable=token_var, show="•")
            tok.pack(side="left", fill="x", expand=True, padx=6)
            ttk.Checkbutton(row2, text="显示",
                            command=lambda: tok.configure(
                                show="" if tok.cget("show") else "•")
                            ).pack(side="left")
            row3 = ttk.Frame(g)
            row3.pack(fill="x", pady=(6, 0))
            btn_setup = ttk.Button(
                row3, text="一键创建仓库并发布当前版本",
                command=lambda: _setup_impl(repo_var, token_var, btn_setup))
            btn_setup.pack(side="left")
            ttk.Button(row3, text="Token 页面",
                       command=lambda: self._open_url(
                           "https://github.com/settings/tokens/new"
                           "?scopes=repo&description=KeyMacro")
                       ).pack(side="left", padx=6)
            ttk.Label(g, text="没装过 GitHub 就不用管这一栏，上面的方式一/二已经够用。",
                      foreground="#888").pack(anchor="w", pady=(6, 0))

        def do_check_url(url_var):
            url = url_var.get().strip()
            if not url:
                log("✗ 请填写网址", "new")
                return
            updater.set_update_url(url)
            run_check(lambda: updater.fetch_manifest(url), url)

        def _setup_impl(repo_var, token_var, btn):
            repo = repo_var.get().strip()
            token = token_var.get().strip()
            if not repo or not token:
                log("✗ 仓库与 Token 都要填", "new")
                return
            updater.set_github_token(token)
            btn.configure(state="disabled")
            log("正在创建仓库并发布当前版本…", "dim")

            def work():
                try:
                    res = updater.setup_github_repo(repo)
                except Exception as exc:
                    self.after(0, lambda: (log(f"✗ 失败：{exc}", "new"),
                                           btn.configure(state="normal")))
                    return
                self.after(0, lambda: done(res))
            import threading
            threading.Thread(target=work, daemon=True).start()

        def done(res):
            log(f"✓ 已发布 {res['tag']} 到 {res['repo']}", "ok")
            log("以后点工具栏「🔄 升级」即可一键更新。", "dim")

        # ---------------- 底部按钮 ----------------
        brow = ttk.Frame(frm)
        brow.pack(fill="x", pady=(10, 0))
        def auto_check():
            if not updater.has_source():
                log("请先选择升级文件夹（方式一），或用方式二直接选 zip。", "new")
                return
            run_check(updater.active_manifest)

        ttk.Button(brow, text="↑ 立即升级（用已设置的来源）",
                   command=auto_check).pack(side="left")
        ttk.Button(brow, text="保存文件夹并检查",
                   command=do_check_dir).pack(side="left", padx=6)
        ttk.Button(brow, text="升级日志",
                   command=lambda: self._open_path(
                       updater.recent_log() or str(updater.log_dir()))
                   ).pack(side="left", padx=6)
        ttk.Button(brow, text="关闭", command=win.destroy).pack(side="right")

        log("步骤：选一个文件夹（方式一）放好升级 zip → 点「保存文件夹并检查」；"
            "或直接用方式二选 zip。两步都行，不需要 GitHub。", "dim")
        if start_check and updater.has_source():
            self.after(200, lambda: run_check(updater.active_manifest))
        win.bind("<Escape>", lambda e: win.destroy())

    def _open_url(self, url: str) -> None:
        """用系统默认浏览器打开链接"""
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            messagebox.showinfo("链接", url)

    def _build_event_list(self, parent: ttk.Frame) -> None:
        cols = ("idx", "time", "gap", "action")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings",
                                 selectmode="extended")
        self.tree.heading("idx", text="#")
        self.tree.heading("time", text="时间")
        self.tree.heading("gap", text="间隔")
        self.tree.heading("action", text="动作")
        self.tree.column("idx", width=40, anchor="center", stretch=False)
        self.tree.column("time", width=74, anchor="center", stretch=False)
        self.tree.column("gap", width=70, anchor="center", stretch=False)
        self.tree.column("action", width=360, anchor="w")

        # 顶部工具条：撤销 / 重做 / 搜索 / 折叠（放在列表上方，就近操作）
        top = ttk.Frame(parent)
        top.pack(side="top", fill="x", pady=(0, 6))

        self.btn_undo = ttk.Button(top, text="↶ 撤销", width=8,
                                   command=self.undo, state="disabled")
        self.btn_redo = ttk.Button(top, text="↷ 重做", width=8,
                                   command=self.redo, state="disabled")
        self.btn_undo.pack(side="left", padx=(0, 4))
        self.btn_redo.pack(side="left", padx=(0, 8))

        self.filter_var = tk.StringVar()
        ttk.Button(top, text="⊟ 折叠", width=8,
                   command=self.fold_all).pack(side="right", padx=(0, 4))
        ttk.Button(top, text="✕", width=3,
                   command=self._clear_filter).pack(side="right", padx=(4, 6))
        ttk.Label(top, text="🔍").pack(side="left", padx=(0, 2))
        ent = ttk.Entry(top, textvariable=self.filter_var, width=12)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<KeyRelease>", self._apply_filter)
        ent.bind("<Escape>", self._clear_filter)
        ent.bind("<Return>", self._jump_to_first_hit)
        self.filter_entry = ent

        # 按钮行须先打包，否则会被下方 expand 的列表挤成 0 高
        edit_row = ttk.Frame(parent)
        edit_row.pack(side="bottom", fill="x", pady=(6, 0))
        # 两行布局：面板变窄时也不会把按钮挤出可视区
        r1 = ttk.Frame(edit_row)
        r1.pack(fill="x")
        r2 = ttk.Frame(edit_row)
        r2.pack(fill="x", pady=(4, 0))

        ttk.Button(r1, text="删除", width=6,
                   command=self.delete_selected).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(r1, text="↑", width=3,
                   command=lambda: self.move_selected(-1)).pack(side="left", padx=(0, 2))
        ttk.Button(r1, text="↓", width=3,
                   command=lambda: self.move_selected(1)).pack(side="left", padx=(0, 8))
        ttk.Button(r1, text="启用/禁用",
                   command=self.toggle_selected).pack(side="left", fill="x", expand=True)

        ttk.Button(r2, text="改时间",
                   command=self.edit_time).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(r2, text="改间隔",
                   command=self.edit_gap).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(r2, text="统一间隔…",
                   command=self.set_gap_batch).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(r2, text="加注释",
                   command=self.add_comment).pack(side="left", fill="x", expand=True)

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        # 按指令类型的行着色
        for tag, color in (
            ("mouse",   "#0d47a1"),   # 鼠标：深蓝
            ("key",     "#6a1fb0"),   # 键盘：紫
            ("flow",    "#e65100"),   # 循环/跳出：橙
            ("cond",    "#00695c"),   # 条件：青
            ("wait",    "#795548"),   # 等待：棕
            ("findimg", "#ad1457"),   # 找图：玫红
            ("note",    "#8a8a8a"),   # 注释：灰
            ("disabled", "#b0b0b0"),  # 禁用行：浅灰
        ):
            self.tree.tag_configure(tag, foreground=color)
        # 回放出错行：只设底色，不覆盖上面的文字颜色
        self.tree.tag_configure("fail", background="#ffe0e0")

        # 双击编辑 / 右键菜单
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<space>", lambda e: self.toggle_selected())

        # 按住行上下拖动 → 调整执行顺序（与 ↑ / ↓ 按钮同一套落库逻辑）
        self._drag_iid: str | None = None
        self._drag_y0 = 0
        self._drag_on = False
        self._drop_row: str | None = None
        self.tree.tag_configure("dropmark", background="#cfe8ff")
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_drop)

    _TAG_BY_TYPE = {
        "click": "mouse", "move": "mouse", "drag": "mouse",
        "keytype": "key", "edit": "key", "key": "key", "paste_text": "key",
        "loop_start": "flow", "loop_end": "flow", "break": "flow",
        "if": "cond", "else": "cond", "if_end": "cond",
        "delay": "wait", "wait": "wait",
        "findimg": "findimg", "find_image": "findimg", "ocr": "findimg",
        "comment": "note",
    }

    @classmethod
    def _event_tag(cls, ev: Event) -> tuple[str, ...]:
        tags = [cls._TAG_BY_TYPE.get(ev.type, "mouse")]
        if getattr(ev, "enabled", True) is False:
            tags.append("disabled")
        return tuple(tags)

    def _insert_event_row(self, i: int, ev: Event, *, prepend: bool = False,
                          pairs: dict[int, int] | None = None) -> None:
        """统一插入事件行（含类型着色 + 与上一行的间隔）"""
        evs = self.script.events
        prev_t = evs[i - 1].t if 0 < i < len(evs) else 0
        gap = max(0, ev.t - prev_t)
        detail = ev.detail()
        if pairs and i in self._folded and i in pairs:
            n = pairs[i] - i - 1
            detail += f"    ⊟ 已折叠 {n} 步（双击序号展开）"
        kw = dict(values=(i + 1, f"{ev.t / 1000:.2f}s",
                          f"{gap / 1000:.2f}s", detail),
                  tags=self._event_tag(ev))
        if prepend:
            self.tree.insert("", 0, iid=str(i), **kw)
        else:
            self.tree.insert("", "end", iid=str(i), **kw)

    # ------------------------------------------------------------ 拖拽排序
    def _on_tree_press(self, event: tk.Event) -> None:
        """记下按下的行，等真的拖动了再进入拖拽状态

        不返回 "break"：正常的单击选中、双击编辑还要靠后面的类绑定。
        """
        self._drag_iid = None
        self._drag_on = False
        self._drop_row = None
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self._drag_iid = row
        self._drag_y0 = event.y

    def _on_tree_drag(self, event: tk.Event):
        if not self._drag_iid:
            return None
        if not self._drag_on:
            # 鼠标要移动几像素才算拖：否则双击、单击微抖都会被当成排序
            if abs(event.y - self._drag_y0) < 6:
                return None
            self._drag_on = True
            try:
                self.tree.configure(cursor="hand2")
            except tk.TclError:
                pass
        row = self.tree.identify_row(event.y)
        if row != self._drop_row:           # 只在目标行变了才重画，长脚本也不卡
            self._drop_row = row
            self._mark_drop(row)
        return "break"

    def _on_tree_drop(self, event: tk.Event):
        if not self._drag_on:
            self._drag_iid = None
            return None
        self._drag_on = False
        src_iid, self._drag_iid = self._drag_iid, None
        self._drop_row = None
        self._mark_drop(None)
        try:
            self.tree.configure(cursor="")
        except tk.TclError:
            pass
        target = self.tree.identify_row(event.y)
        if not src_iid or not target:
            return "break"
        try:
            src, tgt = int(src_iid), int(target)
        except ValueError:
            return "break"
        # 落在目标行的上半区 = 插到它前面，下半区 = 插到它后面
        bbox = self.tree.bbox(target)
        mid = (bbox[1] + bbox[3] // 2) if bbox else 0
        dst = tgt + (1 if event.y > mid else 0)
        if dst > src:
            dst -= 1
        self._move_event(src, dst)
        return "break"

    def _mark_drop(self, row: str | None) -> None:
        """把「松手后会落在哪一行」标出来（不改动真实顺序，拖动中零成本）"""
        for iid in self.tree.get_children():
            raw = self.tree.item(iid, "tags")
            if isinstance(raw, str):
                tags = [t for t in raw.split() if t]
            else:
                tags = [str(t) for t in (raw or ())]
            tags = [t for t in tags if t != "dropmark"]
            if iid == row and iid != self._drag_iid:
                tags.append("dropmark")
            self.tree.item(iid, tags=tuple(tags))

    def _move_event(self, src: int, dst: int) -> bool:
        """把第 src 条挪到第 dst 位（执行顺序变了，节奏不变）

        时间槽留在原地：拖动只回答「第几步做什么」，不该把整段脚本的
        时间轴一起搅乱，否则每挪一行后面所有的 t 都得跟着改。
        """
        evs = self.script.events
        n = len(evs)
        if not (0 <= src < n):
            return False
        dst = max(0, min(dst, n - 1))
        if dst == src:
            return False
        self._snapshot()
        slots = [e.t for e in evs]
        ev = evs.pop(src)
        evs.insert(dst, ev)
        for e, t in zip(evs, slots):
            e.t = t
        if self._folded:
            self._folded.clear()            # 折叠位置按行号记的，重排后失效
        self._refresh_list()
        self.tree.selection_set(str(dst))
        self.tree.see(str(dst))
        self._set_status(f"已调整顺序：第 {src + 1} 行 → 第 {dst + 1} 行（Ctrl+Z 可撤销）")
        return True

    def _command_defs(self) -> list[tuple[str, list[tuple[str, object]]]]:
        """指令库定义：面板按钮与右键「插入指令」菜单共用。

        (分组, [(显示名, 回调)])
        """
        return [
            ("鼠标", [
                ("左键点击", self._cmd_click_left),
                ("右键点击", self._cmd_click_right),
                ("双击", self._cmd_double_click),
                ("拖拽", self._cmd_drag),
            ]),
            ("选择 / 复制 / 粘贴 / 删除", [
                ("全选", lambda: self._insert_edit("select_all")),
                ("复制", lambda: self._insert_edit("copy")),
                ("剪切", lambda: self._insert_edit("cut")),
                ("粘贴", lambda: self._insert_edit("paste")),
                ("删除", lambda: self._insert_edit("delete")),
                ("退格", lambda: self._insert_edit("backspace")),
                ("撤销", lambda: self._insert_edit("undo")),
                ("重做", lambda: self._insert_edit("redo")),
            ]),
            ("键盘", [
                ("输入文本", self._cmd_type_text),
                ("组合键", self._cmd_hotkey),
                ("回车", lambda: self._insert_edit("enter")),
                ("Tab", lambda: self._insert_edit("tab")),
                ("Esc", lambda: self._insert_edit("esc")),
            ]),
            ("控制 / 等待 / 找图", [
                ("等待ms", self._cmd_delay),
                ("循环开始", self._cmd_loop_start),
                ("循环结束", self._cmd_loop_end),
                ("跳出循环", self._cmd_break),
                ("如果", self._cmd_if),
                ("否则", self._cmd_else),
                ("结束判断", self._cmd_if_end),
                ("找图点击", self._cmd_find_image),
                ("验证码识别", self._cmd_ocr),
                ("注释", self._cmd_comment),
            ]),
        ]

    def _build_command_panel(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="指令库（点击插入到末尾）", padding=8)
        box.pack(fill="x")

        for title, items in self._command_defs():
            self._group(box, title, items)

    def _group(self, parent: ttk.Frame, title: str, items: list[tuple]) -> None:
        ttk.Label(parent, text=title, foreground="#555").pack(anchor="w", pady=(6, 2))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        for i, (label, cmd) in enumerate(items):
            b = ttk.Button(row, text=label, width=9, command=cmd)
            b.grid(row=i // 4, column=i % 4, padx=2, pady=2, sticky="ew")
        for c in range(4):
            row.columnconfigure(c, weight=1)

    def _build_settings(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="脚本设置", padding=8)
        box.pack(fill="x", pady=(10, 0))

        ttk.Label(box, text="名称").grid(row=0, column=0, sticky="w", pady=2)
        self.name_var = tk.StringVar(value=self.script.name)
        ttk.Entry(box, textvariable=self.name_var, width=18).grid(row=0, column=1, pady=2, sticky="ew")

        ttk.Label(box, text="速度").grid(row=1, column=0, sticky="w", pady=2)
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Combobox(box, textvariable=self.speed_var, width=16,
                     values=("0.25", "0.5", "1.0", "1.5", "2.0", "3.0", "4.0")
                     ).grid(row=1, column=1, pady=2, sticky="ew")

        ttk.Label(box, text="循环次数").grid(row=2, column=0, sticky="w", pady=2)
        self.loop_var = tk.StringVar(value="1")
        ttk.Spinbox(box, from_=1, to=9999, textvariable=self.loop_var,
                    width=17).grid(row=2, column=1, pady=2, sticky="ew")

        ttk.Label(box, text="随机抖动(ms)").grid(row=3, column=0, sticky="w", pady=2)
        self.jitter_var = tk.StringVar(value="0")
        ttk.Spinbox(box, from_=0, to=2000, textvariable=self.jitter_var,
                    width=17).grid(row=3, column=1, pady=2, sticky="ew")

        ttk.Label(box, text="失败重试").grid(row=4, column=0, sticky="w", pady=2)
        self.retry_var = tk.StringVar(value="0")
        ttk.Spinbox(box, from_=0, to=10, textvariable=self.retry_var,
                    width=17).grid(row=4, column=1, pady=2, sticky="ew")

        # 回放倒计时：点回放后先等几秒，给时间切到目标窗口
        ttk.Label(box, text="回放倒计时(秒)").grid(row=5, column=0,
                                                   sticky="w", pady=2)
        cd = ttk.Spinbox(box, from_=0, to=10, textvariable=self.countdown_var,
                         width=17, command=self._apply_countdown_setting)
        cd.grid(row=5, column=1, pady=2, sticky="ew")
        cd.bind("<FocusOut>", lambda e: self._apply_countdown_setting())

        # 开始录制时如何处理已有脚本（防止手滑清空）
        ttk.Label(box, text="开始录制时").grid(row=6, column=0, sticky="w", pady=2)
        rmc = ttk.Combobox(box, textvariable=self.record_mode_var, width=16,
                           state="readonly",
                           values=tuple(self._REC_MODE_LABELS.values()))
        rmc.grid(row=6, column=1, pady=2, sticky="ew")
        rmc.bind("<<ComboboxSelected>>",
                 lambda e: self._apply_record_mode_setting())

        self.focus_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="回放前聚焦目标窗口",
                        variable=self.focus_var).grid(row=7, column=0,
                                                     columnspan=2, sticky="w", pady=(4, 2))

        # 录制时的点击涟漪特效
        self.ripple_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="录制时点击显示涟漪特效",
                        variable=self.ripple_var,
                        command=self._apply_ripple_setting).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))
        # 回放时的点击涟漪：脚本跑起来能直接看到点在哪
        self.play_ripple_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="回放时点击显示涟漪特效",
                        variable=self.play_ripple_var,
                        command=self._apply_ripple_setting).grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(2, 0))
        # 涟漪模式：自动（键控）在个别显卡/缩放环境下可能不显示，提供闪烁兼容
        self.ripple_mode_var = tk.StringVar(value="自动（键控镂空）")
        cb = ttk.Combobox(box, textvariable=self.ripple_mode_var, width=17,
                          state="readonly",
                          values=("自动（键控镂空）", "闪烁（兼容模式）"))
        cb.grid(row=11, column=0, columnspan=2, sticky="w", pady=(2, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_ripple_setting())

        # 移动轨迹精简：每个动作前只保留最近 N 个坐标
        ttk.Label(box, text="保留移动点").grid(row=8, column=0, sticky="w", pady=2)
        self.moves_var = tk.StringVar(value=str(self.recorder.moves_before_click))
        mv = ttk.Spinbox(box, from_=0, to=10, textvariable=self.moves_var,
                         width=17, command=self._apply_moves_setting)
        mv.grid(row=8, column=1, pady=2, sticky="ew")
        mv.bind("<FocusOut>", lambda e: self._apply_moves_setting())
        ttk.Label(box, text="每个动作前只保留最近几个鼠标坐标（0=不保留）",
                  foreground="#888", wraplength=300,
                  justify="left").grid(row=9, column=0, columnspan=2,
                                       sticky="w", pady=(0, 2))
        box.columnconfigure(1, weight=1)

        # ---- 数据源（Excel/CSV 变量）----
        ds = ttk.LabelFrame(parent, text="数据源（Excel/CSV 变量）", padding=8)
        ds.pack(fill="x", pady=(10, 0))

        ttk.Label(ds, text="数据文件").grid(row=0, column=0, sticky="w", pady=2)
        self.data_var = tk.StringVar(value=self.script.data_file)
        ttk.Entry(ds, textvariable=self.data_var, width=14).grid(
            row=0, column=1, pady=2, sticky="ew")
        ttk.Button(ds, text="📂", width=3,
                   command=self._pick_data_file).grid(row=0, column=2, padx=(4, 0))

        ttk.Label(ds, text="取数模式").grid(row=1, column=0, sticky="w", pady=2)
        self.data_mode_var = tk.StringVar(value=self.script.data_mode)
        ttk.Combobox(ds, textvariable=self.data_mode_var, width=14,
                     state="readonly",
                     values=("off", "per_loop", "fixed")).grid(
            row=1, column=1, pady=2, sticky="ew")

        ttk.Label(ds, text="起始行").grid(row=2, column=0, sticky="w", pady=2)
        self.data_start_var = tk.StringVar(value=str(self.script.data_start_row))
        ttk.Spinbox(ds, from_=2, to=99999, textvariable=self.data_start_var,
                    width=14).grid(row=2, column=1, pady=2, sticky="ew")

        self.data_hint = tk.StringVar(value="")
        ttk.Label(ds, textvariable=self.data_hint, foreground="#888",
                  wraplength=300, justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ds.columnconfigure(1, weight=1)
        self.data_var.trace_add("write", lambda *a: self._refresh_data_hint())
        self._refresh_data_hint()

        self.stat_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.stat_var, justify="left",
                  foreground="#444").pack(anchor="w", pady=(10, 0))

    def _apply_moves_setting(self) -> None:
        """把「保留移动点」设置同步给录制器"""
        try:
            n = int(self.moves_var.get())
        except (ValueError, AttributeError):
            return
        self.recorder.moves_before_click = max(0, min(10, n))

    def _apply_countdown_setting(self) -> None:
        """回放倒计时秒数（0 = 关闭），立即写盘"""
        try:
            n = max(0, min(10, int(self.countdown_var.get())))
        except (ValueError, AttributeError, tk.TclError):
            return
        self.countdown_var.set(str(n))
        self.cfg["countdown"] = n
        save_config({"countdown": n})
        self._set_status(f"回放倒计时：{'已关闭' if n == 0 else f'{n} 秒'}")

    def _apply_record_mode_setting(self) -> None:
        """开始录制时对已有脚本的处理方式"""
        rev = {v: k for k, v in self._REC_MODE_LABELS.items()}
        mode = rev.get(self.record_mode_var.get(), "ask")
        self.cfg["record_mode"] = mode
        save_config({"record_mode": mode})
        self._set_status(f"开始录制时：{self.record_mode_var.get()}")

    def _autocheck_ripple(self) -> None:
        """启动自检：键控色不可用时自动切到闪烁兼容模式（只做一次）"""
        try:
            import ui.ripple as _rp
            cfg = _rp.load_ripple_mode()
            if cfg == "flash":
                self.ripple.set_mode("flash")
                self.ripple_mode_var.set("闪烁（兼容模式）")
                return
            if cfg == "key":
                self.ripple.set_mode(None)
                return
            # auto：只做静态判断，不在屏幕上画探针
            # （旧做法会在启动时闪一个橙色方块，看着像界面出错了）
            if _rp.key_transparency_available():
                return
            self.ripple.set_mode("flash")
            self.ripple_mode_var.set("闪烁（兼容模式）")
            _rp.save_ripple_mode("flash")
            self.status_var.set("涟漪已自动切换为「闪烁（兼容模式）」")
        except Exception:
            pass

    def _apply_ripple_setting(self) -> None:
        try:
            # enabled 只表示「这台机器能显示涟漪」；录制、回放各用自己的开关
            play = getattr(self, "play_ripple_var", None)
            on = bool(self.ripple_var.get()) or (play is None or bool(play.get()))
            self.ripple.enabled = self.ripple.available() and on
            if hasattr(self, "ripple_mode_var"):
                flash = "兼容" in self.ripple_mode_var.get()
                self.ripple.set_mode("flash" if flash else None)
                import ui.ripple as _rp
                _rp.save_ripple_mode("flash" if flash else "key")
        except (AttributeError, tk.TclError):
            pass

    def _build_statusbar(self) -> None:
        status = ttk.Frame(self, padding=(10, 6))
        status.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        # 热键提示（启动后由 _register_hotkeys 填充实际绑定）
        self.hotkey_var = tk.StringVar(value=HOTKEY_HINT)
        ttk.Label(status, textvariable=self.hotkey_var,
                  foreground="#666").pack(side="right", padx=(10, 0))
        ttk.Button(status, text="热键设置", width=9,
                   command=self.show_hotkey_settings).pack(side="right")

        # DPI 状态（定位不准时用户第一眼能看到）
        try:
            from core.dpi import diagnostic_report
            dpi_text = diagnostic_report()
        except Exception:
            dpi_text = ""
        if dpi_text:
            self.dpi_var = tk.StringVar(value=dpi_text)
            ttk.Label(status, textvariable=self.dpi_var,
                      foreground="#777").pack(side="left", padx=(12, 0))

    def show_hotkey_settings(self) -> None:
        """热键设置与状态对话框"""
        win = tk.Toplevel(self)
        win.title("全局热键设置")
        win.transient(self)
        win.resizable(False, False)

        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="热键在程序最小化或后台时依然生效。",
                  foreground="#555").grid(row=0, column=0, columnspan=3,
                                          sticky="w", pady=(0, 8))
        vars_: dict[str, tk.StringVar] = {}
        keys = self.hotkeys.available_keys()
        for i, act in enumerate(("stop_record", "abort_play", "toggle_run"), start=1):
            ttk.Label(frm, text=HOTKEY_LABELS[act]).grid(row=i, column=0,
                                                        sticky="w", pady=3)
            v = tk.StringVar(value=self.hotkeys.binding_of(act).upper())
            vars_[act] = v
            ttk.Combobox(frm, textvariable=v, width=12, state="readonly",
                         values=[k.upper() for k in keys]).grid(
                row=i, column=1, padx=6, pady=3)

        status = ttk.Label(frm, text=self.hotkeys.status_text(),
                           foreground="#2e7d32" if self.hotkeys.running else "#c62828")
        status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 6))

        def apply():
            ok_all = True
            for act, v in vars_.items():
                if not self.hotkeys.set_binding(act, v.get().lower()):
                    ok_all = False
            if not ok_all:
                messagebox.showwarning("热键冲突",
                                       "存在重复绑定的热键，或该键不受支持。",
                                       parent=win)
                return
            self._update_hotkey_hint()
            status.configure(text=self.hotkeys.status_text())
            messagebox.showinfo("已应用", "热键设置已生效。", parent=win)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="应用", width=8, command=apply).pack(side="left", padx=4)
        ttk.Button(btns, text="关闭", width=8,
                   command=win.destroy).pack(side="left")

    def _update_hotkey_hint(self) -> None:
        if self.hotkeys.running:
            self.hotkey_var.set(self.hotkeys.status_text())
        else:
            self.hotkey_var.set(self.hotkeys.last_error or "全局热键不可用")

    # ============================================================ 坐标校准
    def show_calibration(self) -> None:
        """打开坐标校准对话框（解决鼠标定位偏移）"""
        from ui.calibrate import CalibrationDialog

        def applied():
            self._set_status(f"坐标校准已生效：缩放 {self.script.coord_scale}，"
                             f"偏移 ({self.script.coord_offset_x}, "
                             f"{self.script.coord_offset_y})")

        CalibrationDialog(self, self.script, on_apply=applied)

    # ============================================================ 录制
    def start_recording(self) -> None:
        if self.recorder.running or self.player.running:
            return
        mode = self._ask_record_mode()
        if mode is None:                       # 用户取消
            return
        self._record_mode = mode
        self.target_window, _ = get_active_window()
        self.update_idletasks()

        # 先把迷你条摆出来，再算排除区——这样条自身不会录进脚本
        self._show_hud("recording")

        if HUD_ONLY_RECORD and self.hud is not None:
            # 缩成小条：主窗口隐藏，屏幕上只留迷你条
            self.withdraw()
        else:
            self.iconify()

        self.after(250, self._really_start_recording)

    def _ask_record_mode(self) -> str | None:
        """询问「清空重录 / 追加到末尾」。返回 replace / append / None(取消)"""
        if not self.script.events:
            return "replace"
        pref = str(self.cfg.get("record_mode", "ask"))
        if pref in ("replace", "append"):
            return pref
        dlg = _RecordModeDialog(self, len(self.script.events))
        self.wait_window(dlg)
        if dlg.result is None:
            return None
        if dlg.remember:
            self.cfg["record_mode"] = dlg.result
            save_config({"record_mode": dlg.result})
        return dlg.result

    def _backup_before_append(self) -> str:
        """追加录制前把现有脚本备份一份，防止录砸了不可挽回"""
        try:
            d = app_dir() / "backup"
            d.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d_%H%M%S")
            path = d / f"{name}_追加录制前.json"
            self.script.save(str(path))
            return str(path)
        except Exception:                      # noqa: BLE001
            return ""

    def _really_start_recording(self) -> None:
        self._apply_moves_setting()
        self._snapshot()                       # 整段录制算一步撤销
        # 录制时清掉筛选，否则新录的行会被过滤条件挡住
        if self._filter_text:
            self.filter_var.set("")
            self._filter_text = ""
        if self._record_mode == "append" and self.script.events:
            self._record_offset = self.script.duration_ms + 300
            self._backup_before_append()
        else:
            self.script.events.clear()
            self._record_offset = 0
        self._last_row_count = len(self.script.events)
        self._refresh_list()

        # 录制排除区 = 主窗口矩形（若已隐藏则不算） + 迷你条矩形
        rects = []
        if self.state() != "withdrawn":
            rects.append((self.winfo_rootx(), self.winfo_rooty(),
                          self.winfo_width(), self.winfo_height()))
        if self.hud is not None:
            rects.append(self.hud.geometry_rect())
        self.recorder.exclusion_rects = [r for r in rects if r[2] > 0 and r[3] > 0]

        self.recorder.start()
        self.btn_rec.configure(state="disabled", text="● 录制中…")
        self.btn_stop.configure(state="normal")
        self.btn_play.configure(state="disabled")
        self._set_status("录制中… 请操作目标窗口（F9 停止 · F8 暂停）")

        if self.hud is not None:
            self.hud.set_moves_before(self.recorder.moves_before_click)
            self.hud.set_state("recording")
            self.hud.set_paused(False)
        self._tick_recording()

    def _tick_recording(self) -> None:
        if not self.recorder.running:
            return
        n, clicks, ms = self.recorder.snapshot()
        self._set_status(f"录制中… 已记录 {n} 条事件（F9 停止）")
        if self.hud is not None:
            self.hud.update_stats(n, clicks, ms)
        self.after(250, self._tick_recording)

    def _on_record_event(self, ev: Event) -> None:
        """实时预览：录制中每产生一条事件就追加到列表"""
        if not self.recorder.running:
            return
        if getattr(self, "_record_offset", 0):
            ev.t += self._record_offset          # 追加录制：整体接在已有末尾之后
        i = len(self.script.events)
        self.script.events.append(ev)
        self._insert_event_row(i, ev)
        self.tree.yview_moveto(1.0)

    def _ripple_at(self, x: int, y: int, button: str = "left") -> None:
        """在屏幕坐标画涟漪（录制中由 on_press 回调触发）"""
        if not self.recorder.running or self.recorder.paused:
            return
        if not bool(self.ripple_var.get()):
            return
        try:
            self.ripple.show(x, y, button or "left")
        except Exception:
            pass

    def _ripple_play(self, x: int, y: int, button: str = "left") -> None:
        """回放时的涟漪（player.on_action → 主线程）"""
        try:
            if not bool(self.play_ripple_var.get()) or not self.ripple.enabled:
                return
            self.ripple.show(x, y, button or "left")
        except Exception:
            pass

    # ============================================================ 迷你控制条
    def _ensure_hud(self):
        """惰性创建迷你条（首次录制 / 回放时才建，启动时不闪）"""
        if self.hud is not None:
            return self.hud
        from ui.hud import MiniBar
        self.hud = MiniBar(
            self,
            on_stop=self._hud_stop,
            on_pause=self._hud_pause,
            on_play=self._hud_play,
            on_restore=self._hud_restore,
            on_save=self._hud_save,
            on_discard=self._hud_discard,
            on_wait=self._hud_insert_wait,
            on_moves_change=self._hud_moves_change,
        )
        return self.hud

    def _show_hud(self, state: str) -> None:
        hud = self._ensure_hud()
        hud.set_moves_before(self.recorder.moves_before_click)
        hud.show(state, events=0, clicks=0, elapsed_ms=0)

    def _hide_hud(self) -> None:
        if self.hud is not None:
            try:
                self.hud.hide()
            except Exception:
                pass

    # ------------------------------------------------------------ HUD 回调
    def _hud_stop(self) -> None:
        """迷你条「停止」"""
        if self.recorder.running:
            self.stop_recording()
        elif self.player.running:
            self.abort_play()

    def _hud_pause(self) -> None:
        """迷你条「暂停 / 继续」"""
        if not self.recorder.running:
            return
        paused = self.recorder.toggle_pause()
        if self.hud is not None:
            self.hud.set_paused(paused)
        n, clicks, ms = self.recorder.snapshot()
        self._set_status("录制已暂停（F8 继续）" if paused else "录制继续中…")

    def toggle_pause_recording(self) -> None:
        """F8：暂停 / 继续录制"""
        if not self.recorder.running:
            return
        paused = self.recorder.toggle_pause()
        if self.hud is not None:
            self.hud.set_paused(paused)
        self._set_status("录制已暂停（F8 继续）" if paused else "录制继续中…")

    def _hud_play(self) -> None:
        """迷你条「回放」（空闲态）"""
        if self.recorder.running or self.player.running:
            return
        self._hide_hud()
        self.deiconify()
        self.lift()
        self.start_play()

    def _hud_restore(self) -> None:
        """迷你条「回到主窗口」：有录制就停止并回主界面"""
        if self.recorder.running:
            self.stop_recording()
        else:
            self._hide_hud()
            self.deiconify()
            self.lift()
            self.focus_force()

    def _hud_save(self) -> None:
        """迷你条「保存到脚本库」（录制中则先停止）"""
        if self.recorder.running:
            self.stop_recording()
        self.after(120, self.save_script)

    def _hud_discard(self) -> None:
        """迷你条「放弃本次录制」"""
        from tkinter import messagebox as mb
        n = len(self.recorder.events)
        if n and not mb.askyesno("放弃录制", f"确认丢弃这 {n} 条已录制的事件？"):
            return
        if self.recorder.running:
            self.recorder.stop()
        self.recorder.events.clear()
        self.script.events.clear()
        self._hide_hud()
        self.deiconify()
        self.lift()
        self.btn_rec.configure(state="normal", text="● 开始录制")
        self.btn_stop.configure(state="disabled")
        self.btn_play.configure(state="normal")
        self._refresh_list()
        self._set_status("已放弃本次录制")

    def _hud_insert_wait(self) -> None:
        """迷你条「插入等待 1 秒」（录制中则先停止再插入）"""
        if self.recorder.running:
            self.stop_recording()
        self.after(150, lambda: self._insert_wait_event(1000))

    def _insert_wait_event(self, ms: int) -> None:
        from core.script_model import Event as _E
        last_t = self.script.events[-1].t if self.script.events else 0
        ev = _E(t=last_t, type="wait", ms=ms, valid=True)
        self.script.events.append(ev)
        self._refresh_list()
        self._set_status(f"已插入等待 {ms} 毫秒")

    def _hud_moves_change(self, n: int) -> None:
        """迷你条滚轮 / 滑杆调整「保留移动点」"""
        self.recorder.moves_before_click = max(0, min(10, int(n)))
        try:
            self.moves_var.set(str(self.recorder.moves_before_click))
        except Exception:
            pass

    # ------------------------------------------------------------ 回放时同步 HUD
    def _hud_sync_play(self, lap: int, total: int, step: int, steps: int) -> None:
        if self.hud is not None and self.hud.winfo_exists():
            self.hud.update_play(lap, total, step, steps)


    def stop_recording(self) -> None:
        if not self.recorder.running:
            self._hide_hud()
            self.deiconify()
            return
        before = len(self.recorder.events)
        self.recorder.stop()
        self.script.events = list(self.recorder.events)
        after = len(self.script.events)
        self._hide_hud()
        self.deiconify()
        self.lift()
        self.btn_rec.configure(state="normal", text="● 开始录制")
        self.btn_stop.configure(state="disabled")
        self.btn_play.configure(state="normal")
        self._refresh_list()
        s = self.script
        trimmed = before - after
        extra = f"，已清理 {trimmed} 条冗余移动" if trimmed > 0 else ""
        self._set_status(f"录制完成：{len(s.events)} 条事件{extra}，"
                         f"{s.click_count} 次点击，时长 {s.duration_ms/1000:.1f} 秒")
        if not s.events:
            messagebox.showinfo(
                "提示", "没有录制到任何事件。\n\n"
                "请确认：\n· 已把鼠标移出本工具窗口再操作\n"
                "· macOS 已授予「辅助功能」权限")

    # ============================================================ 回放
    def _sync_settings(self) -> bool:
        """把界面设置写回 script；失败返回 False"""
        try:
            self.script.speed = float(self.speed_var.get())
            self.script.loop = max(1, int(self.loop_var.get()))
            self.script.jitter_ms = max(0, int(self.jitter_var.get()))
            self.script.retry = max(0, int(self.retry_var.get()))
        except ValueError:
            messagebox.showerror("参数错误", "速度/循环/抖动/重试必须是数字。")
            return False
        self.script.name = self.name_var.get().strip() or "未命名脚本"
        # 数据源
        self.script.data_file = self.data_var.get().strip()
        self.script.data_mode = self.data_mode_var.get() or "off"
        try:
            self.script.data_start_row = max(2, int(self.data_start_var.get()))
        except ValueError:
            self.script.data_start_row = 2
        return True

    def _pick_data_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择数据文件（Excel/CSV）",
            filetypes=[("数据文件", "*.xlsx *.xlsm *.xls *.csv *.tsv"),
                       ("所有文件", "*.*")])
        if path:
            self.data_var.set(path)

    def _refresh_data_hint(self) -> None:
        """显示数据文件概要：列名 + 行数，提示 {{列名}} 用法"""
        path = self.data_var.get().strip()
        if not path:
            self.data_hint.set(
                "未启用。在「输入文本」里写 {{列名}}，回放时自动替换成对应值。")
            return
        try:
            from core.data_source import load_table
            t = load_table(path)
            if not t.headers:
                self.data_hint.set("⚠ 文件为空")
                return
            cols = "、".join(t.headers[:8]) + ("…" if len(t.headers) > 8 else "")
            self.data_hint.set(f"✓ {t.n_rows} 行数据　列：{cols}\n"
                               f"变量：{{{{列名}}}}（随轮次换行）或 {{{{C5}}}}"
                               f"（固定取 C 列第 5 行）")
        except Exception as exc:
            self.data_hint.set(f"⚠ 读取失败：{exc}")

    def data_row_count(self) -> int:
        try:
            from core.data_source import load_table
            return load_table(self.data_var.get().strip()).n_rows
        except Exception:
            return 0

    def start_play(self, hotkey_mode: bool = False) -> None:
        """回放入口：先倒计时几秒，给用户时间切到目标窗口"""
        if self.player.running or self.recorder.running:
            return
        if not self.script.active_events:
            messagebox.showinfo("提示", "请先录制或打开一份脚本。")
            return
        if self.cancel_countdown():            # 倒计时中再点一次 → 取消
            return
        if self._countdown_seconds() <= 0:
            self._play_now(hotkey_mode)
            return
        self._start_countdown(lambda: self._play_now(hotkey_mode))

    def _play_now(self, hotkey_mode: bool = False) -> None:
        if self.player.running or self.recorder.running:
            return
        if not self.script.active_events:
            messagebox.showinfo("提示", "请先录制或打开一份脚本。")
            return
        if not self._sync_settings():
            return

        warns = self.script.validate_structure()
        if warns:
            messagebox.showwarning("脚本结构问题", "\n".join(warns))

        if not self._check_image_steps():
            return

        # 数据源校验：有 {{变量}} 但未启用 / 文件读不了 / 模式不符
        from core.data_source import placeholders_in
        used_vars = {p for ev in self.script.active_events
                     if ev.type == "keytype"
                     for p in placeholders_in(ev.text)}
        if used_vars and not self.script.use_data():
            if not messagebox.askyesno(
                    "未启用数据源",
                    "脚本里有输入文本用了变量：" + "、".join(sorted(used_vars))
                    + "\n\n当前未启用数据源，回放时将原样输入 {{...}}。"
                    + "\n\n是否仍然继续回放？"):
                return
        if self.script.use_data():
            try:
                self.script.values_for_lap(1)
            except Exception as exc:
                messagebox.showerror("数据源错误", f"无法读取数据文件：\n{exc}")
                return

        # 热键模式下不最小化窗口（用户可能正在别的软件里，
        # 强行 iconify 会打断他的操作）
        self._hotkey_mode = hotkey_mode
        if not hotkey_mode:
            self._show_hud("playback")
            if HUD_ONLY_RECORD:
                self.withdraw()
            else:
                self.iconify()
        self.after(300 if not hotkey_mode else 60, self._really_start_play)

    def _really_start_play(self) -> None:
        self.btn_play.configure(state="disabled", text="▶ 回放中…")
        self.btn_rec.configure(state="disabled")
        self.btn_abort.configure(state="normal")
        self._set_status("回放中…（F10 中断）")
        self.clear_log()

        # 每次回放一份新日志：实时刷到面板 + 落盘 JSONL
        self._run_logger = RunLogger(
            script_name=self.script.name,
            on_entry=lambda e: self.after(0, self._append_log, e),
        )

        focus = None
        if self.focus_var.get() and self.target_window:
            hwnd = self.target_window
            focus = lambda: focus_window(hwnd)          # noqa: E731

        self.player.play(
            self.script,
            on_event=lambda ev, lap: self.after(0, self._on_play_event, ev, lap),
            on_progress=lambda lap, i, n: self.after(0, self._on_progress, lap, i, n),
            on_done=lambda ok, msg: self.after(0, self._on_play_done, ok, msg),
            focus_window=focus,
            logger=self._run_logger,
        )

    def _image_steps(self) -> list[tuple[int, str]]:
        """脚本里的找图步骤：[(行号(1起), 图片路径)]，跳过已禁用的"""
        out: list[tuple[int, str]] = []
        for i, e in enumerate(self.script.events):
            if getattr(e, "enabled", True) is False:
                continue
            if e.type in ("find_image", "if_image") and e.text:
                out.append((i + 1, e.text))
            elif e.type == "if" and (e.cond or "") == "image" and e.left:
                out.append((i + 1, e.left))
        return out

    def _check_image_steps(self) -> bool:
        """脚本要用找图但本机跑不通时，提前说清楚；返回 False 表示用户取消"""
        steps = self._image_steps()
        if not steps:
            return True
        missing = [n for n, p in steps if not os.path.isfile(p)]
        engine_ok = self.player.image_engine_available()
        if engine_ok and not missing:
            return True

        lines = []
        if not engine_ok:
            lines.append("· 本机缺少找图组件（numpy / Pillow），"
                         f"{len(steps)} 个找图步骤都会被跳过")
        if missing:
            show = "、".join(str(n) for n in missing[:8])
            if len(missing) > 8:
                show += "…"
            lines.append(f"· 有 {len(missing)} 张图片文件找不到（第 {show} 步），"
                         "这些步骤会直接失败")
            if (self.script_path or "").lower().endswith(".ajjl"):
                lines.append("  多半是包里的附件被删了，重新导出一份 .ajjl 再试；"
                             "或让对方用 .ajjl 单文件包发你")

        msg = "\n".join(lines) + "\n\n"
        if not engine_ok:
            msg += ("这通常是安装包不完整，重新安装一次本程序即可；\n"
                    "用源码运行时执行：pip install numpy pillow\n\n")
        msg += "仍然继续回放吗？"
        return messagebox.askyesno("找图步骤可能跑不通", msg, parent=self)

    def _on_play_event(self, ev: Event, lap: int) -> None:
        try:
            idx = self.script.events.index(ev)
        except ValueError:
            return
        iid = str(idx)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _on_progress(self, lap: int, i: int, n: int) -> None:
        self._set_status(f"回放中… 第 {lap}/{self.script.loop} 轮  步骤 {i}/{n}（F10 中断）")
        self._hud_sync_play(lap, self.script.loop, i, n)

    def _on_play_done(self, ok: bool, msg: str) -> None:
        log_name = ""
        if self._run_logger:
            self._run_logger.close()
            if self._run_logger.log_path:
                log_name = os.path.basename(self._run_logger.log_path)
            self._run_logger = None

        self._hide_hud()
        self.deiconify()
        self.lift()
        self.btn_play.configure(state="normal", text="▶ 回放")
        self.btn_rec.configure(state="normal")
        self.btn_abort.configure(state="disabled")

        note = ""
        if not ok and msg != "已手动中断":
            note = self._locate_failure()
            if note:
                msg = f"{msg}\n\n出错位置：{note}"
        elif ok:
            miss = self._last_miss_text()
            if miss:
                note = miss

        parts = [msg.splitlines()[0]]
        if note:
            parts.append(note)
        if log_name:
            parts.append(f"日志 {log_name}")
        self._set_status("　·　".join(parts))
        if not ok and msg != "已手动中断":
            messagebox.showerror("回放失败", msg)

    def _last_miss_text(self) -> str:
        """回放正常结束但有步骤未命中时，给一句提示（不弹窗、不打扰）"""
        ev = getattr(self.player, "last_fail_event", None)
        step = getattr(self.player, "last_fail_step", 0)
        if ev is None or not step:
            return ""
        return f"有步骤未命中：第 {step} 步 {ev.detail()}（详见日志）"

    def _locate_failure(self) -> str:
        """回放出错后：跳到出错那一行、选中并标底色。返回定位说明。"""
        ev = getattr(self.player, "last_fail_event", None)
        if ev is None:
            return ""
        idx = -1
        for i, e in enumerate(self.script.events):
            if e is ev:                    # 用对象身份，避免内容相同的行串位
                idx = i
                break
        if idx < 0:
            return ""
        # 该行可能被搜索过滤或循环折叠挡住了，先恢复显示
        need_refresh = False
        if self._filter_text:
            self.filter_var.set("")
            self._filter_text = ""
            need_refresh = True
        if idx in self._hidden_rows():
            self._folded.clear()
            need_refresh = True
        if need_refresh:
            self._refresh_list()
        iid = str(idx)
        if not self.tree.exists(iid):
            return f"第 {idx + 1} 步（当前列表未显示该行）"
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        try:
            tags = tuple(self.tree.item(iid, "tags")) + ("fail",)
            self.tree.item(iid, tags=tags)
        except Exception:                      # noqa: BLE001
            pass
        return f"第 {idx + 1} 步 {ev.detail()}"

    def abort_play(self) -> None:
        if self.cancel_countdown():            # 倒计时阶段：直接取消，不算中断
            return
        self.player.stop()
        self._set_status("正在中断…")

    # ============================================================ 列表
    def _refresh_list(self) -> None:
        """按当前筛选 / 折叠状态重建列表"""
        self.tree.delete(*self.tree.get_children())
        hidden = self._hidden_rows()
        pairs = self._loop_pairs()
        q = self._filter_text
        for i, ev in enumerate(self.script.events):
            if i in hidden:
                continue
            if q and not self._match(ev, q):
                continue
            self._insert_event_row(i, ev, pairs=pairs)
        self._update_stats()

    def _update_stats(self) -> None:
        s = self.script
        self.stat_var.set(
            f"事件数：{len(s.events)}    点击：{s.click_count}\n"
            f"总时长：{s.duration_ms/1000:.1f} 秒\n"
            f"创建时间：{s.created_at}"
        )

    def _selected_indices(self) -> list[int]:
        return sorted(int(i) for i in self.tree.selection())

    def _selected_index(self) -> int | None:
        sel = self._selected_indices()
        return sel[0] if sel else None

    def delete_selected(self) -> None:
        idxs = self._selected_indices()
        if not idxs:
            return
        self._snapshot()
        for i in reversed(idxs):
            self.script.remove(i)
        self._refresh_list()
        self._set_status(f"已删除 {len(idxs)} 条（Ctrl+Z 可撤销）")

    def move_selected(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        self._move_event(idx, idx + delta)

    def toggle_selected(self) -> None:
        idxs = self._selected_indices()
        if not idxs:
            return
        self._snapshot()
        # 以第一个的相反状态为准，批量设置
        target = not self.script.events[idxs[0]].enabled
        for i in idxs:
            self.script.events[i].enabled = target
        self._refresh_list()

    def edit_time(self) -> None:
        """修改某行的绝对时间：后面的步骤整体自动平移，间隔保持不变"""
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先在列表里选中一行。")
            return
        evs = self.script.events
        ev = evs[idx]
        floor = evs[idx - 1].t if idx > 0 else 0      # 不能早于上一行
        ms = simpledialog.askinteger(
            "修改时间",
            f"当前 {ev.t} ms（{ev.t / 1000:.2f} 秒）。\n"
            "输入新的时间（毫秒）：\n\n后面所有步骤会跟着自动平移，"
            "各自的间隔不变。",
            initialvalue=ev.t, minvalue=floor, parent=self)
        if ms is None or ms == ev.t:
            return
        self._snapshot()
        delta = ms - ev.t
        for e in evs[idx:]:
            e.t += delta
        self._refresh_list()
        self.tree.see(str(idx))
        self.tree.selection_set(str(idx))
        self._set_status(f"第 {idx + 1} 行时间改为 {ms} ms，后面步骤已同步平移")

    # ---------------------------------------------------- 间隔时间
    def _gap_of(self, idx: int) -> int:
        """第 idx 行与上一行的间隔（毫秒）；首行相对开始（0）"""
        evs = self.script.events
        if not (0 <= idx < len(evs)):
            return 0
        prev_t = evs[idx - 1].t if idx > 0 else 0
        return max(0, evs[idx].t - prev_t)

    def edit_gap(self) -> None:
        """修改选中行的间隔：该行及其后所有步骤整体平移，后续间隔不变"""
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先在列表里选中一行。")
            return
        evs = self.script.events
        old = self._gap_of(idx)
        ms = simpledialog.askinteger(
            "修改间隔",
            f"当前间隔 {old} ms（{old / 1000:.2f} 秒）。\n"
            "输入新的间隔（毫秒，0 = 与上一步同时）：\n\n"
            "本行之后的所有步骤会跟着整体平移，后面的间隔不受影响。",
            initialvalue=old, minvalue=0, parent=self)
        if ms is None or ms == old:
            return
        self._snapshot()
        delta = ms - old
        for e in evs[idx:]:
            e.t += delta
        self.script.retime()
        self._refresh_list()
        self.tree.see(str(idx))
        self.tree.selection_set(str(idx))
        self._set_status(f"第 {idx + 1} 行间隔已设为 {ms} ms（{ms / 1000:.2f} 秒）")

    def set_gap_batch(self) -> None:
        """统一设置间隔：作用于选中行（未选则全部）"""
        evs = self.script.events
        if not evs:
            return
        idxs = self._selected_indices() or list(range(len(evs)))
        ms = simpledialog.askinteger(
            "统一间隔",
            f"把选中的 {len(idxs)} 行的间隔统一设为（毫秒）：\n"
            "选中的行会用这个间隔，未选中的行保持原间隔。",
            initialvalue=self._gap_of(idxs[0]), minvalue=0, parent=self)
        if ms is None:
            return
        self._snapshot()
        gaps = [self._gap_of(i) for i in range(len(evs))]
        for i in idxs:
            gaps[i] = ms
        t = gaps[0]
        evs[0].t = t
        for i in range(1, len(evs)):
            t += gaps[i]
            evs[i].t = t
        self._refresh_list()
        self._set_status(f"已把 {len(idxs)} 行的间隔设为 {ms} ms（{ms / 1000:.2f} 秒）")

    def add_comment(self) -> None:
        text = simpledialog.askstring("添加注释", "注释内容：", parent=self)
        if not text:
            return
        self._snapshot()
        idx = self._selected_index()
        t = self.script.events[idx].t if idx is not None else self.script.duration_ms
        new_i = self.script.insert_sorted(Event(t=t, type="comment", text=text))
        self._refresh_list()
        self.tree.selection_set(str(new_i))

    def _on_tree_double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        elif not self.tree.selection():
            return
        col = self.tree.identify_column(event.x)       # #1 序号 #2 时间 #3 间隔 #4 动作
        if col == "#1":
            # 双击序号列：折叠 / 展开该行所属的循环
            self.toggle_fold()
            return
        if col == "#2":
            self.edit_time()
            return
        if col == "#3":
            self.edit_gap()
            return
        idx = self._selected_index()
        if idx is not None:
            self._with_undo(self._edit_event_at, idx)

    def _on_tree_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        idx = self._selected_index()

        menu = tk.Menu(self, tearoff=0)

        menu.add_command(label="↶ 撤销 (Ctrl+Z)", command=self.undo)
        menu.add_command(label="↷ 重做 (Ctrl+Y)", command=self.redo)
        menu.add_separator()

        # ---- 编辑当前事件 ----
        if idx is not None:
            menu.add_command(label="✏ 编辑事件…", command=self.edit_selected_event)

        # ---- 在此处插入指令 ----
        insert_menu = self._build_insert_menu(idx)
        menu.add_cascade(
            label="➕ 在此处插入指令"
                  + (f"（第 {idx + 1} 行前）" if idx is not None else "（末尾）"),
            menu=insert_menu)

        menu.add_separator()
        menu.add_command(label="启用/禁用", command=self.toggle_selected)
        menu.add_command(label="删除", command=self.delete_selected)
        menu.add_separator()
        menu.add_command(label="上移", command=lambda: self.move_selected(-1))
        menu.add_command(label="下移", command=lambda: self.move_selected(1))
        menu.add_command(label="修改时间", command=self.edit_time)
        menu.add_command(label="修改间隔", command=self.edit_gap)
        menu.add_command(label="统一设置间隔…", command=self.set_gap_batch)
        menu.add_command(label="加注释", command=self.add_comment)
        menu.add_separator()
        menu.add_command(label="⊟ 折叠/展开循环（双击序号同效）",
                         command=self.toggle_fold)
        menu.tk_popup(event.x_root, event.y_root)

    def _build_insert_menu(self, idx: int | None) -> tk.Menu:
        """构造「在此处插入指令」子菜单（分组级联，与右侧指令库一致）"""
        m = tk.Menu(self, tearoff=0)
        pos = idx if idx is not None else len(self.script.events)
        for title, items in self._command_defs():
            sub = tk.Menu(m, tearoff=0)
            for label, cmd in items:
                sub.add_command(
                    label=label,
                    command=lambda c=cmd, p=pos: self._insert_at(p, c))
            m.add_cascade(label=title, menu=sub)
        return m

    def _insert_at(self, pos: int, factory) -> None:
        """把「插入位置」暂存后调用指令工厂；_append 会消费并清掉"""
        if self.recorder.running or self.player.running:
            messagebox.showinfo("提示", "录制 / 回放进行中，无法插入指令。")
            return
        self._insert_index = pos
        try:
            factory()
        finally:
            # 工厂内部若因输入取消而未走到 _append（如 askstring 返回空），
            # 这里兜底清理，避免影响下一次「插入到末尾」
            pass

    def edit_selected_event(self) -> None:
        """右键「编辑事件」：按事件类型弹出对应编辑框"""
        idx = self._selected_index()
        if idx is not None:
            self._with_undo(self._edit_event_at, idx)

    def _edit_event_at(self, idx: int) -> None:
        # 把双击编辑逻辑抽出来共用（原 _on_tree_double_click 的主体）
        ev = self.script.events[idx]
        if ev.type in ("keytype", "comment"):
            val = simpledialog.askstring(
                "编辑内容", "内容：", initialvalue=ev.text or "", parent=self)
            if val is not None:
                ev.text = val
                self._refresh_list()
        elif ev.type == "hotkey":
            val = simpledialog.askstring(
                "编辑组合键", "用 + 连接，如 ctrl+shift+s：",
                initialvalue="+".join(ev.keys or []), parent=self)
            if val:
                ev.keys = [k.strip().lower() for k in val.split("+") if k.strip()]
                self._refresh_list()
        elif ev.type == "delay":
            ms = simpledialog.askinteger("编辑等待", "毫秒：",
                                         initialvalue=ev.dx or 0,
                                         minvalue=0, parent=self)
            if ms is not None:
                ev.dx = ms
                self._refresh_list()
        elif ev.type in ("click", "dblclick", "mousedown", "mouseup"):
            val = simpledialog.askstring(
                "编辑坐标", "格式 x,y：",
                initialvalue=f"{ev.x},{ev.y}", parent=self)
            if val and "," in val:
                try:
                    x, y = [int(v.strip()) for v in val.split(",", 1)]
                    ev.x, ev.y = x, y
                    self._refresh_list()
                except ValueError:
                    messagebox.showerror("格式错误", "请输入「x,y」形式，如 300,500")
        elif ev.type == "drag":
            val = simpledialog.askstring(
                "编辑拖拽", "格式 x1,y1,x2,y2：",
                initialvalue=f"{ev.x},{ev.y},{ev.x2},{ev.y2}", parent=self)
            if val and val.count(",") == 3:
                try:
                    x1, y1, x2, y2 = [int(v.strip()) for v in val.split(",")]
                    ev.x, ev.y, ev.x2, ev.y2 = x1, y1, x2, y2
                    self._refresh_list()
                except ValueError:
                    messagebox.showerror("格式错误", "请输入「x1,y1,x2,y2」形式")
        elif ev.type == "edit":
            from core.script_model import EDIT_ACTIONS as _EA
            choices = sorted(_EA.keys())
            dialog = _EditKeyDialog(self, choices, ev.key or "")
            self.wait_window(dialog)
            if dialog.result:
                ev.key = dialog.result
                self._refresh_list()
        elif ev.type == "loop_start":
            val = simpledialog.askstring(
                "编辑循环", "循环次数：\n· 数字 = 执行几次\n· 留空 = 无限\n"
                            "· data = 按数据行数",
                initialvalue="" if (ev.text or "") == "forever" else (ev.text or ""),
                parent=self)
            if val is not None:
                ev.text = val.strip() or "forever"
                self._refresh_list()
        elif ev.type == "if":
            self._edit_if_event(ev)
        elif ev.type in ("else", "if_end", "loop_end"):
            messagebox.showinfo(
                "提示", f"「{ev.detail()}」是结构标记，没有可编辑的属性。\n\n"
                        "如需调整，请删除后重新插入（右键 → 删除）。")
        elif ev.type == "break":
            messagebox.showinfo(
                "提示", "「跳出循环」不需要参数，执行到这里会立即结束最内层循环。")
        elif ev.type == "find_image":
            val = simpledialog.askstring(
                "编辑找图", "相似度 50-100：",
                initialvalue=str(ev.dx or 90), parent=self)
            if val and val.strip().isdigit():
                ev.dx = max(50, min(100, int(val.strip())))
            region, ok = self._ask_find_region(ev.region())
            if ok:
                ev.set_region(region)
            self._refresh_list()

        elif ev.type == "ocr":
            if messagebox.askyesno(
                    "编辑验证码识别",
                    "要重新框选验证码区域吗？\n\n"
                    "选「是」：重新取点（2 点框区域，可选第 3 点点输入框）\n"
                    "选「否」：只改 OCR 服务地址", parent=self):
                self._pick_for_existing(ev)
                return
            ep = simpledialog.askstring(
                "OCR 服务地址",
                "留空 = 默认 127.0.0.1:1224\n"
                "也可以只填 127.0.0.1:端口",
                initialvalue=ev.text or "", parent=self)
            if ep is not None:
                ev.text = ep.strip()
            self._refresh_list()
        else:
            messagebox.showinfo("提示",
                                f"{ev.detail()}\n\n该类型暂无专门编辑器，"
                                f"可双击时间列改时间或删除后重录。")


    def _edit_if_event(self, ev: Event) -> None:
        """编辑「如果」条件（图像 / 变量）"""
        if (ev.cond or "var") == "image":
            res = ask_image_condition(
                self, self._known_images(), current=ev.left or "",
                hit=(ev.op != "img_miss"))
            if res is None:
                return
            path, hit = res
            ev.op = "img_hit" if hit else "img_miss"
            if path == GRAB_ITEM:
                def build(rect, p):
                    ev.left = p
                    self._refresh_list()
                    self._set_status(f"已更新：{ev.detail()}")
                    return None

                self._grab_and_fill(build)
                self._refresh_list()
                return
            ev.left = path
            self._refresh_list()
            self._set_status(f"已更新：{ev.detail()}{self._quality_note(path)}")
            return

        left = simpledialog.askstring("编辑条件", "左边：",
                                      initialvalue=ev.left or "", parent=self)
        if left is None:
            return
        cur_op = {v: k for k, v in
                  {"=": "eq", "!=": "ne", "包含": "contains",
                   ">": "gt", "<": "lt"}.items()}.get(ev.op or "eq", "=")
        op_raw = simpledialog.askstring("比较方式", "= != 包含 > <：",
                                        initialvalue=cur_op, parent=self)
        if op_raw is None:
            return
        op_map = {"=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
                  "包含": "contains", ">": "gt", "<": "lt"}
        right = simpledialog.askstring("比较值", "右边：",
                                       initialvalue=ev.right or "", parent=self)
        if right is None:
            return
        ev.cond = "var"
        ev.left = left.strip()
        ev.op = op_map.get(op_raw.strip(), "eq")
        ev.right = right.strip()
        self._refresh_list()

    # ============================================================ 指令插入
    def _append(self, ev: Event) -> int:
        """插入一条事件。

        若 self._insert_index 不为 None（右键「在此处插入」），插到该位置；
        否则追加到末尾。用完即清。
        """
        idx = self._insert_index
        self._insert_index = None
        self._snapshot()
        if idx is None:
            ev.t = self.script.duration_ms + 100 if self.script.events else 0
            i = self.script.insert_sorted(ev)
        else:
            idx = max(0, min(int(idx), len(self.script.events)))
            prev_t = self.script.events[idx - 1].t if idx > 0 else 0
            if idx < len(self.script.events):
                # 不越过插入点后那条事件的时间，保持整体单调
                ev.t = min(prev_t + 1, self.script.events[idx].t)
            else:
                ev.t = prev_t + 1
            self.script.events.insert(idx, ev)
            i = idx
        self._refresh_list()
        self.tree.see(str(i))
        self.tree.selection_set(str(i))
        return i

    def _insert_edit(self, action: str) -> None:
        label = EDIT_ACTIONS[action]["label"]
        if action == "right_click":
            self._cmd_click_right()
            return
        self._append(Event(t=0, type="edit", key=action))
        self._set_status(f"已插入【{label}】")

    # ============================================================ 脚本库
    def _build_library(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="📚 脚本库",
                  font=("", 10, "bold")).pack(anchor="w", pady=(2, 4))

        # 搜索框
        search = ttk.Frame(parent)
        search.pack(fill="x", pady=(0, 4))
        self.lib_search_var = tk.StringVar()
        ent = ttk.Entry(search, textvariable=self.lib_search_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<KeyRelease>", lambda e: self._refresh_library())
        ttk.Button(search, text="✕", width=2,
                   command=self._lib_clear_search).pack(side="left", padx=(2, 0))

        # 排序
        sort_row = ttk.Frame(parent)
        sort_row.pack(fill="x", pady=(0, 4))
        self.lib_sort_var = tk.StringVar(value="最近使用")
        cb = ttk.Combobox(sort_row, textvariable=self.lib_sort_var,
                          state="readonly", width=10,
                          values=("最近使用", "名称", "创建时间"))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_library())
        self.lib_count_var = tk.StringVar(value="")
        ttk.Label(sort_row, textvariable=self.lib_count_var,
                  foreground="#777").pack(side="right")

        # 列表
        list_box = ttk.Frame(parent)
        list_box.pack(fill="both", expand=True)
        self.lib_tree = ttk.Treeview(list_box, columns=("name",),
                                     displaycolumns=(), show="tree",
                                     selectmode="browse")
        self.lib_tree.column("#0", width=300, stretch=True)
        vsb = ttk.Scrollbar(list_box, orient="vertical",
                            command=self.lib_tree.yview)
        self.lib_tree.configure(yscrollcommand=vsb.set)
        self.lib_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        # 列宽跟随面板宽度：面板变宽时脚本名显示更多，不留空白
        def _fit_columns(_evt=None):
            try:
                w = self.lib_tree.winfo_width()
            except Exception:
                return
            if w > 60:
                self.lib_tree.column("#0", width=w - 4)
        self.lib_tree.bind("<Configure>", _fit_columns)

        self.lib_tree.bind("<<TreeviewSelect>>", self._on_lib_select)
        self.lib_tree.bind("<Double-1>", lambda e: self.lib_open())
        self.lib_tree.bind("<Button-3>", self._on_lib_right_click)

        # 信息条
        self.lib_info_var = tk.StringVar(value="选中脚本查看详情")
        self.lib_info_lbl = ttk.Label(parent, textvariable=self.lib_info_var,
                                      foreground="#555", justify="left",
                                      wraplength=310)
        self.lib_info_lbl.pack(anchor="w", pady=(4, 2))

        # 换行宽度跟随面板，避免文字被截断或过早折行
        def _fit_wrap(evt):
            try:
                self.lib_info_lbl.configure(
                    wraplength=max(120, evt.width - 12))
            except Exception:
                pass
        parent.bind("<Configure>", _fit_wrap)

        # 操作按钮
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=(2, 0))
        ttk.Button(row1, text="打开", width=6,
                   command=self.lib_open).pack(side="left", padx=(0, 3))
        ttk.Button(row1, text="保存", width=6,
                   command=self.lib_save).pack(side="left", padx=(0, 3))
        ttk.Button(row1, text="另存", width=6,
                   command=self.lib_save_as).pack(side="left")

        row2 = ttk.Frame(parent)
        row2.pack(fill="x", pady=(3, 0))
        ttk.Button(row2, text="重命名", width=6,
                   command=self.lib_rename).pack(side="left", padx=(0, 3))
        ttk.Button(row2, text="复制", width=6,
                   command=self.lib_duplicate).pack(side="left", padx=(0, 3))
        ttk.Button(row2, text="删除", width=6,
                   command=self.lib_delete).pack(side="left")

        row3 = ttk.Frame(parent)
        row3.pack(fill="x", pady=(3, 0))
        ttk.Button(row3, text="导入", width=6,
                   command=self.lib_import).pack(side="left", padx=(0, 3))
        ttk.Button(row3, text="导出", width=6,
                   command=self.lib_export).pack(side="left", padx=(0, 3))
        ttk.Button(row3, text="目录", width=6,
                   command=self.open_lib_dir).pack(side="left")

        self._refresh_library()

    def _lib_clear_search(self) -> None:
        self.lib_search_var.set("")
        self._refresh_library()

    def _refresh_library(self) -> None:
        """重新填充脚本库列表"""
        if not hasattr(self, "lib_tree"):
            return
        sort_map = {"最近使用": "recent", "名称": "name", "创建时间": "created"}
        kw = self.lib_search_var.get().strip()
        items = self.library.list(kw, sort_map.get(self.lib_sort_var.get(), "recent"))

        self.lib_tree.delete(*self.lib_tree.get_children())
        for m in items:
            self.lib_tree.insert("", "end", iid=m.filename,
                                 text=f" {m.name}",
                                 values=(m.name,))
        self.lib_count_var.set(f"{len(items)} 个")
        # 保持当前选中
        if self.current_filename and self.lib_tree.exists(self.current_filename):
            self.lib_tree.selection_set(self.current_filename)
            self.lib_tree.see(self.current_filename)

    def _on_lib_select(self, _evt=None) -> None:
        sel = self.lib_tree.selection()
        if not sel:
            self.lib_info_var.set("选中脚本查看详情")
            return
        meta = self.library.get(sel[0])
        if not meta:
            return
        self.lib_info_var.set(
            f"{meta.event_count} 步 · {meta.duration_ms/1000:.1f}s\n"
            f"使用 {meta.use_count} 次\n"
            f"改于 {meta.modified_at or '—'}")

    def _on_lib_right_click(self, evt) -> None:
        row = self.lib_tree.identify_row(evt.y)
        if row:
            self.lib_tree.selection_set(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开到编辑器", command=self.lib_open)
        menu.add_command(label="覆盖保存", command=self.lib_save)
        menu.add_command(label="另存为", command=self.lib_save_as)
        menu.add_separator()
        menu.add_command(label="重命名", command=self.lib_rename)
        menu.add_command(label="复制一份", command=self.lib_duplicate)
        menu.add_command(label="导出到文件…", command=self.lib_export)
        menu.add_separator()
        menu.add_command(label="删除", command=self.lib_delete)
        menu.tk_popup(evt.x_root, evt.y_root)

    def _lib_selected(self) -> str | None:
        sel = self.lib_tree.selection() if hasattr(self, "lib_tree") else ()
        return sel[0] if sel else None

    def lib_open(self) -> None:
        fname = self._lib_selected()
        if not fname:
            messagebox.showinfo("提示", "请先在脚本库中选中一个脚本。")
            return
        if self.player.running or self.recorder.running:
            return
        if not self._confirm_discard("打开脚本库里的脚本"):
            return
        script = self.library.load(fname)
        if script is None:
            messagebox.showerror("打开失败", "脚本文件已损坏或已被移动。")
            self._refresh_library()
            return
        self._apply_script(script, fname)
        self._set_status(f"已从脚本库打开「{script.name}」")

    def _apply_script(self, script: Script, filename: str | None) -> None:
        """把脚本装载到界面（供库/文件两处复用）"""
        self.script = script
        self.current_filename = filename
        self.name_var.set(script.name)
        self.speed_var.set(str(script.speed))
        self.loop_var.set(str(script.loop))
        self.jitter_var.set(str(script.jitter_ms))
        self.retry_var.set(str(script.retry))
        # 换了脚本：撤销历史不再适用，清空并视为已保存
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._folded.clear()
        self._update_undo_ui()
        self._mark_clean()
        self._refresh_list()

    def lib_save(self) -> None:
        """保存：已关联库条目则覆盖，否则等同于另存"""
        if not self._sync_settings():
            return
        if not self.current_filename:
            self.lib_save_as()
            return
        self.library.save(self.script, self.current_filename)
        self._refresh_library()
        self._on_lib_select()
        self._mark_clean()
        self._set_status(f"已保存到脚本库：{self.script.name}")

    def lib_save_as(self) -> None:
        if not self._sync_settings():
            return
        if not self.script.events:
            messagebox.showinfo("提示", "当前没有事件可保存。")
            return
        name = simpledialog.askstring(
            "另存为新脚本", "脚本名称：", initialvalue=self.script.name,
            parent=self)
        if not name:
            return
        self.script.name = name.strip()
        meta = self.library.save_new(self.script, self.script.name)
        self.current_filename = meta.filename
        self.name_var.set(self.script.name)
        self._refresh_library()
        self._set_status(f"已另存为「{meta.name}」")

    def lib_rename(self) -> None:
        fname = self._lib_selected()
        if not fname:
            return
        meta = self.library.get(fname)
        if not meta:
            return
        new_name = simpledialog.askstring("重命名", "新的名称：",
                                          initialvalue=meta.name, parent=self)
        if not new_name or new_name.strip() == meta.name:
            return
        new_meta = self.library.rename(fname, new_name)
        if not new_meta:
            messagebox.showerror("重命名失败", "文件名无效或已被占用。")
            return
        if self.current_filename == fname:
            self.current_filename = new_meta.filename
            self.script.name = new_meta.name
            self.name_var.set(new_meta.name)
        self._refresh_library()
        self._set_status(f"已重命名为「{new_meta.name}」")

    def lib_duplicate(self) -> None:
        fname = self._lib_selected()
        if not fname:
            return
        meta = self.library.duplicate(fname)
        if not meta:
            messagebox.showerror("复制失败", "无法读取该脚本。")
            return
        self._refresh_library()
        self._set_status(f"已复制为「{meta.name}」")

    def lib_delete(self) -> None:
        fname = self._lib_selected()
        if not fname:
            return
        meta = self.library.get(fname)
        if not meta:
            return
        if not messagebox.askyesno("删除脚本",
                                   f"确定删除「{meta.name}」？\n"
                                   "（会移到回收目录，可手动恢复）"):
            return
        self.library.delete(fname)
        if self.current_filename == fname:
            self.current_filename = None
        self._refresh_library()
        self._set_status(f"已删除「{meta.name}」")

    def lib_import(self) -> None:
        path = filedialog.askopenfilename(
            title="导入脚本到库",
            initialdir=self._default_save_dir(),
            filetypes=[("脚本（.ajjl / .json）", "*.ajjl *.json"),
                       ("单文件脚本包", "*.ajjl"),
                       ("所有文件", "*.*")])
        if not path:
            return
        meta = self.library.import_file(path)
        if not meta:
            messagebox.showerror("导入失败", "文件不是有效的脚本格式。")
            return
        self._refresh_library()
        self._set_status(f"已导入「{meta.name}」")

    def lib_export(self) -> None:
        fname = self._lib_selected()
        if not fname:
            return
        meta = self.library.get(fname)
        if not meta:
            return
        dest = filedialog.asksaveasfilename(
            title="导出脚本",
            defaultextension=".ajjl",
            initialdir=self._default_save_dir(),
            initialfile=f"{meta.name}.ajjl",
            filetypes=[("单文件脚本包（含图片/数据，推荐）", "*.ajjl"),
                       ("纯 JSON 脚本", "*.json")])
        if not dest:
            return
        try:
            script = self.library.load(fname)
            if script is None:
                messagebox.showerror("导出失败", "读不出这个脚本。")
                return
            real, n_attach, missing = script_package.save(script, dest)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))
            return
        self._remember_dir(real)
        msg = f"已导出 {os.path.basename(real)}"
        if n_attach:
            msg += f"（含 {n_attach} 个图片/数据文件）"
        self._set_status(msg)
        if missing:
            messagebox.showwarning(
                "有文件没找到",
                "这些文件在当前电脑上不存在，没有打进包里：\n\n"
                + "\n".join(missing[:8])
                + ("\n…" if len(missing) > 8 else ""),
                parent=self)

    def open_lib_dir(self) -> None:
        d = str(self.library.scripts_dir)
        try:
            if SYSTEM == "Windows":
                os.startfile(d)                     # type: ignore[attr-defined]
            elif SYSTEM == "Darwin":
                os.system(f'open "{d}"')
            else:
                os.system(f'xdg-open "{d}" >/dev/null 2>&1 &')
        except Exception:
            messagebox.showinfo("脚本库目录", d)

    # ============================================================ 执行日志
    def _build_log_panel(self, parent: ttk.Frame) -> None:
        head = ttk.Frame(parent)
        head.pack(fill="x", pady=(6, 2))
        ttk.Label(head, text="📝 执行日志",
                  font=("", 10, "bold")).pack(side="left")
        ttk.Button(head, text="历史", width=6,
                   command=self.show_log_history).pack(side="right")
        ttk.Button(head, text="打开目录", width=8,
                   command=self.open_log_dir).pack(side="right", padx=(0, 3))
        ttk.Button(head, text="清空", width=6,
                   command=self.clear_log).pack(side="right", padx=(0, 3))

        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=8, wrap="none",
                                state="disabled", font=("Consolas", 9),
                                background="#fbfbfb", relief="flat")
        vsb = ttk.Scrollbar(wrap, orient="vertical",
                            command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        # 三档配色
        self.log_text.tag_configure("ok", foreground="#2e7d32")
        self.log_text.tag_configure("warn", foreground="#ef6c00")
        self.log_text.tag_configure("error", foreground="#c62828")
        self.log_text.tag_configure("info", foreground="#333333")
        self.log_text.tag_configure("dim", foreground="#888888")

    def _append_log(self, entry) -> None:
        """回放线程回调（已通过 after 切回主线程）"""
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", entry.display() + "\n", entry.level)
        # 环形缓冲：只保留最近 800 行
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 800:
            self.log_text.delete("1.0", f"{lines - 800}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_log_dir(self) -> None:
        d = str(app_dir() / "logs")
        try:
            if SYSTEM == "Windows":
                os.startfile(d)                     # type: ignore[attr-defined]
            elif SYSTEM == "Darwin":
                os.system(f'open "{d}"')
            else:
                os.system(f'xdg-open "{d}" >/dev/null 2>&1 &')
        except Exception:
            messagebox.showinfo("日志目录", d)

    def show_log_history(self) -> None:
        """历史日志查看器"""
        win = tk.Toplevel(self)
        win.title("历史执行日志")
        win.geometry("820x520")
        win.transient(self)

        paned = ttk.PanedWindow(win, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        # 左：日志文件列表
        left = ttk.Frame(paned, width=240)
        paned.add(left, weight=1)
        files = list_logs(limit=100)
        tree = ttk.Treeview(left, columns=("t",), show="tree")
        tree.column("#0", width=200)
        tree.pack(fill="both", expand=True)
        for i, f in enumerate(files):
            tree.insert("", "end", iid=str(i), text=f" {f['name']}")

        # 右：内容
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        txt = tk.Text(right, wrap="none", state="disabled",
                      font=("Consolas", 9))
        vsb = ttk.Scrollbar(right, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        txt.tag_configure("ok", foreground="#2e7d32")
        txt.tag_configure("warn", foreground="#ef6c00")
        txt.tag_configure("error", foreground="#c62828")

        def on_pick(_e=None):
            sel = tree.selection()
            if not sel:
                return
            f = files[int(sel[0])]
            rows = read_log(f["path"], limit=2000)
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            for r in rows:
                line = (f"{r.get('ts','')} {r.get('level','info'):5s} "
                        f"{('#' + str(r['step'])) if r.get('step') else '':5s} "
                        f"{r.get('message','')}")
                if r.get("shot"):
                    line += f"  📷 {os.path.basename(r['shot'])}"
                txt.insert("end", line + "\n", r.get("level", "info"))
            txt.configure(state="disabled")

        tree.bind("<<TreeviewSelect>>", on_pick)
        if files:
            tree.selection_set("0")

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(btn_row, text=f"共 {len(files)} 份日志，默认保留最近 30 天",
                  foreground="#666").pack(side="left")
        ttk.Button(btn_row, text="清理旧日志",
                   command=lambda: self._cleanup_logs_ui(win)).pack(side="right")

    def _cleanup_logs_ui(self, parent) -> None:
        from core.logger import cleanup_logs
        n = cleanup_logs(keep_days=30)
        messagebox.showinfo("清理完成", f"已删除 {n} 个过期文件。", parent=parent)

    # ============================================================ 单步调试
    def step_selected(self) -> None:
        """单步执行选中的那一条事件"""
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先在列表里选中一行。")
            return
        ev = self.script.events[idx]
        if not ev.enabled:
            self._set_status("该步骤已被禁用，跳过")
            return
        if self.recorder.running:
            return
        self._set_status(f"单步执行：{ev.detail()}")
        msg = self.player.step_once(ev)
        self._set_status(f"单步结果：{msg}  ← {ev.detail()}")
        # 执行完自动移到下一行，便于连续单步
        if idx + 1 < len(self.script.events):
            self.tree.selection_set(str(idx + 1))
            self.tree.see(str(idx + 1))

    def run_to_selected(self) -> None:
        """从头执行到选中的那一行"""
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先在列表里选中目标行。")
            return
        if self.player.running or self.recorder.running:
            return
        if not self._sync_settings():
            return
        self.btn_play.configure(state="disabled")
        self.btn_rec.configure(state="disabled")
        self.btn_abort.configure(state="normal")
        self._set_status(f"执行到第 {idx + 1} 行…（F10 中断）")

        focus = None
        if self.focus_var.get() and self.target_window:
            hwnd = self.target_window
            focus = lambda: focus_window(hwnd)          # noqa: E731

        self._run_logger = RunLogger(
            script_name=f"{self.script.name}_到第{idx + 1}行",
            on_entry=lambda e: self.after(0, self._append_log, e),
        )
        self.clear_log()
        self.player.run_until(
            self.script, idx,
            on_event=lambda ev, lap: self.after(0, self._on_play_event, ev, lap),
            on_done=lambda ok, msg: self.after(0, self._on_play_done, ok, msg),
            focus_window=focus,
            logger=self._run_logger,
        )

    # ============================================================ 取点工具
    def _pick_and_fill(self, count: int, builder) -> None:
        """打开取点工具，取完后用 builder 构造事件插入

        builder(points) -> Event | None
        """
        self.iconify()                 # 先收起自己，避免遮挡目标
        self.after(220, lambda: self._open_picker(count, builder))

    def _open_picker(self, count: int, builder) -> None:
        def on_done(pts):
            self.after(0, self._after_pick, pts, builder)

        try:
            pick_points(self, count=count, on_done=on_done)
        except Exception as exc:                       # noqa: BLE001
            self.deiconify()
            messagebox.showerror("取点失败", str(exc))

    def _after_pick(self, pts, builder) -> None:
        self.deiconify()
        self.lift()
        if not pts:
            self._insert_index = None        # 取消取点：清掉待插入位置
            self._set_status("已取消取点")
            return
        ev = builder(pts)
        if ev is not None:
            self._append(ev)
            self._set_status(f"已插入：{ev.detail()}")
        else:
            self._insert_index = None

    # ------------------------------------------------------------ 框选截图
    def _grab_and_fill(self, builder) -> None:
        """框选屏幕 → 自动截图存模板 → builder(rect, path) 构造事件插入

        builder(rect, path) -> Event | None
        """
        self.iconify()                 # 先收起自己，避免挡住目标
        self.after(220, lambda: self._open_grabber(builder))

    def _open_grabber(self, builder) -> None:
        def on_done(rect, path, err):
            self.after(0, self._after_grab, rect, path, err, builder)

        try:
            grab_region(self, on_done=on_done)
        except Exception as exc:                       # noqa: BLE001
            self.deiconify()
            messagebox.showerror("框选失败", str(exc), parent=self)

    def _after_grab(self, rect, path, err, builder) -> None:
        self.deiconify()
        self.lift()
        if err:
            self._insert_index = None
            messagebox.showerror("框选截图失败",
                                 f"{err}\n\n（目标图没保存，指令没有添加）",
                                 parent=self)
            return
        if not rect:
            self._insert_index = None        # 取消框选
            self._set_status("已取消框选")
            return
        ev = builder(rect, path)
        if ev is None:
            self._insert_index = None
            return
        self._append(ev)
        # 模板太淡的话回放几乎必然点错。以前用弹窗拦，这里不打扰，
        # 只在状态栏提醒一句——框选流程要的就是快。
        self._set_status(f"已插入：{ev.detail()}{self._quality_note(path)}")

    def _cmd_click_left(self) -> None:
        self._pick_and_fill(1, lambda p: Event(
            t=0, type="click", x=p[0][0], y=p[0][1], button="left"))

    def _cmd_click_right(self) -> None:
        self._pick_and_fill(1, lambda p: Event(
            t=0, type="edit", key="right_click",
            x=p[0][0], y=p[0][1], button="right"))

    def _cmd_double_click(self) -> None:
        self._pick_and_fill(1, lambda p: Event(
            t=0, type="dblclick", x=p[0][0], y=p[0][1], button="left"))

    def _cmd_drag(self) -> None:
        def build(p):
            if len(p) < 2:
                return None
            return Event(t=0, type="drag", x=p[0][0], y=p[0][1],
                         x2=p[1][0], y2=p[1][1], button="left")

        self._pick_and_fill(2, build)

    def _cmd_type_text(self) -> None:
        text = simpledialog.askstring(
            "输入文本", "要输入的文本：\n\n"
            "支持变量：{{列名}} —— 配合右侧「数据源」，\n"
            "回放时自动替换成 Excel/CSV 对应行的值。", parent=self)
        if text:
            self._append(Event(t=0, type="keytype", text=text))
        else:
            self._insert_index = None       # 用户取消

    def _cmd_hotkey(self) -> None:
        val = simpledialog.askstring(
            "组合键", "用 + 连接，如 ctrl+c、ctrl+shift+esc：", parent=self)
        if not val:
            self._insert_index = None       # 用户取消
            return
        keys = [k.strip().lower() for k in val.split("+") if k.strip()]
        if keys:
            self._append(Event(t=0, type="hotkey", keys=keys))
        else:
            self._insert_index = None

    def _cmd_delay(self) -> None:
        ms = simpledialog.askinteger("等待", "毫秒：", initialvalue=500,
                                     minvalue=0, parent=self)
        if ms is not None:
            self._append(Event(t=0, type="delay", dx=ms))
        else:
            self._insert_index = None       # 用户取消

    def _cmd_loop_start(self) -> None:
        val = simpledialog.askstring(
            "循环开始", "循环次数：\n· 填数字 = 执行几次\n"
                        "· 留空 = 无限循环（F9 停止）\n"
                        "· 填 data = 按 Excel 数据行数循环",
            initialvalue="data", parent=self)
        if val is None:
            self._insert_index = None       # 用户取消
            return
        txt = val.strip()
        self._append(Event(t=0, type="loop_start",
                           text=txt if txt else "forever"))

    def _cmd_loop_end(self) -> None:
        self._append(Event(t=0, type="loop_end"))

    def _cmd_break(self) -> None:
        self._append(Event(t=0, type="break"))

    def _cmd_else(self) -> None:
        self._append(Event(t=0, type="else"))

    def _cmd_if_end(self) -> None:
        self._append(Event(t=0, type="if_end"))

    # ------------------------------------------------------------ 如果（条件）
    def _cmd_if(self) -> None:
        """插入「如果」事件：屏幕有图 或 变量比较"""
        kind = simpledialog.askstring(
            "如果（条件）", "条件类型：\n1 = 屏幕有/没有某图\n2 = 变量比较",
            initialvalue="1", parent=self)
        if kind is None:
            self._insert_index = None
            return
        if kind.strip() == "2":
            self._cmd_if_var()
        else:
            self._cmd_if_image()

    def _cmd_if_var(self) -> None:
        left = simpledialog.askstring(
            "变量比较", "左边（变量或文字）：\n如 {{金额}}、{{分支机构}}、{{行号}}",
            parent=self)
        if not left:
            self._insert_index = None
            return
        op_raw = simpledialog.askstring(
            "比较方式", "输入比较符：\n=  不等于填 !=\n包含 / > / <",
            initialvalue="=", parent=self)
        if op_raw is None:
            self._insert_index = None
            return
        op_map = {"=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
                  "包含": "contains", ">": "gt", "<": "lt"}
        op = op_map.get(op_raw.strip(), "eq")
        v = simpledialog.askstring("比较值", "右边：", initialvalue="",
                                   parent=self)
        if v is None:
            self._insert_index = None
            return
        self._append(Event(t=0, type="if", cond="var",
                           left=left.strip(), op=op, right=v.strip()))

    def _known_images(self) -> list[str]:
        """可以作为条件判断的候选图

        顺序即优先级：脚本里**最近用过**的排最前 —— 刚框完的那张找图模板
        就躺在事件列表末尾，一眼就能选中，不用再去文件夹里认文件名。
        """
        exts = (".png", ".jpg", ".jpeg", ".bmp")

        def usable(p) -> bool:
            return bool(p) and str(p).lower().endswith(exts)

        seen: dict[str, None] = {}
        for ev in reversed(self.script.events):
            if ev.type in ("find_image", "if_image"):
                if usable(ev.text):
                    seen.setdefault(str(ev.text), None)
            elif ev.type == "if" and (ev.cond or "") == "image":
                if usable(ev.left):
                    seen.setdefault(str(ev.left), None)
        try:
            files = sorted(images_dir().glob("*.png"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[:15]:
                seen.setdefault(str(p), None)
        except Exception:                   # noqa: BLE001
            pass
        return list(seen)

    def _cmd_if_image(self) -> None:
        """图片条件：从用过的图里挑（或现场框选），不必再翻文件对话框"""
        res = ask_image_condition(self, self._known_images(), hit=True)
        if res is None:
            self._insert_index = None       # 用户取消
            return
        path, hit = res
        op = "img_hit" if hit else "img_miss"
        if path == GRAB_ITEM:
            def build(rect, p):
                return Event(t=0, type="if", cond="image", left=p, op=op, dx=0)

            self._grab_and_fill(build)
            return
        ev = Event(t=0, type="if", cond="image", left=path, op=op, dx=0)
        self._append(ev)
        self._set_status(f"已插入：{ev.detail()}{self._quality_note(path)}")

    def _template_std(self, path: str) -> float | None:
        """模板图的灰度标准差（越小越「平」）；读不出来返回 None"""
        try:
            import numpy as np
            from PIL import Image

            with Image.open(path) as im0:
                arr = np.asarray(im0.convert("L"), dtype=np.float32)
        except Exception:                      # noqa: BLE001
            return None                        # 读不了就别拦，交给运行时判断
        if arr.size == 0:
            return None
        return float(arr.std())

    def _quality_note(self, path: str) -> str:
        """模板太淡时的提醒后缀（只挂状态栏，不弹窗打断）"""
        std = self._template_std(path)
        if std is None or std >= 8.0:
            return ""
        return (f"　·　提示：这张图对比度偏低（{std:.1f}），在屏幕上几乎处处相似，"
                f"回放容易点错，建议重框一个边界清楚的目标")

    def _cmd_find_image(self) -> None:
        """找图点击：直接在屏幕上框选目标 → 自动截图存模板 → 自动添加

        之前是「选文件 + 输入相似度 + 手填 x,y,宽,高」三个弹窗，工具本该
        自己干的事全推给用户了（先自己截图、再记坐标）。现在只做一件事：
        拖一个框，松手就是一条找图指令。
        """
        def build(rect, path):
            ev = Event(t=0, type="find_image", text=path, dx=90)
            # 默认全屏找：框选只是为了取模板，不是限定搜索范围。
            # 想只在某块区域找，右键「编辑事件」里改区域即可。
            ev.set_region(None)
            return ev

        self._grab_and_fill(build)

    def _cmd_ocr(self) -> None:
        """验证码识别：框选验证码 →（可选）点输入框 → 插入指令"""
        from core import ocr as ocr_mod

        if not ocr_mod.available():
            if not messagebox.askyesno(
                    "没连上 OCR 服务",
                    "没连上本机 Umi-OCR（127.0.0.1:1224）。\n\n"
                    "请先启动 Umi-OCR，并在「设置 → HTTP 服务」里打开服务；\n"
                    "端口不一样的话，插入后右键「编辑事件」可以改服务地址。\n\n"
                    "仍然插入这条指令吗？（回放时会再次尝试连接）", parent=self):
                self._insert_index = None
                return

        if not messagebox.askokcancel(
                "选择验证码区域",
                "接下来框选验证码图片：\n\n"
                "  第 1 点：验证码左上角\n"
                "  第 2 点：验证码右下角\n"
                "  第 3 点（可跳过）：点一下要填写的输入框\n\n"
                "只想输入到当前光标位置？取完 2 点按 Enter 结束。", parent=self):
            self._insert_index = None
            return

        def build(pts):
            if len(pts) < 2:
                return None
            ev = Event(t=0, type="ocr", text="")
            ev.set_region(_rect_of(pts))
            if len(pts) >= 3:
                ev.x, ev.y = pts[2][0], pts[2][1]
            return ev

        self._pick_and_fill(3, build)

    def _pick_for_existing(self, ev: Event) -> None:
        """给已有事件重新框选区域（验证码识别编辑用）"""
        def build(pts):
            if len(pts) >= 2:
                ev.set_region(_rect_of(pts))
                if len(pts) >= 3:
                    ev.x, ev.y = pts[2][0], pts[2][1]
                else:
                    ev.x = ev.y = None
                self._refresh_list()
                self._set_status(f"已更新：{ev.detail()}")
            return None

        self._pick_and_fill(3, build)

    def _ask_find_region(self, cur=None):
        """询问找图搜索区域 → (region 或 None, 是否确认)

        region=(x, y, 宽, 高)，None 表示全屏；第二个返回 False 表示用户取消。
        """
        init = f"{cur[0]},{cur[1]},{cur[2]},{cur[3]}" if cur else ""
        text = simpledialog.askstring(
            "找图搜索区域（可留空）",
            "留空 = 全屏找。程序会自动判断哪处是任务栏图标、哪处是\n"
            "桌面图标，优先点任务栏里的那个。\n\n"
            "也可以只在这个矩形里找，格式：x,y,宽,高（物理像素）\n"
            "例：整条任务栏 → 0,1500,2560,100\n"
            "限定区域后，区域外的同款图标直接不参与匹配。",
            initialvalue=init, parent=self)
        if text is None:
            return None, False
        text = text.strip().replace("，", ",").replace(" ", "")
        if not text:
            return None, True               # 留空 = 全屏
        try:
            vals = [int(float(p)) for p in text.split(",") if p]
        except ValueError:
            vals = []
        if len(vals) != 4 or vals[2] <= 0 or vals[3] <= 0:
            messagebox.showwarning(
                "格式不对",
                "请按 x,y,宽,高 填 4 个数字，例如：0,1500,2560,100\n"
                "宽和高都必须大于 0。", parent=self)
            return None, False
        return tuple(vals), True

    def _cmd_comment(self) -> None:
        text = simpledialog.askstring("注释", "注释内容：", parent=self)
        if text:
            self._append(Event(t=0, type="comment", text=text))
        else:
            self._insert_index = None       # 用户取消

    # ============================================================ 文件
    def _default_save_dir(self) -> str:
        d = (self.cfg.get("last_dir") or "").strip()
        return d if os.path.isdir(d) else str(Path.home())

    def save_script(self) -> None:
        if not self._sync_settings():
            return
        path = filedialog.asksaveasfilename(
            title="保存脚本", defaultextension=".ajjl",
            initialdir=self._default_save_dir(),
            initialfile=f"{self.script.name}.ajjl",
            filetypes=[("单文件脚本包（含图片/数据，推荐）", "*.ajjl"),
                       ("纯 JSON 脚本", "*.json"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            real, n_attach, missing = script_package.save(self.script, path)
            self.script_path = real
            self._remember_dir(real)
            self._mark_clean()
            msg = f"已保存：{os.path.basename(real)}"
            if n_attach:
                msg += f"（含 {n_attach} 个图片/数据文件）"
            self._set_status(msg)
            if missing:
                messagebox.showwarning(
                    "有文件没找到",
                    "下面这些文件在当前电脑上找不到，没有打进包里：\n\n"
                    + "\n".join(missing[:8])
                    + ("\n…" if len(missing) > 8 else "")
                    + "\n\n相关步骤在别的电脑上会执行失败。",
                    parent=self)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("保存失败", str(exc))

    def _remember_dir(self, path: str) -> None:
        try:
            self.cfg["last_dir"] = str(Path(path).parent)
            save_config(self.cfg)
        except Exception:                              # noqa: BLE001
            pass

    def _confirm_discard(self, action: str = "继续") -> bool:
        """有未保存改动时确认一次；返回 True 表示可以继续"""
        if not self._dirty or not self.script.events:
            return True
        ans = messagebox.askyesnocancel(
            "尚未保存",
            f"当前脚本「{self.script.name}」有未保存的改动。\n\n"
            f"{action}前要保存吗？",
            parent=self)
        if ans is None:                        # 取消
            return False
        if ans:                                # 是：保存
            self.save_script()
            return not self._dirty
        return True                            # 否：放弃改动

    def open_script(self) -> None:
        if not self._confirm_discard("打开其他脚本"):
            return
        path = filedialog.askopenfilename(
            title="打开脚本",
            initialdir=self._default_save_dir(),
            filetypes=[("脚本（.ajjl / .json）", "*.ajjl *.json"),
                       ("单文件脚本包", "*.ajjl"),
                       ("纯 JSON 脚本", "*.json"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            is_pkg = script_package.is_package(path)
            self.script = script_package.load(path)
            self.script_path = path
            self._remember_dir(path)
            self._apply_script(self.script, None)
            extra = "（脚本包，图片已释放）" if is_pkg else ""
            self._set_status(f"已打开：{os.path.basename(path)}，"
                             f"{len(self.script.events)} 条事件{extra}")
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("打开失败", f"无法解析脚本文件：\n{exc}")

    def clear_script(self) -> None:
        if self.recorder.running or self.player.running:
            return
        if self.script.events and not messagebox.askyesno(
                "确认", "清空当前所有事件？\n\n清空后可以用 Ctrl+Z 撤销。"):
            return
        self._snapshot()
        self.script = Script()
        self._load_script_vars(self.script)
        self._folded.clear()
        self._refresh_list()
        self._mark_dirty()
        self._set_status("已清空（Ctrl+Z 可撤销）")

    # ============================================================ 杂项
    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _on_close(self) -> None:
        # 倒计时中：先取消，别让用户以为程序卡住了
        if getattr(self, "_cd_done", None) is not None:
            self.cancel_countdown()
            return

        # 有未保存改动 → 问一次，避免手滑丢掉刚录的脚本
        if self._dirty and self.script.events:
            ans = messagebox.askyesnocancel(
                "尚未保存",
                f"当前脚本「{self.script.name}」有 {len(self.script.events)} 条事件"
                "还没保存。\n\n退出前要保存吗？",
                parent=self)
            if ans is None:                    # 取消：留在程序里
                return
            if ans:                            # 是：保存（用户取消了保存框就别退）
                self.save_script()
                if self._dirty:
                    return
        try:
            if self.recorder.running:
                self.recorder.stop()
            if self.player.running:
                self.player.stop()
            self._hide_hud()
            if self.hud is not None:
                try:
                    self.hud.destroy()
                except Exception:
                    pass
            self.hotkeys.stop()
        finally:
            self.destroy()


def main() -> None:
    MacroApp().mainloop()


if __name__ == "__main__":
    main()
