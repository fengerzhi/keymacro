"""test_safety.py —— 数据安全与效率改动的回归测试

覆盖：
  · 崩溃兜底：异常写盘 + 可读格式
  · 撤销 / 重做：增删改、上限、重做失效
  · 未保存标记：修改置位、保存清除
  · 录制：追加偏移、模式询问、备份
  · 列表：搜索过滤、循环折叠
  · 回放：倒计时状态机与取消

运行：DISPLAY=:99 python3 test_safety.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")
os.environ["HOME"] = tempfile.mkdtemp(prefix="macro-safety-test-")

import tkinter as tk
import core.library as cl
from core import crash
from core.script_model import Event, Script
from ui import app as app_mod

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")


def eq(name, got, want):
    check(name, got == want, f"got={got} want={want}")


# ================================================================ 1. 崩溃兜底
print("=" * 62)
print("1. 崩溃兜底")
try:
    raise ValueError("模拟崩溃")
except ValueError:
    path = crash.handle(*sys.exc_info(), popup=False)

check("崩溃日志已落盘", bool(path) and os.path.exists(path), path)
text = Path(path).read_text(encoding="utf-8") if path else ""
check("日志含标题与版本", "崩溃报告" in text and "版本" in text)
check("日志含异常类型与消息", "ValueError" in text and "模拟崩溃" in text)
check("日志含调用栈", "Traceback" in text or "test_safety" in text)
check("KeyboardInterrupt 不写盘", crash.handle(KeyboardInterrupt, None, None, popup=False) == "")

# ================================================================ 2. 应用启动
print("=" * 62)
print("2. 撤销 / 重做")
app = app_mod.MacroApp()
app.update()
app.cfg["countdown"] = 0                      # 后续测试不启用倒计时


def load(events):
    app.script = Script(events=list(events))
    app._undo_stack.clear()
    app._redo_stack.clear()
    app._folded.clear()
    app._filter_text = ""
    app.filter_var.set("")
    app._refresh_list()
    app.update()


load([Event(t=0, type="click", x=1, y=1),
      Event(t=500, type="click", x=2, y=2)])

app.tree.selection_set("0")
app.delete_selected()
app.update()
eq("删除后剩 1 条", len(app.script.events), 1)
check("撤销按钮已可用", str(app.btn_undo["state"]) == "normal")

app.undo()
app.update()
eq("撤销后恢复 2 条", len(app.script.events), 2)

app.redo()
app.update()
eq("重做后又是 1 条", len(app.script.events), 1)

app.undo()
load([Event(t=0, type="click", x=1, y=1)])
app.tree.selection_set("0")
app.add_comment.__self__  # 占位，避免 lint 报未使用
# 新操作应清空重做栈
app._snapshot()
app.script.events.append(Event(t=100, type="click", x=9, y=9))
app._refresh_list()
eq("新修改后重做栈被清空", len(app._redo_stack), 0)

# 撤销上限
load([Event(t=0, type="click", x=1, y=1)])
for i in range(80):
    app._snapshot()
    app.script.events.append(Event(t=100 * (i + 1), type="click", x=i, y=i))
check("撤销栈不超过上限 60", len(app._undo_stack) <= 60, len(app._undo_stack))

# 清空可撤销
load([Event(t=0, type="click", x=1, y=1),
      Event(t=100, type="click", x=2, y=2)])
app_mod.messagebox.askyesno = lambda *a, **kw: True
app.clear_script()
app.update()
eq("清空后 0 条", len(app.script.events), 0)
app.undo()
app.update()
eq("清空可撤销回来", len(app.script.events), 2)

# ================================================================ 3. 未保存标记
print("=" * 62)
print("3. 未保存标记")
load([Event(t=0, type="click", x=1, y=1)])
app._mark_clean()
check("刚载入时无未保存标记", app._dirty is False)
app.tree.selection_set("0")
app.delete_selected()
check("修改后置位 dirty", app._dirty is True)
check("标题出现未保存提示", "未保存" in app.title(), app.title())
app._mark_clean()
check("清除后标题恢复", "未保存" not in app.title(), app.title())

# ================================================================ 4. 录制
print("=" * 62)
print("4. 录制：追加与备份")
load([Event(t=0, type="click", x=1, y=1),
      Event(t=1000, type="click", x=2, y=2)])
app._record_mode = "append"
app._record_offset = app.script.duration_ms + 300
backup = app._backup_before_append()
check("追加前已备份脚本", bool(backup) and os.path.exists(backup), backup)

app.recorder.running = True                    # 伪造录制中
app._on_record_event(Event(t=0, type="click", x=3, y=3))
app._on_record_event(Event(t=200, type="click", x=4, y=4))
app.recorder.running = False
eq("追加事件时间整体后移", [e.t for e in app.script.events],
   [0, 1000, 1300, 1500])
eq("原有事件未被清空", app.script.events[0].t, 0)

app.cfg["record_mode"] = "replace"
eq("配置为 replace 时不再询问", app._ask_record_mode(), "replace")
app.cfg["record_mode"] = "append"
eq("配置为 append 时直接追加", app._ask_record_mode(), "append")
app.cfg["record_mode"] = "ask"
load([])
eq("没有事件时不询问，直接清空重录", app._ask_record_mode(), "replace")

# ================================================================ 5. 搜索与折叠
print("=" * 62)
print("5. 搜索过滤 / 循环折叠")
load([
    Event(t=0, type="loop_start", text="3"),
    Event(t=100, type="click", x=10, y=10),
    Event(t=200, type="keytype", text="hello"),
    Event(t=300, type="click", x=20, y=20),
    Event(t=400, type="loop_end"),
    Event(t=500, type="comment", text="收尾"),
])
eq("初始可见 6 行", len(app.tree.get_children()), 6)

app.filter_var.set("单击")
app._apply_filter()
rows = app.tree.get_children()
eq("过滤后只剩 2 条点击", len(rows), 2)
check("过滤结果都是点击", all("单击" in app.tree.item(i, "values")[3] for i in rows))

app.filter_var.set("hello")
app._apply_filter()
check("能按输入文本搜到", len(app.tree.get_children()) == 1)

app._clear_filter()
app.update()
eq("清除过滤恢复 6 行", len(app.tree.get_children()), 6)

pairs = app._loop_pairs()
eq("循环配对正确", pairs, {0: 4})

app.tree.selection_set("0")
app.toggle_fold()
app.update()
check("折叠集合记录了循环起点", 0 in app._folded, app._folded)
eq("折叠后只剩循环首行 + 循环外的行", len(app.tree.get_children()), 2)
eq("可见行就是第 1 行和第 6 行", list(app.tree.get_children()), ["0", "5"])
row0 = app.tree.item("0", "values")[3]
check("折叠行显示步数摘要", "已折叠" in row0, row0)

app.toggle_fold(0)
app.update()
eq("展开后恢复 6 行", len(app.tree.get_children()), 6)

app.fold_all()
eq("一键折叠全部", len(app._folded), 1)
app.fold_all()
eq("再点一次全部展开", len(app._folded), 0)

# 选中循环体内部的行也能折叠它所在的循环
app.tree.selection_set("2")
app.toggle_fold()
check("选中循环内部也能折叠所属循环", 0 in app._folded, app._folded)
app.fold_all()

# ================================================================ 6. 倒计时
print("=" * 62)
print("6. 回放倒计时")
app.cfg["countdown"] = 3
eq("读取倒计时配置", app._countdown_seconds(), 3)
app.cfg["countdown"] = 99
eq("倒计时上限 10 秒", app._countdown_seconds(), 10)
app.cfg["countdown"] = -5
eq("倒计时下限 0 秒", app._countdown_seconds(), 0)

fired = []
app.cfg["countdown"] = 2
app._start_countdown(lambda: fired.append("go"))
app.update()
check("倒计时期间按钮显示剩余秒数",
      "秒后" in app.btn_play["text"], app.btn_play["text"])
check("倒计时期间还没开始", fired == [])

check("取消成功", app.cancel_countdown() is True)
check("取消后不会执行", fired == [])
check("重复取消返回 False", app.cancel_countdown() is False)

app.cfg["countdown"] = 2
app._start_countdown(lambda: fired.append("go"))
app._cd_left = 0                               # 直接推到终点
app._cd_tick()
check("倒计时结束执行回调", fired == ["go"], fired)
check("结束后按钮文案复原", app.btn_play["text"] == "▶ 回放",
      app.btn_play["text"])

# ================================================================ 7. 配置持久化
print("=" * 62)
print("7. 配置持久化")
cl.save_config({"countdown": 5, "record_mode": "append"})
cfg = cl.load_config()
eq("倒计时已写盘", cfg["countdown"], 5)
eq("录制方式已写盘", cfg["record_mode"], "append")
cl.save_config({"countdown": 1})
eq("单项更新不影响其他键", cl.load_config()["record_mode"], "append")

try:
    app.destroy()
except Exception:
    pass

# ================================================================ 汇总
print("=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  失败：", f)
    sys.exit(1)
print("ALL PASS")
