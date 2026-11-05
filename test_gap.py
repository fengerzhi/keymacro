"""test_gap.py —— 验证「间隔」列与级联改时间（Xvfb）"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")

os.environ["HOME"] = tempfile.mkdtemp(prefix="macro-gap-test-")

import tkinter as tk
import core.library as cl
from pathlib import Path
cl.app_dir = lambda: Path(os.environ["HOME"]) / ".macro-sprite"
# 脚本库现在落在「安装目录/脚本」：测试里指到临时目录，别污染项目根
cl.install_dir = lambda: Path(os.environ["HOME"]) / "install"

from core.script_model import Script, Event
from ui import app as app_mod

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

# mock 输入框
_answers = {}
def fake_askinteger(title, prompt, **kw):
    return _answers.get("value")
app_mod.simpledialog.askinteger = fake_askinteger

app = app_mod.MacroApp()
app.update()

# ---------------------------------------------------------------- 造数据
evs = []
for t in (0, 80, 300, 1500, 1640, 2100):
    evs.append(Event(t=t, type="mousemove", x=10, y=20))
app.script = Script(events=evs)
app._refresh_list()
app.update()

check("存在 gap 列", "gap" in app.tree["columns"], app.tree["columns"])
check("gap 列标题为「间隔」", app.tree.heading("gap")["text"] == "间隔")
check("列顺序：序号/时间/间隔/动作",
      list(app.tree["columns"]) == ["idx", "time", "gap", "action"])

rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
check("第 1 行间隔 = 0.00s", rows[0][2] == "0.00s", rows[0])
check("第 2 行间隔 = 0.08s", rows[1][2] == "0.08s", rows[1])
check("第 3 行间隔 = 0.22s", rows[2][2] == "0.22s", rows[2])
check("第 4 行间隔 = 1.20s", rows[3][2] == "1.20s", rows[3])

# ---------------------------------------------------------------- 改间隔级联
gaps_before = [app._gap_of(i) for i in range(6)]
app.tree.selection_set("2")          # 第 3 行，原间隔 220ms
app._answers = None
_answers["value"] = 500
app.edit_gap()
app.update()

check("改间隔后第 3 行间隔 = 500ms", app._gap_of(2) == 500, app._gap_of(2))
check("改间隔后第 3 行时间 = 300+280 = 580ms", app.script.events[2].t == 580,
      app.script.events[2].t)
check("后续行时间整体 +280ms", [e.t for e in app.script.events] == [0, 80, 580, 1780, 1920, 2380],
      [e.t for e in app.script.events])
check("后续行间隔保持不变",
      [app._gap_of(i) for i in (3, 4, 5)] == gaps_before[3:], gaps_before[3:])
check("表格同步刷新", app.tree.item("2", "values")[2] == "0.50s",
      app.tree.item("2", "values"))

# ---------------------------------------------------------------- 改时间级联
app.tree.selection_set("4")          # 第 5 行，2000... 实为 2140
before = [e.t for e in app.script.events]
_answers["value"] = 3000
app.edit_time()
app.update()
after = [e.t for e in app.script.events]
check("改时间后第 5 行 = 3000ms", after[4] == 3000, after[4])
check("前 4 行不受影响", after[:4] == before[:4], before[:4])
check("后面的行同步平移", after[5] - before[5] == 3000 - before[4], (before, after))

# ---------------------------------------------------------------- 统一间隔
app.tree.selection_set(("1", "3"))       # iid = 索引，选第 2、4 行
_answers["value"] = 100
before_gaps = [app._gap_of(i) for i in range(6)]
app.set_gap_batch()
app.update()
after_gaps = [app._gap_of(i) for i in range(6)]
check("统一间隔：选中的第 2、4 行 = 100ms",
      after_gaps[1] == 100 and after_gaps[3] == 100, after_gaps)
check("统一间隔：未选中行保持原间隔",
      [after_gaps[0], after_gaps[2], after_gaps[4], after_gaps[5]]
      == [before_gaps[0], before_gaps[2], before_gaps[4], before_gaps[5]],
      (before_gaps, after_gaps))
check("统一间隔后时间轴单调且总时长正确",
      [e.t for e in app.script.events]
      == [0, 100, 100 + before_gaps[2],
          200 + before_gaps[2],
          200 + before_gaps[2] + before_gaps[4],
          200 + before_gaps[2] + before_gaps[4] + before_gaps[5]],
      [e.t for e in app.script.events])

# ---------------------------------------------------------------- 拖动排序
# 期望：被拖的行带着自己的间隔走，落位后整表时间按间隔重新累加
app.script = Script(events=[
    Event(t=t, type="mousemove", x=100 + i, y=20)
    for i, t in enumerate((0, 500, 600, 700))])
app._refresh_list()
app.update()
app._move_event(1, 0)          # 第 2 行（间隔 500ms）拖到最前
app.update()

check("拖动后顺序：第 2 行排到最前",
      [e.x for e in app.script.events] == [101, 100, 102, 103],
      [e.x for e in app.script.events])
check("被拖的行保留自己的间隔（500ms）", app._gap_of(0) == 500, app._gap_of(0))
check("时间按间隔重新累加",
      [e.t for e in app.script.events] == [500, 500, 600, 700],
      [e.t for e in app.script.events])
check("拖动后表格时间列同步",
      app.tree.item("0", "values")[1] == "0.50s",
      app.tree.item("0", "values"))

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + "、".join(FAIL))
print("=" * 62)
app.destroy()
sys.exit(1 if FAIL else 0)
