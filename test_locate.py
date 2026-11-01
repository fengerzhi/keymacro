"""test_locate.py —— 回放失败定位、找图依赖提示、单文件包存取的界面级测试

运行：DISPLAY=:99 python3 test_locate.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")
os.environ["HOME"] = tempfile.mkdtemp(prefix="macro-locate-test-")

from core.script_model import Event, Script
from core import script_package as sp
from ui import app as app_mod

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")


def eq(name, got, want):
    check(name, got == want, f"got={got!r} want={want!r}")


# 拦截弹窗
asked = {"yes": [], "info": [], "error": [], "warn": []}
_yes_answer = {"v": True}
app_mod.messagebox.askyesno = lambda *a, **k: (asked["yes"].append(a), _yes_answer["v"])[1]
app_mod.messagebox.showinfo = lambda *a, **k: asked["info"].append(a)
app_mod.messagebox.showwarning = lambda *a, **k: asked["warn"].append(a)
app_mod.messagebox.showerror = lambda *a, **k: asked["error"].append(a)
app_mod.messagebox.askyesnocancel = lambda *a, **k: False

app = app_mod.MacroApp()
app.geometry("1320x760+0+0")
app.update()

# ---------------------------------------------------------------- 场景脚本
src = Path(tempfile.mkdtemp()) / "图 目录(1)"
src.mkdir(parents=True)
img = src / "btn.png"
img.write_bytes(b"PNG" * 100)

evs = [Event(t=0, type="mousemove", x=10, y=10),
       Event(t=100, type="click", x=10, y=10, button="left"),
       Event(t=200, type="find_image", text=str(img), dx=90),
       Event(t=300, type="click", x=99, y=99, button="left"),
       Event(t=400, type="keytype", text="hello")]
app.script = Script(name="定位测试", events=evs)
app._refresh_list()
app.update()

print("=" * 62)
print("一、回放失败定位到出错行")
print("=" * 62)

# 模拟 player 报告：第 3 条（索引 2）的 find_image 失败
app.player.last_fail_event = app.script.events[2]
app.player.last_fail_step = 3
note = app._locate_failure()
app.update()

check("返回定位说明", bool(note), note)
check("说明里含步骤号", "第 3 步" in note, note)
eq("选中了出错那一行", app.tree.selection(), ("2",))
check("该行被标上 fail 底色", "fail" in app.tree.item("2", "tags"),
      app.tree.item("2", "tags"))
check("不是出错行没有底色", "fail" not in app.tree.item("0", "tags"),
      app.tree.item("0", "tags"))

print()
print("=" * 62)
print("二、被过滤/折叠挡住时能自动恢复显示")
print("=" * 62)

# 用关键字把出错行过滤掉
app.filter_var.set("hello")
app._apply_filter()
app.update()
visible = [app.tree.item(x, "values")[0] for x in app.tree.get_children()]
check("过滤后只剩 1 行", len(visible) == 1, visible)

note2 = app._locate_failure()
app.update()
check("过滤状态下也能定位", "第 3 步" in note2, note2)
check("定位后自动清除了过滤", app.filter_var.get() == "", app.filter_var.get())
eq("出错行重新可见并选中", app.tree.selection(), ("2",))
eq("列表恢复全部行", len(app.tree.get_children()), 5)

# 折叠：造一个循环块，把出错行藏在里面
app.script = Script(name="折叠", events=[
    Event(t=0, type="loop_start", text="2"),
    Event(t=10, type="click", x=1, y=1, button="left"),
    Event(t=20, type="find_image", text=str(img)),
    Event(t=30, type="loop_end"),
    Event(t=40, type="click", x=2, y=2, button="left"),
])
app._refresh_list()
app._folded = {0}
app._refresh_list()
app.update()
eq("折叠后只剩 2 行", len(app.tree.get_children()), 2)

app.player.last_fail_event = app.script.events[2]
note3 = app._locate_failure()
app.update()
check("折叠状态下也能定位", "第 3 步" in note3, note3)
eq("定位后自动展开", len(app.tree.get_children()), 5)
eq("选中被折叠的行", app.tree.selection(), ("2",))

print()
print("=" * 62)
print("三、找图依赖 / 缺图 提前提示")
print("=" * 62)

asked["yes"].clear()
app.script = Script(name="无图", events=[
    Event(t=0, type="mousemove", x=1, y=1),
    Event(t=10, type="click", x=1, y=1, button="left"),
])
eq("没有找图步骤时不打扰", app._check_image_steps(), True)
eq("没有弹过窗", len(asked["yes"]), 0)

# 有找图步骤，但图片文件不存在
app.script = Script(name="缺图", events=[
    Event(t=0, type="find_image", text=str(src / "不存在.png")),
    Event(t=10, type="click", x=1, y=1, button="left"),
])
asked["yes"].clear()
_check = app.player.image_engine_available()
_yes_answer["v"] = False
got = app._check_image_steps()
check("缺图时弹出确认", len(asked["yes"]) == 1, len(asked["yes"]))
msg = asked["yes"][0][1] if asked["yes"] else ""
check("提示里点明第几步", "第 1 步" in msg, msg)
check("提示里说明数量", "1 张图片" in msg, msg)
eq("用户选「否」→ 返回 False（不开始回放）", got, False)

_yes_answer["v"] = True
eq("用户选「是」→ 允许继续", app._check_image_steps(), True)

# 图片存在但引擎不可用时，提示要含安装命令
_engine_orig = app.player.image_engine_available
app.player.image_engine_available = lambda: False
asked["yes"].clear()
app._check_image_steps()
msg2 = asked["yes"][0][1] if asked["yes"] else ""
check("缺组件时提示重装程序", "重新安装" in msg2, msg2)
check("缺组件时说明会被跳过", "跳过" in msg2, msg2)
app.player.image_engine_available = _engine_orig

print()
print("=" * 62)
print("四、界面保存 / 打开单文件包")
print("=" * 62)

app.script = Script(name="带图脚本", events=[
    Event(t=0, type="find_image", text=str(img), dx=90),
    Event(t=500, type="click", x=5, y=6, button="left"),
])
app._refresh_list()

outdir = Path(tempfile.mkdtemp())
target = outdir / "带图脚本.ajjl"
app_mod.filedialog.asksaveasfilename = lambda **k: str(target)
app.save_script()
app.update()

check("文件已生成", target.is_file(), target)
check("文件是 zip", sp.is_package(target), target)
n, _m = sp.collect(app.script)
eq("打包带走 1 个附件", len(n), 1)

# 打开
app_mod.filedialog.askopenfilename = lambda **k: str(target)
app.script = Script()
app._refresh_list()
app.open_script()
app.update()
eq("打开后事件数正确", len(app.script.events), 2)
check("图片路径已还原且存在",
      Path(app.script.events[0].text).is_file(), app.script.events[0].text)
check("状态栏提示是脚本包",
      "脚本包" in app.status_var.get(), app.status_var.get())

# 纯 json 也能打开
plain = outdir / "老脚本.json"
app.script.save(str(plain))
app_mod.filedialog.askopenfilename = lambda **k: str(plain)
app.open_script()
app.update()
eq("纯 json 也能打开", len(app.script.events), 2)
check("纯 json 不提示脚本包",
      "脚本包" not in app.status_var.get(), app.status_var.get())

# 保存目录记忆
check("记住上次保存目录", str(outdir) in str(app.cfg.get("last_dir", "")),
      app.cfg.get("last_dir"))

print()
print("=" * 62)
if FAIL:
    print(f"✗ {len(FAIL)} 项失败 / 共 {len(PASS) + len(FAIL)} 项")
    for f in FAIL:
        print("   -", f)
else:
    print(f"✓ 全部通过：{len(PASS)} 项")
app.destroy()
