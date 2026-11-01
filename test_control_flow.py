"""控制流测试：if/else、变量条件、break、按数据行数循环

运行：DISPLAY=:99 python3 test_control_flow.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.player import Flow, Player                 # noqa: E402
from core.script_model import Event, Script         # noqa: E402

R: dict[str, bool] = {}


def ok(name: str, cond: bool) -> None:
    R[name] = bool(cond)


# ---------------------------------------------------------------- 驱动工具
def drive(events: list[Event], script: Script | None = None,
          image_hit: bool = True, extra: dict | None = None):
    """手动跑一遍 _exec_block，返回 (flow, 执行到的事件下标列表)"""
    s = script if script is not None else Script(events=events)
    pl = Player()
    hits: list[int] = []

    def fake_dispatch(ev, sc):
        hits.append(events.index(ev))
        return "ok"

    pl._dispatch_with_retry = fake_dispatch
    pl._image_exists = lambda path, timeout=0: image_hit
    if extra:
        for k, v in extra.items():
            setattr(pl, k, v)
    jumps = pl._compile_loops(events)
    flow = pl._exec_block(events, jumps, 0, len(events) - 1, 1000, s,
                          None, None, 1, 1, None)
    return flow, hits


# ================================================================ 1 兼容性
old = {"t": 0, "type": "click", "x": 10, "y": 20}
e = Event.from_dict(old)
ok("旧JSON无cond字段", e.cond is None and e.type == "click")

e2 = Event.from_dict({"t": 0, "type": "if", "cond": "var",
                      "left": "{{a}}", "op": "eq", "right": "1"})
ok("新字段可反序列化", e2.cond == "var" and e2.op == "eq")

d = Event(t=0, type="if", cond="var", left="{{a}}", op="eq", right="1").to_dict()
ok("to_dict保留新字段", d.get("cond") == "var" and d.get("right") == "1")

clean = Event(t=0, type="click", x=1, y=2).to_dict()
ok("无 cond 时不写入冗余键", "cond" not in clean and "left" not in clean)

# ================================================================ 2 跳转表
ev = [Event(t=0, type="if", cond="var", left="a", op="eq", right="a"),
      Event(t=1, type="click", x=1, y=1),
      Event(t=2, type="else"),
      Event(t=3, type="click", x=9, y=9),
      Event(t=4, type="if_end")]
pl = Player()
j = pl._compile_loops(ev)
ok("if→else", j.get(0) == 2)
ok("else→if_end", j.get(2) == 4)
ok("if_end→if", j.get(4) == 0)

ev_no_else = [Event(t=0, type="if", cond="var"),
              Event(t=1, type="click", x=1, y=1),
              Event(t=2, type="if_end")]
j2 = pl._compile_loops(ev_no_else)
ok("无else时if→if_end", j2.get(0) == 2)

ev_nest = [Event(t=0, type="loop_start", text="2"),
           Event(t=1, type="if", cond="var"),
           Event(t=2, type="click", x=1, y=1),
           Event(t=3, type="if_end"),
           Event(t=4, type="loop_end")]
j3 = pl._compile_loops(ev_nest)
ok("loop嵌套if配对", j3.get(0) == 4 and j3.get(4) == 0 and j3.get(1) == 3)

# ================================================================ 3 分支走向
flow, hits = drive(ev)
ok("if成立走then", hits == [1] and flow == Flow.OK)

ev[0].right = "zzz"
flow, hits = drive(ev)
ok("if不成立走else", hits == [3] and flow == Flow.OK)

ev2 = [Event(t=0, type="if", cond="var", left="a", op="eq", right="zzz"),
       Event(t=1, type="click", x=1, y=1),
       Event(t=2, type="if_end"),
       Event(t=3, type="click", x=5, y=5)]
flow, hits = drive(ev2)
ok("无else时跳过then继续后面", hits == [3])

ev3 = [Event(t=0, type="if", cond="image", left="a.png", op="img_hit"),
       Event(t=1, type="click", x=1, y=1),
       Event(t=2, type="if_end")]
flow, hits = drive(ev3, image_hit=True)
ok("图像条件成立", hits == [1])
flow, hits = drive(ev3, image_hit=False)
ok("图像条件不成立", hits == [])

ev4 = [Event(t=0, type="if", cond="image", left="a.png", op="img_miss"),
       Event(t=1, type="click", x=1, y=1),
       Event(t=2, type="if_end")]
flow, hits = drive(ev4, image_hit=False)
ok("图像不存在条件成立", hits == [1])

# ================================================================ 4 变量比较
CASES = [("eq", "abc", "abc", True), ("eq", "abc", "abd", False),
         ("ne", "abc", "abd", True), ("ne", "abc", "abc", False),
         ("contains", "hello world", "world", True),
         ("contains", "hello", "xyz", False),
         ("gt", "100", "50", True), ("gt", "50", "100", False),
         ("lt", "50", "100", True), ("lt", "100", "50", False),
         ("gt", "abc", "50", False)]
for op, lv, rv, expect in CASES:
    p = Player()
    e = Event(t=0, type="if", cond="var", left=lv, op=op, right=rv)
    ok(f"比较 {op} {lv}/{rv}", p._eval_condition(e, Script()) is expect)

# ================================================================ 5 break
ev5 = [Event(t=0, type="loop_start", text="3"),      # 外层 3 轮
       Event(t=1, type="loop_start", text="5"),      # 内层 5 轮
       Event(t=2, type="click", x=1, y=1),
       Event(t=3, type="break"),
       Event(t=4, type="loop_end"),
       Event(t=5, type="click", x=2, y=2),           # 外层每轮都会到这里
       Event(t=6, type="loop_end")]
flow, hits = drive(ev5)
ok("break只跳内层", hits.count(2) == 3)
ok("break后外层继续", hits.count(5) == 3)

ev6 = [Event(t=0, type="loop_start", text="3"),
       Event(t=1, type="click", x=1, y=1),
       Event(t=2, type="break"),
       Event(t=3, type="loop_end"),
       Event(t=4, type="click", x=9, y=9)]
flow, hits = drive(ev6)
ok("break跳出循环后继续后续", hits == [1, 4] and flow == Flow.OK)

# ================================================================ 6 按数据行数
import csv                                                   # noqa: E402

tmpdir = tempfile.mkdtemp()
csv_path = os.path.join(tmpdir, "data.csv")
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["名称", "金额"])
    for i in range(1, 4):
        w.writerow([f"机构{i}", str(i * 100)])

s = Script(events=[], data_file=csv_path, data_mode="per_loop", data_start_row=2)
ok("_parse_times data 取行数", Player._parse_times("data", s) == 3)
ok("_parse_times 0 取行数", Player._parse_times("0", s) == 3)
ok("_parse_times 数字", Player._parse_times("7", s) == 7)
ok("_parse_times forever", Player._parse_times("forever", s) == 1)
ok("无数据源时 data 回退 1",
   Player._parse_times("data", Script(events=[])) == 1)

# 不回绕：第 4 轮应越界返回 -1
ok("wrap=False 越界返回-1", s.data_row_for_lap(4, wrap=False) == -1)
ok("wrap=True 越界回绕", s.data_row_for_lap(4, wrap=True) == 0)
ok("用尽后变量为空", s.values_for_lap(4, wrap=False) == {})
ok("第1轮取首行", s.values_for_lap(1, wrap=False).get("名称") == "机构1")

# 循环内 data_row 驱动取值
ev7 = [Event(t=0, type="loop_start", text="data"),
       Event(t=9, type="keytype", text="{{名称}}"),
       Event(t=10, type="loop_end")]
s7 = Script(events=ev7, data_file=csv_path, data_mode="per_loop",
            data_start_row=2)
typed: list[str] = []
pl = Player()
pl._type_text = lambda t: typed.append(t)
pl._dispatch_with_retry = lambda ev, sc: (
    pl._type_text(pl._resolve_text(ev.text or "", sc)), "ok")[1]
jumps = pl._compile_loops(ev7)
pl._exec_block(ev7, jumps, 0, len(ev7) - 1, 1000, s7, None, None, 1, 1, None)
ok("按数据行循环各轮取不同行", typed == ["机构1", "机构2", "机构3"])

# ================================================================ 7 文案
ok("if文案", "如果" in Event(t=0, type="if", cond="image",
                           left="C:/a/ok.png", op="img_hit").detail())
ok("else文案", Event(t=0, type="else").detail() == "◇ 否则")
ok("if_end文案", "结束判断" in Event(t=0, type="if_end").detail())
ok("break文案", "跳出循环" in Event(t=0, type="break").detail())
ok("loop data文案", "按数据行" in Event(
    t=0, type="loop_start", text="data").detail())
ok("变量条件文案", ">" in Event(t=0, type="if", cond="var", left="{{金额}}",
                              op="gt", right="100").detail())

# ================================================================ 8 结构校验
def warn(evs):
    return Script(events=evs).validate_structure()


ok("正常结构无警告", warn([
    Event(t=0, type="if", cond="var"), Event(t=1, type="else"),
    Event(t=2, type="if_end")]) == [])
ok("缺结束判断", any("结束判断" in w for w in
                    warn([Event(t=0, type="if", cond="var")])))
ok("孤立否则", any("否则" in w for w in
                  warn([Event(t=0, type="else")])))
ok("孤立结束判断", any("结束判断" in w for w in
                    warn([Event(t=0, type="if_end")])))
ok("循环外break", any("循环" in w for w in
                    warn([Event(t=0, type="break")])))
ok("循环内break合法", warn([
    Event(t=0, type="loop_start", text="2"), Event(t=1, type="break"),
    Event(t=2, type="loop_end")]) == [])
ok("缺循环结束仍报警", any("循环结束" in w for w in
                       warn([Event(t=0, type="loop_start", text="2")])))
ok("多余循环结束仍报警", any("多余" in w for w in
                        warn([Event(t=0, type="loop_end")])))

# ================================================================ 9 UI
os.environ.setdefault("DISPLAY", ":99")
try:
    import tkinter as tk                               # noqa: E402

    import ui.app as app_mod                          # noqa: E402

    app = app_mod.MacroApp()
    app.update()
    defs = app._command_defs()
    ok("指令库仍4组", len(defs) == 4)
    ctrl = dict(defs).get("控制 / 等待 / 找图") or []
    labels = [t for t, _ in ctrl]
    ok("控制组含如果", "如果" in labels)
    ok("控制组含否则", "否则" in labels)
    ok("控制组含结束判断", "结束判断" in labels)
    ok("控制组含跳出循环", "跳出循环" in labels)
    ok("控制组按钮数", len(ctrl) >= 9)

    # 插入 if（模拟对话框输入）
    from tkinter import filedialog, simpledialog
    seq = iter(["2", "{{金额}}", ">", "100"])

    def fake_askstring(*a, **kw):
        return next(seq, None)

    simpledialog.askstring = fake_askstring
    app._cmd_if()
    app.update()
    last = app.script.events[-1]
    ok("插入if事件", last.type == "if" and last.op == "gt"
       and last.left == "{{金额}}" and last.right == "100")
    ok("插入后清空插入位置", app._insert_index is None)

    # 取消路径
    seq2 = iter([])
    simpledialog.askstring = lambda *a, **kw: next(seq2, None)
    n0 = len(app.script.events)
    app._cmd_if()
    ok("取消不插入", len(app.script.events) == n0)

    # 图像条件路径
    filedialog.askopenfilename = lambda *a, **kw: "/tmp/a.png"
    seq3 = iter(["1", "2"])
    simpledialog.askstring = lambda *a, **kw: next(seq3, None)
    app._cmd_if()
    ev_if = app.script.events[-1]
    ok("图像条件img_miss", ev_if.cond == "image" and ev_if.op == "img_miss")

    app._cmd_else()
    app._cmd_if_end()
    app._cmd_break()
    ts = [e.type for e in app.script.events[-3:]]
    ok("插入else/if_end/break", ts == ["else", "if_end", "break"])

    app.destroy()
except Exception as exc:                              # noqa: BLE001
    import traceback
    print("UI 测试异常：\n" + traceback.format_exc())
    ok("UI 测试无异常", False)

# ================================================================ 汇总
print("=" * 60)
fails = [k for k, v in R.items() if not v]
for k, v in R.items():
    if not v:
        print("  ✗", k)
print(f"通过 {len(R) - len(fails)} 项，失败 {len(fails)} 项")
print("=" * 60)
sys.exit(1 if fails else 0)
