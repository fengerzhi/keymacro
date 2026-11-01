"""recorder.py —— 全局鼠标 / 键盘录制器

基于 pynput，监听操作系统级别的输入事件。

核心能力
─────────────────────────────────────────────────────────
1. 组合键智能识别
   Ctrl+C 这类操作不再录成散乱的 keydown/keyup，
   而是在按下主键的瞬间合成一条 hotkey 事件（keys=["ctrl","c"]），
   回放时按键顺序、按下/释放逆序都能精确还原。
2. 语义化编辑指令
   Ctrl+A/C/X/V/Z/Y 以及单独的 Delete/Backspace/Enter/Esc/Tab
   自动识别为 edit 事件（全选/复制/粘贴/剪切/删除…），脚本可读性大幅提升。
   可用 semantic_edit=False 关闭，退化为纯 hotkey。
3. 右键完整支持
   右键按下/释放自动合成为 click(button="right")。
4. 双击识别
   同一位置 400ms 内两次左键按下 → 合并为 dblclick。
5. 拖拽识别
   按下后移动超过 6px 再释放 → 合并为 drag 事件。
6. 完全静默
   自身窗口区域的事件自动排除，避免把"点停止"录进脚本。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard, mouse

from .script_model import Event

STOP_KEY = keyboard.Key.f9

# ---------------------------------------------------------------- 键名规范化
MODIFIER_KEYS = {
    keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
    keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
    keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
    keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
}

MODIFIER_NAMES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}

KEY_ALIAS = {
    keyboard.Key.space: "space",
    keyboard.Key.enter: "enter",
    keyboard.Key.tab: "tab",
    keyboard.Key.backspace: "backspace",
    keyboard.Key.delete: "delete",
    keyboard.Key.esc: "esc",
    keyboard.Key.up: "up",
    keyboard.Key.down: "down",
    keyboard.Key.left: "left",
    keyboard.Key.right: "right",
    keyboard.Key.home: "home",
    keyboard.Key.end: "end",
    keyboard.Key.page_up: "page_up",
    keyboard.Key.page_down: "page_down",
    keyboard.Key.insert: "insert",
    keyboard.Key.caps_lock: "caps_lock",
}

# 单键即可映射为语义指令
SINGLE_KEY_EDIT = {
    "delete": "delete",
    "backspace": "backspace",
    "enter": "enter",
    "esc": "esc",
    "tab": "tab",
}

# Ctrl/Cmd + 键 → 语义指令
COMBO_EDIT = {
    "a": "select_all",
    "c": "copy",
    "x": "cut",
    "v": "paste",
    "z": "undo",
    "y": "redo",
}

DOUBLE_CLICK_MS = 400
DOUBLE_CLICK_PX = 6
DRAG_THRESHOLD_PX = 6


def normalize_key(key) -> str:
    """把 pynput 按键对象转成可序列化的字符串"""
    if key in MODIFIER_KEYS:
        raw = getattr(key, "name", None) or str(key).replace("Key.", "")
        return MODIFIER_NAMES.get(raw, raw)
    if key in KEY_ALIAS:
        return KEY_ALIAS[key]
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char
        if key.vk is not None:
            return f"vk_{key.vk}"
    name = getattr(key, "name", None)
    return (name or str(key)).replace("Key.", "")


class Recorder:
    """全局输入录制器"""

    def __init__(self, on_stop: Callable[[], None] | None = None,
                 on_event: Callable[[Event], None] | None = None,
                 on_drop: Callable[[Event], None] | None = None,
                 on_press: Callable[[int, int, str], None] | None = None):
        self.events: list[Event] = []
        self.running = False
        self.paused = False               # 暂停：监听仍在跑，但不写入事件
        self.on_stop = on_stop
        self.on_event = on_event          # 每条事件产生时回调（实时预览用）
        self.on_drop = on_drop            # 事件被丢弃时回调（UI 同步移除行）
        # 按下瞬间回调 (x, y, button)：用于涟漪特效，必须在按键那一刻触发，
        # 不能等 mousedown 落盘（那要等到松开鼠标，画面就"没有动感"了）
        self.on_press = on_press

        self._t0 = 0.0
        self._lock = threading.Lock()
        self._mouse_listener: mouse.Listener | None = None
        self._kbd_listener: keyboard.Listener | None = None
        self._stopping = False

        # 排除区域（屏幕绝对坐标）
        self.exclusion: tuple[int, int, int, int] | None = None
        # 多个排除区域（主窗口 + 迷你控制条）
        self.exclusion_rects: list[tuple[int, int, int, int]] = []

        # 配置
        self.semantic_edit = True         # 识别为【全选】【复制】等
        self.min_move_ms = 10
        self.min_move_px = 3
        # 点击前保留的移动轨迹条数：只留最后 2 条（足够还原「移过去再点」的轨迹），
        # 更早的纯移动点对回放没有价值，只会让脚本臃肿、回放变慢。
        self.moves_before_click = 2
        # 是否在录制结束后自动清理无用移动事件
        self.trim_moves = True

        # 内部状态
        self._last_move = (0.0, 0, 0)
        self._recent_moves: list[Event] = []   # 当前保留的移动事件（滚动窗口）
        self._text_buffer: list[str] = []
        self._text_start = 0.0
        self._mods_down: set[str] = set()      # 当前按住的修饰键
        self._combo_active = False             # 是否处于组合键过程中
        self._consumed: set[str] = set()       # 按下时已消费、release 时应忽略的键
        self._last_click: dict | None = None   # 最近一次左键单击（判双击）
        self._keys_down: set[str] = set()      # 当前物理按下的键（去重用）
        self._last_press_at: dict[str, float] = {}  # 每键最后一次 press 时间（去重窗口）
        self._last_release_at: dict[str, float] = {}  # release 去重窗口
        self._swallow_release = False          # 双击后的释放事件需吞掉
        self._pending: Event | None = None     # 待合并的 mousedown（判双击）
        self._drag_start: tuple[int, int, float] | None = None

    # ============================================================ 生命周期
    def start(self) -> None:
        if self.running:
            return
        self.events.clear()
        self._text_buffer.clear()
        self._mods_down.clear()
        self._pending = None
        self._drag_start = None
        self._combo_active = False
        self._consumed.clear()
        self._last_click = None
        self._keys_down.clear()
        self._last_press_at.clear()
        self._last_release_at.clear()
        self._swallow_release = False
        self._stopping = False
        self.paused = False
        self._t0 = time.perf_counter()
        self.running = True

        self._mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._kbd_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._mouse_listener.start()
        self._kbd_listener.start()

    def stop(self) -> list[Event]:
        if not self.running:
            return self.events
        self._stopping = True
        self.paused = False
        self._flush_text()
        self._flush_pending()
        for lst in (self._mouse_listener, self._kbd_listener):
            if lst:
                try:
                    lst.stop()
                except Exception:
                    pass
        self._mouse_listener = self._kbd_listener = None
        self.running = False
        if self.trim_moves:
            self._final_trim()
        self._consolidate_typing()
        return self.events

    # ------------------------------------------------------------ 暂停 / 恢复
    def pause(self) -> bool:
        """暂停录制：监听线程继续运行（保证恢复后不漏按键状态），
        但期间产生的所有输入都被丢弃，不会写进脚本。

        返回 True 表示本次操作生效（转为暂停）。
        """
        if not self.running or self.paused:
            return False
        # 先把「暂停前」还没落盘的一段输入收尾，避免暂停后文本被截断
        self._flush_text()
        self._flush_pending()
        self._recent_moves.clear()
        self.paused = True
        return True

    def resume(self) -> bool:
        """恢复录制；返回 True 表示从暂停态恢复。

        恢复时重置内部状态，避免把暂停期间按住的键误判为组合键。
        """
        if not self.running or not self.paused:
            return False
        self._mods_down.clear()
        self._keys_down.clear()
        self._consumed.clear()
        self._combo_active = False
        self._last_click = None
        self._drag_start = None
        self._recent_moves.clear()
        self._text_buffer.clear()
        self._last_move = (0.0, 0, 0)
        self._last_press_at.clear()
        self._last_release_at.clear()
        self._swallow_release = False
        self.paused = False
        return True

    def toggle_pause(self) -> bool:
        """切换暂停状态；返回切换后是否处于暂停。"""
        if not self.running:
            return False
        if self.paused:
            self.resume()
            return False
        self.pause()
        return True

    # ------------------------------------------------------------ 统计
    @property
    def duration_ms(self) -> int:
        """已录制时长（毫秒）。暂停期间不计入。"""
        if not self.running or not self._t0:
            return 0
        return max(0, int((time.perf_counter() - self._t0) * 1000))

    @property
    def click_count(self) -> int:
        return sum(1 for e in self.events if e.type in ("click", "dblclick"))

    def snapshot(self) -> tuple[int, int, int]:
        """一次性拿 (事件数, 点击次数, 时长ms)，给 UI 计时器用。"""
        with self._lock:
            n = len(self.events)
            clicks = sum(1 for e in self.events if e.type in ("click", "dblclick"))
        return n, clicks, self.duration_ms

    def _final_trim(self) -> None:
        """停止录制后的兜底清理

        滑动窗口已经实时削掉大部分无用移动，但有两类残留：
          1. 录制末尾的悬停（用户停下鼠标才按 F9），这些点后面没有点击
          2. 每个「动作」之前保留的 N 条移动若超出需要（例如两次点击之间
             保留了 2 条，但第 1 条离得很远）
        统一规则：**只有紧邻真实动作（点击/滚动/按键）之前的最后 N 条移动
        才有保留价值，其余全部删除。**
        """
        keep = max(0, int(self.moves_before_click))
        events = self.events
        n = len(events)
        keep_flags = [False] * n

        # 从每个动作向前数 keep 条连续 mousemove
        for i, ev in enumerate(events):
            if ev.type == "mousemove" or not ev.enabled:
                continue
            cnt = 0
            j = i - 1
            while j >= 0 and cnt < keep:
                if events[j].type == "mousemove":
                    keep_flags[j] = True
                    cnt += 1
                else:
                    break          # 中间隔着别的动作，不再往前数
                j -= 1

        removed = [e for k, e in enumerate(events) if not keep_flags[k]
                   and e.type == "mousemove"]
        if not removed:
            return

        with self._lock:
            self.events = [e for k, e in enumerate(events)
                           if not (e.type == "mousemove" and not keep_flags[k])]
        if self.on_drop:
            for e in removed:
                try:
                    self.on_drop(e)
                except Exception:
                    pass

    # ============================================================ 工具
    def _now_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    def _excluded(self, x: int, y: int) -> bool:
        if self.exclusion:
            ex, ey, ew, eh = self.exclusion
            if ex <= x < ex + ew and ey <= y < ey + eh:
                return True
        for (ex, ey, ew, eh) in self.exclusion_rects:
            if ex <= x < ex + ew and ey <= y < ey + eh:
                return True
        return False

    def _add(self, ev: Event) -> None:
        with self._lock:
            self.events.append(ev)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass

    def _blocked(self) -> bool:
        return not self.running or self._stopping or self.paused

    # ============================================================ 鼠标
    def _on_move(self, x: int, y: int) -> None:
        if self._blocked() or self._excluded(x, y):
            return

        now = time.perf_counter()
        lt, lx, ly = self._last_move
        if (now - lt) * 1000 < self.min_move_ms:
            return
        if abs(x - lx) < self.min_move_px and abs(y - ly) < self.min_move_px:
            return
        self._last_move = (now, x, y)
        self._flush_text()

        ev = Event(t=self._now_ms(), type="mousemove", x=x, y=y, valid=True)
        self._add(ev)
        # 只保留最近 N 条移动：新点入队，超出部分从列表里删掉，
        # 这样「移过去再点击」的轨迹还在，之前的大段滑动全部丢弃。
        keep = max(0, int(self.moves_before_click))
        self._recent_moves.append(ev)
        if keep == 0:
            self._drop_event(ev)
            self._recent_moves.clear()
            return
        while len(self._recent_moves) > keep:
            self._drop_event(self._recent_moves.pop(0))

    def _drop_event(self, ev: Event) -> None:
        """从已录制列表中移除某个事件（同时回滚 UI 预览）"""
        with self._lock:
            try:
                self.events.remove(ev)
            except ValueError:
                return
        if self.on_drop:
            try:
                self.on_drop(ev)
            except Exception:
                pass

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if self._blocked() or self._excluded(x, y):
            return

        name = button.name if hasattr(button, "name") else str(button)
        now = self._now_ms()
        # 点击发生 → 此前的移动轨迹已确定，清空窗口，
        # 后续拖动不会再把这些坐标删掉
        self._recent_moves.clear()

        if pressed:
            # 涟漪等即时反馈：按下瞬间立刻回调，不等待事件落盘
            if self.on_press:
                try:
                    self.on_press(x, y, name)
                except Exception:
                    pass
            self._flush_text()
            # 双击判定：与上一次已完成的左键单击在时间/距离阈值内
            if (name == "left" and self._last_click
                    and now - self._last_click["t"] <= DOUBLE_CLICK_MS
                    and abs(x - self._last_click["x"]) <= DOUBLE_CLICK_PX
                    and abs(y - self._last_click["y"]) <= DOUBLE_CLICK_PX):
                # 把上一条 click 升级为 dblclick
                self._upgrade_to_dblclick(x, y)
                self._pending = None
                self._drag_start = None
                self._last_click = None
                self._swallow_release = True    # 本次双击的 release 不再记录
                return
            self._flush_pending()
            self._pending = Event(t=now, type="mousedown", x=x, y=y,
                                  button=name, pressed=True, valid=True)
            self._drag_start = (x, y, now)
        else:
            if self._swallow_release:
                self._swallow_release = False
                return
            if self._pending and self._pending.button == name:
                start = self._pending
                self._pending = None
                drag_start = self._drag_start
                self._drag_start = None
                # 拖拽判定
                if drag_start and name == "left":
                    sx, sy, _ = drag_start
                    moved = (abs(x - sx) > DRAG_THRESHOLD_PX
                             or abs(y - sy) > DRAG_THRESHOLD_PX)
                    if moved:
                        self._add(Event(t=start.t, type="drag",
                                        x=sx, y=sy, x2=x, y2=y,
                                        button="left", valid=True))
                        self._last_click = None
                        return
                # 普通单击（左/右/中键统一合成 click）
                self._add(Event(t=start.t, type="click", x=start.x, y=start.y,
                                button=name, valid=True))
                # 为左键记录"最近一次单击"，供下一次双击判定
                self._last_click = ({"t": now, "x": x, "y": y}
                                    if name == "left" else None)
            else:
                self._add(Event(t=now, type="mouseup", x=x, y=y,
                                button=name, pressed=False, valid=True))

    def _upgrade_to_dblclick(self, x: int, y: int) -> None:
        """把最近一条左键 click 事件改写成 dblclick"""
        with self._lock:
            for ev in reversed(self.events):
                if ev.type == "click" and ev.button == "left":
                    ev.type = "dblclick"
                    ev.x, ev.y = x, y
                    return

    def _flush_pending(self) -> None:
        """把仍在等待判定的 mousedown 落盘为 down+up"""
        if not self._pending:
            return
        p = self._pending
        self._pending = None
        self._add(p)
        self._add(Event(t=p.t, type="mouseup", x=p.x, y=p.y,
                        button=p.button, pressed=False, valid=True))

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if self._blocked() or self._excluded(x, y):
            return
        self._flush_text()
        self._recent_moves.clear()       # 滚动前的移动轨迹已确定
        self._add(Event(t=self._now_ms(), type="scroll",
                        x=x, y=y, dx=int(dx), dy=int(dy), valid=True))

    # ============================================================ 键盘
    def _on_press(self, key) -> None:
        if not self.running:
            return

        # F9 停止（自身不记录）
        if key == STOP_KEY:
            self._stopping = True
            threading.Thread(target=self._trigger_stop, daemon=True).start()
            return
        if self._stopping:
            return

        name = normalize_key(key)

        # 去重：pynput 在 X11 下会对同一物理按键重复触发 press（实测 delete 触发 2 次）。
        # 仅对**非文本键**做 30ms 窗口去重——连续输入相同字符（如 "hello" 的 ll）
        # 必须保留，不能被当成重复。
        if not self._is_text_key(key):
            now_p = time.perf_counter()
            last = self._last_press_at.get(name, 0.0)
            if (now_p - last) * 1000 < 30:
                return
            self._last_press_at[name] = now_p
        self._keys_down.add(name)

        # 修饰键：仅记录状态，不落盘
        if key in MODIFIER_KEYS or name in ("ctrl", "alt", "shift", "cmd"):
            self._mods_down.add(name)
            return

        # ---- 组合键：Ctrl/Cmd + 键 ----
        if self._mods_down & {"ctrl", "cmd"}:
            self._flush_text()
            self._combo_active = True
            has_alt = bool(self._mods_down & {"alt"})
            if (self.semantic_edit and name in COMBO_EDIT and not has_alt):
                self._add(Event(t=self._now_ms(), type="edit",
                                key=COMBO_EDIT[name]))
            else:
                keys = [k for k in ("ctrl", "alt", "shift", "cmd")
                        if k in self._mods_down]
                keys.append(name)
                self._add(Event(t=self._now_ms(), type="hotkey", keys=keys))
            self._consumed.add(name)
            return

        # ---- 单键语义指令 ----
        if (self.semantic_edit and name in SINGLE_KEY_EDIT
                and not (self._mods_down & {"alt", "shift"})):
            self._flush_text()
            self._add(Event(t=self._now_ms(), type="edit",
                            key=SINGLE_KEY_EDIT[name]))
            self._consumed.add(name)      # 标记已消费，release 时不再记
            return

        # ---- 普通可打印字符 / 空格 → 文本缓冲 ----
        # 空格并入缓冲：连续的「输入 xxx + 按键 space」合并成一条 keytype，
        # 播放时逐字输出效果不变（回车/制表等语义键仍单独记录）
        if ((self._is_text_key(key) or name == "space")
                and not (self._mods_down & {"alt"})):
            if not self._text_buffer:
                self._text_start = self._now_ms()
            self._text_buffer.append(" " if name == "space" else name)
            return

        # ---- 其他功能键 ----
        self._flush_text()
        self._add(Event(t=self._now_ms(), type="keydown", key=name))

    def _on_release(self, key) -> None:
        if self._blocked() or key == STOP_KEY:
            return
        name = normalize_key(key)

        was_down = name in self._keys_down
        self._keys_down.discard(name)

        # release 同样会双触发（X11），非文本键做 30ms 窗口去重
        if not self._is_text_key(key):
            now_r = time.perf_counter()
            last = self._last_release_at.get(name, 0.0)
            if (now_r - last) * 1000 < 30:
                return
            self._last_release_at[name] = now_r
        if not was_down and name in getattr(self, "_consumed", set()):
            return

        if key in MODIFIER_KEYS or name in ("ctrl", "alt", "shift", "cmd"):
            self._mods_down.discard(name)
            if not self._mods_down:
                self._combo_active = False
            return

        if not was_down:
            return                   # 重复的 release，忽略

        if self._is_text_key(key) or name == "space":
            return                   # 文本键/空格不单独记 keyup（空格已并入文本缓冲）

        # 已被识别为语义指令/组合键的键：消费标记后跳过
        if name in self._consumed:
            self._consumed.discard(name)
            return
        if self._combo_active:
            return

        self._add(Event(t=self._now_ms(), type="keyup", key=name))

    @staticmethod
    def _is_text_key(key) -> bool:
        if key in MODIFIER_KEYS:
            return False
        if isinstance(key, keyboard.KeyCode):
            return bool(key.char) and key.char.isprintable()
        return False

    # ============================================================ 文本聚合
    def _flush_text(self) -> None:
        if not self._text_buffer:
            return
        text = "".join(self._text_buffer)
        self._text_buffer.clear()
        self._add(Event(t=int(self._text_start), type="keytype", text=text))

    # ------------------------------------------------- 输入过程归并（stop 时）
    def _consolidate_typing(self) -> None:
        """把**紧挨着**的 keytype 事件合并成一条，并在每段前加一条
        「第N次键盘输入：<全文>」注释（替代逐次注释）。

        中间隔了任何其他事件（点击/回车/滚动…）就算两次独立输入。
        """
        events = self.events
        out: list[Event] = []
        seq = 0
        i = 0
        while i < len(events):
            ev = events[i]
            if ev.type != "keytype":
                out.append(ev)
                i += 1
                continue
            # 收集一段连续 keytype
            run = [ev]
            while i + 1 < len(events) and events[i + 1].type == "keytype":
                i += 1
                run.append(events[i])
            if len(run) == 1:
                seq += 1
                out.append(Event(t=ev.t, type="comment",
                                 text=f"第{seq}次键盘输入：{ev.text or ''}"))
                out.append(ev)
            else:
                text = "".join(e.text or "" for e in run)
                seq += 1
                out.append(Event(t=run[0].t, type="comment",
                                 text=f"第{seq}次键盘输入：{text}"))
                out.append(Event(t=run[0].t, type="keytype", text=text))
            i += 1
        self.events = out

    def _trigger_stop(self) -> None:
        if self.on_stop:
            self.on_stop()
