"""player.py —— 脚本回放引擎

用 pynput 的 Controller 发送**真实的系统级输入**（isTrusted=true），
因此可以操作任意软件，包括有防自动化校验的页面。

能力
─────────────────────────────────────────────────────────
· 鼠标：移动 / 左中右键点击 / 双击 / 拖拽 / 滚轮
· 键盘：单键 / 整段文本 / 组合键（Ctrl+C 等，按下顺序与释放逆序精确还原）
· 语义指令：全选 / 复制 / 剪切 / 粘贴 / 删除 / 撤销 / 回车 / 右键菜单
· 控制流：循环块（含嵌套）、条件（如果屏幕有某图）
· 找图点击：内置模板匹配定位后点击中心（numpy 实现，随包提供，无需另装）
· 容错：失败重试、随机延时抖动（防封）、随时中断、修饰键清理
"""

from __future__ import annotations

import os
import platform
import random
import threading
import time
from enum import IntEnum
from typing import Any, Callable

from pynput import keyboard, mouse

from . import image_match, ocr
from .logger import capture_screen
from .script_model import EDIT_ACTIONS, MAC_EDIT_MAP, Event, Script

SYSTEM = platform.system()

# ---------------------------------------------------------------- 键名映射
SPECIAL_KEYS: dict[str, Any] = {
    "space": keyboard.Key.space,
    "enter": keyboard.Key.enter,
    "return": keyboard.Key.enter,
    "tab": keyboard.Key.tab,
    "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "del": keyboard.Key.delete,
    "esc": keyboard.Key.esc,
    "escape": keyboard.Key.esc,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "page_up": keyboard.Key.page_up,
    "page_down": keyboard.Key.page_down,
    "insert": keyboard.Key.insert,
    "ctrl": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt,
    "shift": keyboard.Key.shift,
    "cmd": keyboard.Key.cmd,
    "win": keyboard.Key.cmd,
    "super": keyboard.Key.cmd,
}

BUTTONS = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
}

STOP_KEY = keyboard.Key.f10


class Interrupted(Exception):
    """用户中断回放"""


class Flow(IntEnum):
    """块执行结果（三态）。

    STOP 值与 0 一致，因此老写法 `if not _exec_block(...)` 依然成立，
    便于渐进替换；新代码请显式比较 `== Flow.STOP`。
    """
    STOP = 0        # 被中断（F9 / stop_flag）
    OK = 1          # 正常执行到底
    BREAK = 2       # 遇到「跳出循环」


class Player:
    """脚本回放引擎（线程安全，异步执行）"""

    def __init__(self):
        self._mouse = mouse.Controller()
        self._kbd = keyboard.Controller()
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None
        self._f10_listener: keyboard.Listener | None = None
        self.running = False
        # 最近一次失败信息（供日志/截图使用）
        self.last_failure: tuple[str, str] | None = None
        self.last_image_reason: str = ""      # 找图失败/特殊处理的具体原因
        self.last_image_pos: tuple[int, int] | None = None   # 最近一次找图落点
        self.last_ocr_text: str = ""          # 最近一次识别出的验证码
        self.last_ocr_reason: str = ""        # 识别失败的具体原因
        # 抓网页表格的结果（行列数 / 存到哪 / 失败原因）
        self.last_table_rows: int = 0
        self.last_table_cols: int = 0
        self.last_table_path: str = ""
        self.last_table_reason: str = ""
        # 点击通知：回放时给界面画涟漪用，签名 (x, y, button)
        self.on_action: Callable[[int, int, str], None] | None = None
        self.last_shot: str | None = None
        # 最近一次失败对应的事件对象与 1 起步骤号（供界面定位高亮）
        self.last_fail_event: Event | None = None
        self.last_fail_step: int = 0
        self._lap = 1                       # 当前回放轮次（数据源行取数用）
        self._data_row: int | None = None   # 按数据行数循环时的绝对行（0 起）

    # ============================================================ 对外
    def play(
        self,
        script: Script,
        on_event: Callable[[Event, int], None] | None = None,
        on_done: Callable[[bool, str], None] | None = None,
        on_progress: Callable[[int, int, int], None] | None = None,
        focus_window: Callable[[], Any] | None = None,
        logger: Any = None,
    ) -> None:
        if self.running:
            return
        self._stop_flag.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run,
            args=(script, on_event, on_done, on_progress, focus_window, logger),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()

    # ============================================================ 单步调试
    def step_once(self, ev: Event, script: Script | None = None) -> str:
        """单步执行单条事件（同步），返回结果描述。用于脚本调试。"""
        try:
            r = self._dispatch(ev, script)
            if r is False:
                if ev.type == "ocr" and self.last_ocr_reason:
                    return f"识别失败：{self.last_ocr_reason}"
                return "未命中（找图失败或条件不满足）"
            return "执行成功"
        except Exception as exc:                       # noqa: BLE001
            return f"执行出错：{exc}"

    def run_until(self, script: Script, target_index: int,
                  on_event: Callable[[Event, int], None] | None = None,
                  on_done: Callable[[bool, str], None] | None = None,
                  focus_window: Callable[[], Any] | None = None,
                  logger: Any = None) -> None:
        """从开头执行到指定行（含），用于「执行到此行」调试"""
        if self.running:
            return
        self._stop_flag.clear()
        self.running = True

        def _work():
            ok, msg = True, "已执行到指定行"
            try:
                if focus_window:
                    focus_window()
                    time.sleep(0.3)
                if logger:
                    logger.info(f"调试执行：从第 1 行到第 {target_index + 1} 行")
                speed = max(0.05, float(script.speed or 1.0))
                events = script.active_events
                last_t = 0
                for i, ev in enumerate(events):
                    if self._stop_flag.is_set():
                        raise Interrupted
                    if ev.t > last_t:
                        if self._sleep((ev.t - last_t) / speed):
                            raise Interrupted
                        last_t = ev.t
                    result = self._dispatch_with_retry(ev, script)
                    if logger and ev.type not in ("comment", "loop_end"):
                        level = "ok" if result == "ok" else (
                            "warn" if "未命中" in result else "error")
                        logger.add(level, ev.detail(), step=i + 1,
                                   shot=getattr(self, "last_shot", None)
                                   if level != "ok" else None)
                    if on_event:
                        on_event(ev, 1)
                    if self.script_index_of(ev, events) >= target_index:
                        break
            except Interrupted:
                ok, msg = False, "已中断"
            except Exception as exc:                   # noqa: BLE001
                ok, msg = False, f"出错：{exc}"
                if logger:
                    logger.error(f"调试执行出错：{exc}")
            finally:
                self.running = False
                self._release_all_modifiers()
                if on_done:
                    on_done(ok, msg)

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def script_index_of(ev: Event, events: list[Event]) -> int:
        try:
            return events.index(ev)
        except ValueError:
            return -1

    # ============================================================ 主流程
    def _run(self, script: Script, on_event, on_done, on_progress,
             focus_window, logger=None) -> None:
        self._f10_listener = keyboard.Listener(on_press=self._hotkey)
        self._f10_listener.start()
        ok, msg = True, "回放完成"
        self.last_fail_event = None
        self.last_fail_step = 0
        self.last_failure = None
        try:
            if logger:
                logger.info(f"开始回放「{script.name}」：{len(script.active_events)} 步，"
                            f"速度 {script.speed}x，循环 {script.loop} 次")
            if focus_window:
                focus_window()
                time.sleep(0.3)

            speed = max(0.05, float(script.speed or 1.0))
            total_laps = max(1, int(script.loop or 1))
            events = script.active_events

            # 预编译控制流（loop 块索引映射）
            jumps = self._compile_loops(events)

            for lap in range(1, total_laps + 1):
                self._lap = lap
                if logger and total_laps > 1:
                    logger.info(f"—— 第 {lap}/{total_laps} 轮 ——", lap=lap)
                if self._exec_block(events, jumps, 0, len(events) - 1,
                                    speed, script, on_event, on_progress,
                                    lap, total_laps, logger) == Flow.STOP:
                    raise Interrupted
                if on_progress:
                    on_progress(lap, len(events), len(events))
            if self._stop_flag.is_set():
                raise Interrupted
            if logger:
                logger.ok(f"回放完成，共 {total_laps} 轮")
        except Interrupted:
            ok, msg = False, "已手动中断"
            if logger:
                logger.warn("回放被手动中断")
        except Exception as exc:                       # noqa: BLE001
            ok, msg = False, f"回放出错: {exc}"
            if logger:
                logger.error(f"回放出错：{exc}")
        finally:
            self.running = False
            if self._f10_listener:
                try:
                    self._f10_listener.stop()
                except Exception:
                    pass
                self._f10_listener = None
            self._release_all_modifiers()
            if on_done:
                on_done(ok, msg)

    def _compile_loops(self, events: list[Event]) -> dict[int, int]:
        """配对 loop / if 块，返回跳转表。

        loop_start → loop_end        loop_end → loop_start
        if         → else / if_end   else     → if_end
        if_end     → if
        """
        jumps: dict[int, int] = {}
        stack: list[tuple[str, int, int | None]] = []   # (kind, 起点, else下标)
        for i, e in enumerate(events):
            if e.type == "loop_start":
                stack.append(("loop", i, None))
            elif e.type == "loop_end":
                if stack and stack[-1][0] == "loop":
                    _, start, _ = stack.pop()
                    jumps[start] = i
                    jumps[i] = start
            elif e.type == "if":
                stack.append(("if", i, None))
            elif e.type == "else":
                if stack and stack[-1][0] == "if":
                    _, start, _ = stack.pop()
                    stack.append(("if", start, i))
                    jumps[start] = i            # 条件不成立 → 跳到 else
                    jumps[i] = i                # else 命中后跳到 if_end（下面补）
            elif e.type == "if_end":
                if stack and stack[-1][0] == "if":
                    _, start, else_idx = stack.pop()
                    if else_idx is None:
                        jumps[start] = i        # 无 else：if → if_end
                    else:
                        jumps[else_idx] = i     # else → if_end
                    jumps[i] = start
        return jumps

    def _exec_block(self, events: list[Event], jumps: dict[int, int],
                    start: int, end: int, speed: float, script: Script,
                    on_event, on_progress, lap: int, total_laps: int,
                    logger=None) -> "Flow":
        """执行 [start, end] 区间；返回 Flow.STOP / Flow.OK / Flow.BREAK"""
        last_t = events[start].t if start < len(events) else 0
        i = start
        while i <= end and i < len(events):
            if self._stop_flag.is_set():
                return Flow.STOP
            ev = events[i]

            # ---- 循环块 ----
            if ev.type == "loop_start" and i in jumps:
                end_i = jumps[i]
                times = self._parse_times(ev.text, script)
                by_data = ev.text in ("data", "0") and script.use_data()
                if logger:
                    logger.info(f"进入循环 × {times}"
                                f"{'（按数据行）' if by_data else ''}",
                                step=i + 1, lap=lap)
                first_row = max(2, script.data_start_row) - 2
                for n in range(times):
                    if self._stop_flag.is_set():
                        return Flow.STOP
                    if by_data:
                        self._data_row = first_row + n
                    r = self._exec_block(events, jumps, i + 1, end_i - 1,
                                         speed, script, on_event,
                                         on_progress, lap, total_laps,
                                         logger)
                    if by_data:
                        self._data_row = None
                    if r == Flow.STOP:
                        return Flow.STOP
                    if r == Flow.BREAK:
                        if logger:
                            logger.warn("跳出循环", step=i + 1, lap=lap)
                        break               # 吃掉本层 break，继续后面的步骤
                i = end_i + 1
                continue

            # ---- 条件块：如果 / 否则 / 结束判断 ----
            if ev.type == "if":
                hit = self._eval_condition(ev, script, logger, i, lap)
                if hit:
                    i += 1
                else:
                    nxt = jumps.get(i)
                    i = (nxt + 1) if nxt is not None else end + 1
                continue

            if ev.type == "else":
                # 能执行到 else 说明 then 分支已跑完 → 跳到 if_end 之后
                nxt = jumps.get(i)
                i = (nxt + 1) if nxt is not None else end + 1
                continue

            if ev.type == "if_end":
                i += 1
                continue

            if ev.type == "break":
                return Flow.BREAK

            # ---- 旧式条件：屏幕存在某图才继续（不满足跳过后续） ----
            if ev.type == "if_image":
                hit = self._image_exists(ev.text, ev.dx or 0)
                if logger:
                    (logger.ok if hit else logger.warn)(
                        f"条件判断：{'存在' if hit else '不存在'} "
                        f"{(ev.text or '').rsplit('/', 1)[-1]}",
                        step=i + 1, lap=lap)
                if not hit:
                    return Flow.OK          # 条件不满足 → 跳过后续
                i += 1
                continue

            # ---- 时间推进 ----
            gap = max(0, ev.t - last_t)
            if gap:
                if self._sleep(gap / speed, script.jitter_ms):
                    return Flow.STOP
            last_t = ev.t

            # ---- 派发（含日志）----
            t_start = time.perf_counter()
            try:
                result = self._dispatch_with_retry(ev, script)
            except Exception as exc:                   # noqa: BLE001
                if logger:
                    logger.error(f"第 {i+1} 步失败：{ev.detail()} → {exc}",
                                 step=i + 1, lap=lap)
                raise

            if logger and ev.type not in ("comment", "loop_end"):
                elapsed = int((time.perf_counter() - t_start) * 1000)
                if result == "ok" or result.startswith("ok"):
                    msg = ev.detail()
                    pos = getattr(self, "last_image_pos", None)
                    if ev.type == "find_image" and pos:
                        msg += f" → 点({pos[0]},{pos[1]})"
                    if ev.type == "ocr" and self.last_ocr_text:
                        msg += f" → 识别「{self.last_ocr_text}」"
                    if ev.type == "grab_table" and self.last_table_rows:
                        msg += (f" → {self.last_table_rows} 行 × "
                                f"{self.last_table_cols} 列")
                        if self.last_table_path:
                            msg += f" → {self.last_table_path}"
                    logger.add("ok", msg, step=i + 1, lap=lap,
                               elapsed_ms=elapsed)
                    if ev.type == "find_image":
                        note = image_match.describe_last()
                        if note:
                            logger.info(note, step=i + 1, lap=lap)
                else:
                    # 记下失败位置，回放结束后由界面跳到并高亮这一行
                    self.last_fail_event = ev
                    self.last_fail_step = i + 1
                    logger.add("warn", f"{ev.detail()} → {result}",
                               step=i + 1, lap=lap, elapsed_ms=elapsed,
                               shot=getattr(self, "last_shot", None))
            elif result != "ok" and not result.startswith("ok"):
                # 没有日志对象时也要记录，界面才能定位
                self.last_fail_event = ev
                self.last_fail_step = i + 1

            if on_event:
                on_event(ev, lap)
            if on_progress:
                on_progress(lap, i + 1, len(events))
            i += 1
        return Flow.OK

    # ------------------------------------------------------------ 条件求值
    def _eval_condition(self, ev: Event, script: Script,
                        logger=None, idx: int = 0, lap: int = 1) -> bool:
        """计算 if 条件是否成立（图像 / 变量比较）"""
        cond = ev.cond or "var"
        hit = False
        detail = ""
        try:
            if cond == "image":
                hit = self._image_exists(ev.left or "", ev.dx or 0)
                if ev.op == "img_miss":
                    hit = not hit
                detail = ev.cond_text()
            else:
                lv = self._resolve_text(ev.left or "", script)
                rv = self._resolve_text(ev.right or "", script)
                op = ev.op or "eq"
                if op == "gt" or op == "lt":
                    try:
                        a, b = float(lv), float(rv)
                        hit = a > b if op == "gt" else a < b
                    except (TypeError, ValueError):
                        hit = False        # 非数字无法比较，视为不成立
                elif op == "contains":
                    hit = bool(rv) and rv in lv
                elif op == "ne":
                    hit = lv != rv
                else:                      # eq
                    hit = lv == rv
                detail = f"{ev.cond_text()}（实际：{lv!r}）"
        except Exception as exc:           # noqa: BLE001
            if logger:
                logger.warn(f"条件判断出错：{exc}", step=idx + 1, lap=lap)
            return False

        if logger:
            (logger.ok if hit else logger.warn)(
                f"条件判断：{'成立' if hit else '不成立'} {detail}",
                step=idx + 1, lap=lap)
        return hit

    @staticmethod
    def _parse_times(text: str | None, script: Script | None = None) -> int:
        """循环次数：数字 / forever / data（按数据行数）"""
        if text in ("data", "0"):
            if script is not None and script.use_data():
                try:
                    from .data_source import load_table
                    n = load_table(script.data_file).n_rows
                    return max(1, n)
                except Exception:               # noqa: BLE001
                    return 1
            return 1
        if not text or text == "forever":
            return 1
        try:
            return max(1, int(text))
        except ValueError:
            return 1

    def _dispatch_with_retry(self, ev: Event, script: Script) -> str:
        """带重试的事件派发；返回结果描述（ok / 未命中…）"""
        attempts = max(1, (script.retry or 0) + 1)
        last = "ok"
        for k in range(attempts):
            try:
                r = self._dispatch(ev, script)
                if r is False:
                    last = "未命中（找图失败或条件不满足）"
                    if ev.type == "find_image" and getattr(self, "last_image_reason", ""):
                        last = f"未命中（{self.last_image_reason}）"
                    elif ev.type == "grab_table" and self.last_table_reason:
                        last = f"抓取失败（{self.last_table_reason}）"
                    self.last_failure = (ev.type, ev.detail())
                else:
                    return "ok" if k == 0 else f"ok（第 {k+1} 次重试成功）"
            except Exception:
                if k == attempts - 1:
                    raise
                last = "执行异常"
            if k < attempts - 1 and not self._sleep(200, 0):
                break
        # 是找图失败 → 自动截图存证
        if ev.type == "find_image":
            shot = capture_screen(tag="find_image")
            self.last_shot = shot
        return last

    def _hotkey(self, key) -> None:
        if key == STOP_KEY:
            self._stop_flag.set()

    # ============================================================ 休眠
    def _sleep(self, ms: float, jitter: int = 0) -> bool:
        """可中断 sleep；jitter>0 时随机追加抖动。返回 True 表示被打断"""
        if jitter and jitter > 0:
            ms += random.uniform(0, jitter)
        deadline = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < deadline:
            if self._stop_flag.is_set():
                return True
            time.sleep(min(0.01, max(0.0, deadline - time.perf_counter())))
        return False

    # ============================================================ 派发
    def _dispatch(self, ev: Event, script: Script | None = None) -> bool | None:
        if self._stop_flag.is_set():
            return None
        ty = ev.type
        # 应用坐标校准（默认原样返回；仅用户显式校准后才有换算）
        cx, cy = (script.calibrate(ev.x, ev.y) if script else (ev.x, ev.y))

        # ---------------- 鼠标 ----------------
        if ty == "mousemove":
            self._move_to(cx, cy)

        elif ty == "mousedown":
            self._move_to(cx, cy)
            self._mouse.press(BUTTONS.get(ev.button or "left", mouse.Button.left))
            self._notify_action(cx, cy, ev.button or "left")

        elif ty == "mouseup":
            self._move_to(cx, cy)
            self._mouse.release(BUTTONS.get(ev.button or "left", mouse.Button.left))

        elif ty == "click":
            self._move_to(cx, cy)
            self._mouse.click(BUTTONS.get(ev.button or "left", mouse.Button.left))
            self._notify_action(cx, cy, ev.button or "left")

        elif ty == "dblclick":
            self._move_to(cx, cy)
            btn = BUTTONS.get(ev.button or "left", mouse.Button.left)
            self._mouse.click(btn, 2)
            self._notify_action(cx, cy, ev.button or "left")

        elif ty == "drag":
            self._do_drag(ev, script)

        elif ty == "scroll":
            self._move_to(cx, cy)
            self._mouse.scroll(ev.dx or 0, ev.dy or 0)

        # ---------------- 键盘 ----------------
        elif ty == "keydown":
            self._key_press(ev.key)

        elif ty == "keyup":
            self._key_release(ev.key)

        elif ty == "keytype":
            self._type_text(self._resolve_text(ev.text or "", script))

        elif ty == "hotkey":
            self._do_hotkey(ev.keys or [])

        # ---------------- 语义指令 ----------------
        elif ty == "edit":
            return self._do_edit(ev)

        # ---------------- 其他 ----------------
        elif ty == "delay":
            self._sleep(ev.dx or 0, 0)

        elif ty == "find_image":
            return self._do_find_image(ev)

        elif ty == "ocr":
            return self._do_ocr(ev)

        elif ty == "grab_table":
            return self._do_grab_table(ev, script)

        elif ty in ("comment", "loop_end"):
            pass                        # 注释与块尾不执行

        return True

    # ---------------- 鼠标细节 ----------------
    def _move_to(self, x: int | None, y: int | None) -> None:
        if x is not None and y is not None:
            try:
                self._mouse.position = (int(x), int(y))
            except Exception:
                pass

    def _notify_action(self, x: int | None, y: int | None,
                       button: str = "left") -> None:
        """告诉界面层「这里点了一下」→ 回放时的涟漪特效

        回调在回放线程里执行，界面层负责切回主线程；这里只保证不因
        回调异常打断回放。
        """
        cb = self.on_action
        if cb is None or x is None or y is None:
            return
        try:
            cb(int(x), int(y), button or "left")
        except Exception:                              # noqa: BLE001
            pass

    def _do_drag(self, ev: Event, script: Script | None = None) -> None:
        sx, sy = ev.x, ev.y
        ex, ey = ev.x2, ev.y2
        if None in (sx, sy, ex, ey):
            return
        if script:
            sx, sy = script.calibrate(sx, sy)
            ex, ey = script.calibrate(ex, ey)
        btn = BUTTONS.get(ev.button or "left", mouse.Button.left)
        self._move_to(sx, sy)
        self._mouse.press(btn)
        self._notify_action(sx, sy, ev.button or "left")
        self._sleep(40)
        # 分段移动，模拟真实拖拽轨迹
        steps = 12
        for i in range(1, steps + 1):
            if self._stop_flag.is_set():
                break
            nx = int(sx + (ex - sx) * i / steps)
            ny = int(sy + (ey - sy) * i / steps)
            self._move_to(nx, ny)
            self._sleep(12)
        self._mouse.release(btn)

    # ---------------- 键盘细节 ----------------
    def _resolve_key(self, name: str | None):
        if not name:
            return None
        key = SPECIAL_KEYS.get(name.lower())
        if key is not None:
            return key
        if len(name) == 1:
            return name
        if name.startswith("vk_"):
            try:
                return keyboard.KeyCode.from_vk(int(name[3:]))
            except Exception:
                return None
        return None

    def _key_press(self, name: str | None) -> None:
        k = self._resolve_key(name)
        if k is not None:
            try:
                self._kbd.press(k)
            except Exception:
                pass

    def _key_release(self, name: str | None) -> None:
        k = self._resolve_key(name)
        if k is not None:
            try:
                self._kbd.release(k)
            except Exception:
                pass

    def _resolve_text(self, text: str, script: Script | None) -> str:
        """把 {{变量}} 替换成数据源对应值（无数据源时原样返回）"""
        if not text or "{{" not in text or script is None:
            return text
        if not script.use_data():
            return text
        try:
            from .data_source import load_table, substitute
            table = load_table(script.data_file)
            if self._data_row is not None:
                # 「按数据行数循环」：直接用绝对行号，用完即为空（不回绕）
                vals = (table.row_dict(self._data_row)
                        if 0 <= self._data_row < table.n_rows else {})
                if vals:
                    vals["__row__"] = str(self._data_row + 1)
            else:
                vals = script.values_for_lap(self._lap, table)
            return substitute(text, vals, table=table)
        except Exception:
            return text                     # 数据源出错不打断回放

    def _type_text(self, text: str) -> None:
        """逐字符输入；换行 → Enter，制表 → Tab

        含中文等非 ASCII 字符时键盘无法直接注入，自动改走
        剪贴板 + Ctrl+V 粘贴（用后恢复剪贴板原内容）。
        """
        if text and any(ord(ch) > 127 for ch in text):
            if self._paste_text(text):
                return
            # 粘贴失败（无剪贴板权限等）则退回逐字符，能输多少输多少
        for ch in text:
            if self._stop_flag.is_set():
                return
            if ch == "\n":
                self._tap(keyboard.Key.enter)
            elif ch == "\r":
                continue
            elif ch == "\t":
                self._tap(keyboard.Key.tab)
            else:
                try:
                    self._kbd.type(ch)
                except Exception:
                    pass                # 无法输出的字符（emoji 等）跳过
            time.sleep(0.008)

    # 剪贴板读写统一放在 core/clipboard.py（抓网页表格也要用同一份实现，
    # 两处各写一遍迟早会漂移）。这里保留同名方法，内部直接转调。
    def _clip_get(self) -> str:
        """读剪贴板里的文本（读不到返回空串）"""
        from . import clipboard
        return clipboard.get_text()

    def _clip_set(self, text: str) -> bool:
        """把文本写进剪贴板（成功返回 True）"""
        from . import clipboard
        return clipboard.set_text(text)

    def _paste_text(self, text: str, restore: bool = True) -> bool:
        """把 text 放进剪贴板后 Ctrl+V 粘贴；成功返回 True

        restore=True 时粘完把剪贴板还原成原来的内容（原来没有文本就留空）。
        粘贴而不是逐字符输入：中文输入法开着时，逐字输入会被输入法
        转成候选字，验证码就变样了。
        """
        prev = self._clip_get() if restore else ""
        if not self._clip_set(text):
            return False
        try:
            self._kbd.press(keyboard.Key.ctrl)
            self._kbd.press("v")
            self._kbd.release("v")
            self._kbd.release(keyboard.Key.ctrl)
            time.sleep(0.12)
            return True
        except Exception:
            return False
        finally:
            if restore and prev:
                self._clip_set(prev)

    def _tap(self, key) -> None:
        try:
            self._kbd.press(key)
            self._kbd.release(key)
        except Exception:
            pass

    def _do_hotkey(self, keys: list[str]) -> None:
        """组合键：按顺序按下，逆序释放"""
        if not keys:
            return
        resolved = [self._resolve_key(k) for k in keys]
        resolved = [k for k in resolved if k is not None]
        if not resolved:
            return
        pressed: list[Any] = []
        try:
            for k in resolved:
                self._kbd.press(k)
                pressed.append(k)
                time.sleep(0.012)
        finally:
            for k in reversed(pressed):
                try:
                    self._kbd.release(k)
                except Exception:
                    pass
                time.sleep(0.008)

    # ---------------- 语义指令 ----------------
    def _do_edit(self, ev: Event) -> bool | None:
        action = ev.key or ""
        info = EDIT_ACTIONS.get(action)
        if not info:
            return None

        # 右键菜单：移动到坐标后右键
        if action == "right_click":
            if ev.x is not None and ev.y is not None:
                self._move_to(ev.x, ev.y)
                self._mouse.click(mouse.Button.right)
            else:
                self._mouse.click(mouse.Button.right)
            return True

        # macOS 上 Ctrl 系列换成 Cmd
        keys = list(info["hotkey"])
        if SYSTEM == "Darwin" and action in MAC_EDIT_MAP:
            keys = list(MAC_EDIT_MAP[action])

        if len(keys) == 1:
            self._tap(self._resolve_key(keys[0]) or keys[0])
        else:
            self._do_hotkey(keys)
        return True

    # ---------------- 找图 ----------------
    def image_engine_available(self) -> bool:
        """找图能力是否可用（内置实现，随包提供，无需另装依赖）"""
        return image_match.engine_available()

    def _find_image(self, path: str | None, confidence: float,
                    region: tuple[int, int, int, int] | None = None):
        """在屏幕上找图，返回中心坐标 (x, y) 或 None

        用内置的 numpy 模板匹配（见 core/image_match.py），
        坐标与录制/回放一致，都是物理像素。
        region 为 (x, y, 宽, 高) 时只在该矩形内匹配（如任务栏条带）。
        """
        if not path or not os.path.exists(path):
            return None
        if not image_match.engine_available():
            return None
        return image_match.locate_on_screen(path, confidence or 90,
                                            region=region)

    def _image_exists(self, path: str | None, timeout_ms: int) -> bool:
        if not path:
            return True
        deadline = time.perf_counter() + max(0, timeout_ms) / 1000.0
        while True:
            if self._find_image(path, 90):
                return True
            if time.perf_counter() >= deadline or self._stop_flag.is_set():
                return False
            self._sleep(150, 0)

    def _do_find_image(self, ev: Event) -> bool:
        pos = self._find_image(ev.text, ev.dx or 90, ev.region())
        if not pos:
            self.last_image_pos = None
            self.last_image_reason = (image_match.describe_last()
                                      or "屏幕上没找到这张图")
            self.last_failure = ("find_image",
                                 (ev.text or "").rsplit("/", 1)[-1])
            return False                # 触发重试
        self.last_image_pos = pos
        self.last_image_reason = ""
        self._move_to(*pos)
        time.sleep(0.05)
        self._mouse.click(mouse.Button.left)
        self._notify_action(pos[0], pos[1], "left")
        return True

    def _do_ocr(self, ev: Event) -> bool:
        """识别验证码并自动填写

        1. 截取 ev.region() 指定的屏幕区域
        2. 交给本机 Umi-OCR（默认 127.0.0.1:1224）识别
        3. 清洗成能直接输入的验证码
        4. 有 x/y 就先点一下输入框再输入，没有就输入到当前焦点
        """
        self.last_ocr_text = ""
        code_text, err = ocr.recognize_region(
            ev.region(), endpoint=ev.text or "")
        if not code_text:
            self.last_ocr_reason = err or "没有识别出验证码"
            self.last_failure = ("ocr", ev.note or "")
            return False                        # 触发重试

        self.last_ocr_reason = ""
        if ev.x is not None and ev.y is not None:
            self._move_to(ev.x, ev.y)
            self._sleep(60)
            self._mouse.click(mouse.Button.left)
            self._notify_action(ev.x, ev.y, "left")
            self._sleep(120)
        # 用粘贴而不是逐字输入：输入法（尤其屏幕输入法）开着时，
        # 逐字敲字母会被转成候选汉字，验证码就填歪了
        if not self._paste_text(code_text):
            self._type_text(code_text)          # 剪贴板不可用时退回逐字
        self.last_ocr_text = code_text
        return True

    def _do_grab_table(self, ev: Event, script: Script | None = None) -> bool:
        """抓取网页表格（合同/订单/客户列表这类）

          1. auto 模式：先给前台窗口发 Ctrl+A / Ctrl+C（浏览器自己把表格
             转成制表符分隔的文本放进剪贴板，登录态、JS 渲染都不用管）
          2. 从剪贴板里挑出最大的表格块，解析成表头 + 数据行
          3. ev.text 有值就存成 CSV（utf-8-sig，Excel 直接双击能开）

        失败返回 False 触发重试：抓不到表格多半是页面还没加载完。
        """
        from . import web_table

        self.last_table_rows = 0
        self.last_table_cols = 0
        self.last_table_path = ""
        self.last_table_reason = ""

        if (ev.key or "auto").lower() == "auto":
            # 先留出一点时间给页面渲染/滚动到位，再去选中复制
            self._sleep(ev.dx if ev.dx is not None else 600, 0)
            if not web_table.send_copy(controller=self._kbd):
                self.last_table_reason = "没能给网页发送 Ctrl+A/Ctrl+C（键盘注入失败）"
                self.last_failure = ("grab_table", ev.detail())
                return False
            self._sleep(300, 0)             # 等浏览器把内容写进剪贴板

        table, _raw, err = web_table.grab_from_clipboard()
        if table is None:
            self.last_table_reason = err
            self.last_failure = ("grab_table", ev.detail())
            return False

        self.last_table_rows = table.n_rows
        self.last_table_cols = len(table.headers)
        path = self._resolve_text((ev.text or "").strip(), script)
        if path:
            try:
                self.last_table_path = str(web_table.save_csv(table, path))
            except Exception as exc:                 # noqa: BLE001
                self.last_table_reason = f"保存 CSV 失败：{exc}"
                return False
        return True

    # ---------------- 清理 ----------------
    def _release_all_modifiers(self) -> None:
        for k in (keyboard.Key.ctrl, keyboard.Key.alt,
                  keyboard.Key.shift, keyboard.Key.cmd):
            try:
                self._kbd.release(k)
            except Exception:
                pass
