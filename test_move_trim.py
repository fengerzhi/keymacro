"""测试移动事件清理：只保留点击前最后 N 个坐标"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.recorder import Recorder
from core.script_model import Event

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

print("=" * 64)
print("移动事件清理测试")
print("=" * 64)

# 模拟一段录制：先大幅滑动，再点击；再滑动，再点击
def build(rec, seq):
    """seq: [(type, x, y, ...)] 直接注入 recorder.events 并跑 _final_trim"""
    rec.events = []
    for item in seq:
        rec.events.append(item)
    rec._final_trim()
    return rec.events

r = Recorder()
r.moves_before_click = 2

# 场景 1：滑动 20 点 → 点击 → 滑动 20 点 → 点击
seq = []
t = 0
for i in range(20):
    seq.append(Event(t=t, type="mousemove", x=i * 10, y=i * 10)); t += 20
seq.append(Event(t=t, type="click", x=190, y=190, button="left")); t += 100
for i in range(20):
    seq.append(Event(t=t, type="mousemove", x=500 + i * 10, y=300 + i * 10)); t += 20
seq.append(Event(t=t, type="click", x=690, y=490, button="left")); t += 100

out = build(r, seq)
moves = [e for e in out if e.type == "mousemove"]
clicks = [e for e in out if e.type == "click"]
check("两次点击都保留", len(clicks) == 2, f"{len(clicks)} 个")
check("移动事件只留 4 条（2 点击 × 2）", len(moves) == 4, f"{len(moves)} 条")
check("保留的是点击前最近的两个点",
      [e.x for e in moves] == [180, 190, 680, 690],
      str([e.x for e in moves]))
print(f"  原始 {len(seq)} 条 → 清理后 {len(out)} 条（省掉 {len(seq)-len(out)} 条）")

# 场景 2：末尾悬停（用户停下才按 F9，没有后续点击）
r2 = Recorder(); r2.moves_before_click = 2
seq2 = [Event(t=0, type="click", x=100, y=100, button="left")]
for i in range(10):
    seq2.append(Event(t=100 + i * 20, type="mousemove", x=200 + i, y=200 + i))
out2 = build(r2, seq2)
check("末尾无点击的悬停被清空",
      len([e for e in out2 if e.type == "mousemove"]) == 0,
      f"{len([e for e in out2 if e.type=='mousemove'])} 条")

# 场景 3：moves_before_click = 0（完全不保留）
r3 = Recorder(); r3.moves_before_click = 0
out3 = build(r3, seq)
check("设为 0 时不留任何移动",
      len([e for e in out3 if e.type == "mousemove"]) == 0)

# 场景 4：设为 3 时各留 3 条
r4 = Recorder(); r4.moves_before_click = 3
out4 = build(r4, seq)
check("设为 3 时留 6 条",
      len([e for e in out4 if e.type == "mousemove"]) == 6,
      f"{len([e for e in out4 if e.type=='mousemove'])} 条")

# 场景 5：鼠标移到某处 → 输入文字（keytype 也应保留前面的轨迹）
r5 = Recorder(); r5.moves_before_click = 2
seq5 = []
for i in range(10):
    seq5.append(Event(t=i * 20, type="mousemove", x=i * 50, y=i * 30))
seq5.append(Event(t=300, type="keytype", text="hello"))
out5 = build(r5, seq5)
m5 = [e for e in out5 if e.type == "mousemove"]
check("输入文字前的轨迹保留 2 条", len(m5) == 2, f"{len(m5)} 条")

# 场景 6：拖拽（drag 是单个事件，前后的移动仍按规则清理）
r6 = Recorder(); r6.moves_before_click = 2
seq6 = []
for i in range(15):
    seq6.append(Event(t=i * 20, type="mousemove", x=i * 20, y=i * 20))
seq6.append(Event(t=400, type="drag", x=280, y=280, x2=400, y2=400, button="left"))
out6 = build(r6, seq6)
check("拖拽前保留 2 条移动",
      len([e for e in out6 if e.type == "mousemove"]) == 2,
      f"{len([e for e in out6 if e.type=='mousemove'])} 条")
check("拖拽事件本身保留", len([e for e in out6 if e.type == "drag"]) == 1)

# 场景 7：不能误删非移动事件
r7 = Recorder(); r7.moves_before_click = 2
seq7 = [Event(t=0, type="edit", key="copy"),
        Event(t=10, type="keytype", text="abc"),
        Event(t=20, type="delay", dx=500),
        Event(t=30, type="comment", text="说明")]
out7 = build(r7, seq7)
check("非移动事件全部保留", len(out7) == 4, f"{len(out7)} 条")

print("=" * 64)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败：", FAIL)
sys.exit(0 if not FAIL else 1)
