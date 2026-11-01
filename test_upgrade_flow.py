"""test_upgrade_flow.py —— 复现「放了整包却提示已是最新」并验证修复

场景：本机 1.3.1，升级文件夹里放新版整包 zip（无 version.txt）。
修复前：manifest 用本地版本顶替 → 提示「已是最新」→ 用户升不了级。
修复后：能读出包内版本 → 正常提示「发现新版本」。
"""
import os, sys, tempfile, zipfile, shutil, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")
os.environ["HOME"] = tempfile.mkdtemp(prefix="upgrade-flow-")

import tkinter as tk
from core import updater
from core.script_model import Script
from ui import app as app_mod

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

tmp = tempfile.mkdtemp(prefix="flow-")
adir = os.path.join(tmp, "updir")
os.makedirs(adir)

# 造一个「完整打包 zip」：新版本 2.5.0，没有 version.txt
pkg = os.path.join(adir, "按键精灵-Windows打包.zip")
with zipfile.ZipFile(pkg, "w") as z:
    z.writestr("main.py", "x")
    z.writestr("core/updater.py", 'APP_VERSION = "2.5.0"\n')

# 假装本机是 1.3.1
updater.APP_VERSION = "1.3.1"      # ui.app 内部 from core import updater，读到的是同一对象

# ---------------------------------------------------------------- 纯逻辑层
m = updater.check_update_dir(adir)
check("文件夹里读出版本 2.5.0（不再退回本地版本）", m["version"] == "2.5.0", m["version"])
check("判定为可升级", updater.is_newer(m["version"], "1.3.1") is True)

# ---------------------------------------------------------------- UI 层
asked = {"yn": [], "info": []}
app_mod.messagebox.askyesno = lambda *a, **k: (asked["yn"].append(a), False)[1]
app_mod.messagebox.showinfo = lambda *a, **k: asked["info"].append(a)
app_mod.messagebox.showerror = lambda *a, **k: asked["info"].append(a)
installed = {"n": 0}
app_mod.MacroApp._install_update = lambda self, *a, **k: installed.__setitem__("n", installed["n"] + 1)

app = app_mod.MacroApp()
app.update()
updater.set_update_dir(adir)


def drive(ms=1200):
    """让主线程真正跑 mainloop —— 子线程的 after() 才会被投递"""
    app.after(ms, app.quit)
    app.mainloop()


app.upgrade_now()
drive()

check("直接走到安装确认（不再卡在「已是最新」）", installed["n"] == 1, installed["n"])
check("没有误报「已经是最新版本」",
      not any("已经是最新版本" in str(x) for x in asked["info"]), asked["info"])

# ---------------------------------------------------------------- 版本未知时询问
asked["yn"].clear()
asked["info"].clear()
pkg2 = os.path.join(adir, "没有任何版本信息的包.zip")
with zipfile.ZipFile(pkg2, "w") as z:
    z.writestr("main.py", "x")
os.remove(pkg)                       # 只留这个无版本包
app.upgrade_now()
drive()

check("版本未知时弹「无法识别版本」询问", len(asked["yn"]) == 1, asked["yn"])
check("拒绝后安装次数未增加", installed["n"] == 1, installed["n"])
check("版本未知时不报「已是最新」",
      not any("已经是最新版本" in str(x) for x in asked["info"]), asked["info"])


# ---------------------------------------------------------------- 静默模式不打扰
asked["yn"].clear()
app.upgrade_now(silent=True)
drive(800)
check("静默检查遇到未知版本不弹窗", len(asked["yn"]) == 0, asked["yn"])

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + "、".join(FAIL))
print("=" * 62)
app.destroy()
sys.exit(1 if FAIL else 0)
