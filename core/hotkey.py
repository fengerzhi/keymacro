"""hotkey.py —— 全局热键管理器

统一管理所有全局热键，主窗口最小化或失去焦点时依然生效。

内置热键
─────────────────────────────────────────
    F9   停止录制
    F10  中断回放
    F11  一键开始/停止脚本（核心：无需切回工具窗口）

设计要点
─────────────────────────────────────────
1. 单监听器集中分发
   pynput 只开一个 keyboard.Listener，避免多个监听器互相干扰、
   也避免和录制器/回放器的监听器重复。
2. 回调走线程池
   监听器回调运行在 pynput 线程里，直接操作 tkinter 会崩。
   这里统一用 after(0, ...) 投递到 UI 线程（由调用方提供 scheduler）。
3. 防连发
   按住热键不放会持续触发 press，用「按下状态集合」过滤，
   必须松开再按才算新的一次。
4. 冲突与可用性检测
   注册失败或平台不支持时给出明确提示，不静默失败。
"""

from __future__ import annotations

import platform
import threading
import time
from typing import Any, Callable

from pynput import keyboard

SYSTEM = platform.system()

# 名称 → pynput 按键
HOTKEY_MAP: dict[str, Any] = {
    "f1": keyboard.Key.f1, "f2": keyboard.Key.f2, "f3": keyboard.Key.f3,
    "f4": keyboard.Key.f4, "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
    "f7": keyboard.Key.f7, "f8": keyboard.Key.f8, "f9": keyboard.Key.f9,
    "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
    "pause": keyboard.Key.pause, "scroll_lock": keyboard.Key.scroll_lock,
    "insert": keyboard.Key.insert, "home": keyboard.Key.home,
    "end": keyboard.Key.end, "esc": keyboard.Key.esc,
}

# 默认热键配置
DEFAULT_HOTKEYS = {
    "stop_record": "f9",
    "abort_play": "f10",
    "toggle_run": "f11",
    "toggle_pause": "f8",
}

HOTKEY_LABELS = {
    "stop_record": "停止录制",
    "abort_play": "中断回放",
    "toggle_run": "一键启停脚本",
    "toggle_pause": "暂停/继续录制",
}


class HotkeyManager:
    """全局热键管理器

    :param scheduler: 把回调投递到 UI 线程的函数，通常传 `root.after`
                      签名：scheduler(delay_ms, callable)
    """

    def __init__(self, scheduler: Callable[[int, Callable], Any]):
        self._scheduler = scheduler
        self._listener: keyboard.Listener | None = None
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._bindings: dict[str, str] = dict(DEFAULT_HOTKEYS)
        self._reverse: dict[Any, str] = {}
        self._held: set[Any] = set()
        self._last_fire: dict[Any, float] = {}
        self._lock = threading.Lock()
        self.running = False
        self.last_error: str | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        """启动监听，返回是否成功"""
        if self.running:
            return True
        self._rebuild_reverse()
        try:
            self._listener = keyboard.Listener(on_press=self._on_press,
                                               on_release=self._on_release)
            self._listener.start()
            self.running = True
            self.last_error = None
            return True
        except Exception as exc:                        # noqa: BLE001
            self.last_error = f"全局热键不可用：{exc}"
            self.running = False
            return False

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = None
        self.running = False

    # ------------------------------------------------------------ 注册
    def register(self, action: str, callback: Callable[[], None],
                 key: str | None = None) -> None:
        """注册动作回调；key 为空时使用默认热键"""
        self._callbacks[action] = callback
        if key:
            self._bindings[action] = key.lower()
        self._rebuild_reverse()

    def set_binding(self, action: str, key: str) -> bool:
        """修改某动作的热键；返回是否成功（冲突或未知键返回 False）"""
        key = key.lower().strip()
        if key not in HOTKEY_MAP:
            return False
        # 冲突检测：同一按键不能绑两个动作
        for act, k in self._bindings.items():
            if k == key and act != action:
                return False
        self._bindings[action] = key
        self._rebuild_reverse()
        return True

    def binding_of(self, action: str) -> str:
        return self._bindings.get(action, "")

    def _rebuild_reverse(self) -> None:
        with self._lock:
            self._reverse = {}
            for action, keyname in self._bindings.items():
                k = HOTKEY_MAP.get(keyname)
                if k is not None:
                    self._reverse[k] = action

    # ------------------------------------------------------------ 事件
    def _on_press(self, key) -> None:
        action = self._reverse.get(key)
        if not action:
            return
        # 防连发 + 防双触发：
        #  · X11 下同一次按键会派发两次 press（之间无 release）
        #  · 用户按住不放也会持续 press
        # 用「同键 150ms 内的重复 press 一律忽略」统一处理两者。
        now = time.perf_counter()
        with self._lock:
            last = self._last_fire.get(key, 0.0)
            if (now - last) * 1000 < 150:
                return
            self._last_fire[key] = now
            self._held.add(key)
        cb = self._callbacks.get(action)
        if cb:
            try:
                self._scheduler(0, cb)
            except Exception:
                pass

    def _on_release(self, key) -> None:
        with self._lock:
            self._held.discard(key)

    # ------------------------------------------------------------ 信息
    def status_text(self) -> str:
        if not self.running:
            return self.last_error or "全局热键未启动"
        parts = []
        for act in ("stop_record", "toggle_pause", "abort_play", "toggle_run"):
            key = self._bindings.get(act)
            if key:
                parts.append(f"{key.upper()} {HOTKEY_LABELS[act]}")
        return " · ".join(parts)

    def available_keys(self) -> list[str]:
        return sorted(HOTKEY_MAP.keys())
