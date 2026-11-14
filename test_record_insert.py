"""test_record_insert.py —— 录制中右键插入指令（找图/验证码/抓网页表格）

覆盖两件事：
  · 右键在录制时只弹菜单，不写进脚本（想记录走菜单里的那一项）
  · 插入的指令必须进 recorder.events，否则按 F9 重建脚本时会丢
  · 插入期间的耗时要从时钟里扣掉，不能变成回放时的干等
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["HOME"] = tempfile.mkdtemp(prefix="rec-ins-")

import core.library as cl                                    # noqa: E402
cl.install_dir = lambda: Path(os.environ["HOME"]) / "install"
cl.app_dir = lambda: Path(os.environ["HOME"]) / ".macro-sprite"

from core.recorder import Recorder                           # noqa: E402
from core.script_model import Event                          # noqa: E402

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra="") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   [{extra}]" if not cond else ""))


# ---------------------------------------------------------------- 录制器
print("\n[1] 右键插桩：被菜单吃掉，不进脚本")
got = []
rec = Recorder(on_right_click=lambda x, y: got.append((x, y)),
               on_event=lambda ev: None)
rec.running = True                     # 不启动真实监听（避免干扰桌面输入）
rec._t0 = time.perf_counter()

right = type("B", (), {"name": "right"})()
rec._on_click(100, 200, right, True)
ck("按下时先不弹（等松手）", got == [], got)
rec._on_click(100, 200, right, False)
ck("松手时才回调，且带坐标", got == [(100, 200)], got)
ck("右键没有写进事件", rec.events == [], rec.events)

left = type("B", (), {"name": "left"})()
rec._on_click(10, 20, left, True)
rec._on_click(10, 20, left, False)
ck("左键照旧记录", len(rec.events) == 1 and rec.events[0].type == "click",
   [e.type for e in rec.events])

# 暂停时也要能弹：暂停只是不记录输入，插指令照样有用
got.clear()
rec.paused = True
rec._on_click(5, 6, right, False)
ck("暂停时也能弹菜单", got == [(5, 6)], got)
rec.paused = False

print("\n[2] inject：指令进录制流")
seen: list[tuple[str, int]] = []
rec2 = Recorder(on_event=lambda ev: seen.append((ev.type, ev.t)))
rec2.running = True
rec2._t0 = time.perf_counter()
ev = Event(t=0, type="find_image", text="tpl.png")
rec2.inject(ev, t=1234)
ck("inject 用指定时间", rec2.events[-1].t == 1234, rec2.events[-1].t)
ck("inject 标记为有效", rec2.events[-1].valid is True)
ck("inject 通知了 UI 预览", seen and seen[-1][0] == "find_image", seen)

ev2 = Event(t=0, type="delay", dx=1000)
rec2.inject(ev2)
ck("不指定时间 → 用当前录制时刻",
   0 <= rec2.events[-1].t <= rec2.duration_ms + 50, rec2.events[-1].t)

print("\n[3] shift_clock：把操作耗时从时钟里扣掉")
rec2.shift_clock(500)
ck("时钟往前拨了 500ms", rec2.duration_ms < 200, rec2.duration_ms)
rec2.shift_clock(0)
ck("拨 0 不报错", True)
rec2.running = False
rec2.shift_clock(1000)
ck("没在录制时拨时钟无副作用", True)

# ---------------------------------------------------------------- UI 流程
print("\n[4] 界面：右键插入走 recorder，落 F9 也不丢")
import tkinter as tk                                        # noqa: E402
from ui import app as A                                     # noqa: E402

win = A.MacroApp()
win.withdraw()
try:
    r = win.recorder
    r.running = True                    # 模拟录制中（不启动真实监听）
    r.paused = False
    r._t0 = time.perf_counter()
    win.script.events = []
    win._rec_ctx = None

    inserted = []

    def fake_cmd():
        """替身：模拟框选完成后 _append 一条找图指令"""
        win._append(Event(t=0, type="find_image", text="tpl.png", dx=90))

    win._cmd_find_image = fake_cmd
    t_before = r.duration_ms
    win._rec_insert("find_image")

    ck("录制器暂停过（框选期间不录输入）", r.paused is False)  # 已自动恢复
    ck("指令进了 recorder（不会被 F9 覆盖）",
       any(e.type == "find_image" for e in r.events),
       [e.type for e in r.events])
    ck("指令没有写进会被重建的界面列表",
       not any(e.type == "find_image" for e in win.script.events),
       [e.type for e in win.script.events])
    inserted.append(r.events[-1])
    ck("时间轴与录制对齐（未超过当前时刻）",
       inserted[0].t <= t_before + 80, (inserted[0].t, t_before))
    ck("插入上下文已清理", win._rec_ctx is None)
    ck("主窗口回到录制时的隐藏状态", win.state() == "withdrawn", win.state())

    # 取消路径：框选取消 → 必须恢复录制，不能把录制卡在暂停态
    r.paused = False
    win._rec_ctx = {"t": r.duration_ms, "paused": True}
    r.pause()
    win._rec_cancel()
    ck("取消插入后录制恢复", r.paused is False)
    ck("取消后上下文清空", win._rec_ctx is None)

    # 「记录这次右键」补回一次右键
    win._right_pos = (321, 654)
    n_before = len(r.events)
    win._rec_record_right_click()
    last = r.events[-1]
    ck("能补记这次右键",
       len(r.events) == n_before + 1 and last.type == "edit"
       and last.key == "right_click" and (last.x, last.y) == (321, 654),
       (last.type, last.key, last.x, last.y))

    # 录制中插入等待
    win._rec_insert_wait(1000)
    ck("录制中插等待也进 recorder",
       any(e.type == "delay" and e.dx == 1000 for e in r.events),
       [(e.type, e.dx) for e in r.events])
finally:
    win.recorder.running = False
    win.destroy()

print("\n" + "=" * 56)
print(f"  通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for n in FAIL:
    print("  ✗ " + n)
print("=" * 56)
raise SystemExit(1 if FAIL else 0)
