"""test_ui_library.py —— 在 Xvfb 下验证 UI 脚本库与日志面板集成"""
import os, sys, time, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DISPLAY", ":99")

import tkinter as tk
from core.library import ScriptLibrary, app_dir
from core.logger import RunLogger, list_logs, capture_screen
from core.script_model import Script, Event

# 用临时 HOME 隔离，避免污染真实数据
import tempfile
tmp_home = tempfile.mkdtemp(prefix="macro-test-")
os.environ["HOME"] = tmp_home
import importlib
import core.library as cl
importlib.reload(cl)
import core.logger as clog
importlib.reload(clog)

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

print("=" * 62)
print("UI 集成测试（脚本库 + 日志面板）")
print("=" * 62)

# ---------------------------------------------------------------- 构建窗口
from ui.app import MacroApp
app = MacroApp()
app.update()
app.update_idletasks()

check("主窗口构建成功", app.winfo_exists() == 1)
check("脚本库树控件存在", hasattr(app, "lib_tree"))
check("日志文本框存在", hasattr(app, "log_text"))
check("脚本库实例已就绪", app.library is not None)
print(f"  应用数据目录：{app_dir()}")

# ---------------------------------------------------------------- 造数据
app.script = Script(name="打卡签到")
for i, ev in enumerate([
    Event(t=0, type="click", x=400, y=300, button="left"),
    Event(t=500, type="edit", key="select_all"),
    Event(t=800, type="keytype", text="hello"),
    Event(t=1200, type="edit", key="copy"),
    Event(t=1500, type="delay", dx=300),
]):
    ev.t = i * 300
    app.script.events.append(ev)
app._refresh_list()
check("事件列表填充", len(app.tree.get_children()) == 5,
      f"{len(app.tree.get_children())} 行")

# ---------------------------------------------------------------- 保存到库
app.lib_save_as = lambda: None          # 跳过弹窗
app._sync_settings()
meta = app.library.save_new(app.script, "打卡签到")
app.current_filename = meta.filename
app._refresh_library()
check("保存到库：条目已生成", meta.filename.endswith(".json"), meta.filename)
check("库列表已刷新出现条目",
      len(app.lib_tree.get_children()) == 1,
      f"{len(app.lib_tree.get_children())} 条")

# 重名保护
meta2 = app.library.save_new(app.script, "打卡签到")
check("重名自动加序号", meta2.filename != meta.filename, meta2.filename)

# ---------------------------------------------------------------- 打开
app.lib_tree.selection_set(meta.filename)
app._on_lib_select()
check("选中后信息条有内容", "步" in app.lib_info_var.get(),
      app.lib_info_var.get().replace("\n", " | "))

app.script = Script()
app.current_filename = None
app.lib_open()
check("从库打开脚本", len(app.script.events) == 5,
      f"{len(app.script.events)} 条事件")
check("打开后关联条目", app.current_filename == meta.filename)

# ---------------------------------------------------------------- 复制/重命名
d = app.library.duplicate(meta.filename)
check("复制脚本", d is not None and d.name.endswith("副本"), d.name if d else None)
r = app.library.rename(d.filename, "每日打卡")
check("重命名脚本", r is not None and r.name == "每日打卡", r.name if r else None)

# ---------------------------------------------------------------- 搜索
app.lib_search_var.set("每日")
app._refresh_library()
check("搜索过滤生效", len(app.lib_tree.get_children()) == 1,
      f"{len(app.lib_tree.get_children())} 条")
app.lib_search_var.set("")
app._refresh_library()
check("清空搜索恢复全量", len(app.lib_tree.get_children()) == 3,
      f"{len(app.lib_tree.get_children())} 条")

# ---------------------------------------------------------------- 删除
n_before = len(app.lib_tree.get_children())
app.library.delete(r.filename)
app._refresh_library()
check("删除脚本", len(app.lib_tree.get_children()) == n_before - 1,
      f"{len(app.lib_tree.get_children())} 条")
trash = app_dir() / "trash"
check("删除进回收目录", trash.exists() and len(list(trash.glob('*.json'))) >= 1)

# ---------------------------------------------------------------- 日志面板
app.clear_log()
lg = RunLogger(script_name="日志测试",
               on_entry=lambda e: app.after(0, app._append_log, e))
lg.info("开始回放")
lg.ok("单击左键 @ (400,300)", step=1, elapsed_ms=5)
lg.warn("找图未命中", step=2, elapsed_ms=120)
lg.error("第 3 步失败", step=3)
lg.close()
app.update()

content = app.log_text.get("1.0", "end").strip()
lines = [l for l in content.split("\n") if l.strip()]
check("日志面板显示 4 行", len(lines) == 4, f"{len(lines)} 行")
check("日志含 ✓ 成功标记", "✓" in content)
check("日志含 ! 警告标记", "!" in content)
check("日志含 ✗ 失败标记", "✗" in content)
check("日志落盘文件生成", lg.log_path and os.path.exists(lg.log_path),
      os.path.basename(lg.log_path) if lg.log_path else None)

rows = clog.read_log(lg.log_path)
check("JSONL 可回读 4 条", len(rows) == 4, f"{len(rows)} 条")
check("JSONL 含 elapsed_ms", any("elapsed_ms" in r for r in rows))

hist = list_logs()
check("历史日志列表可用", len(hist) >= 1, f"{len(hist)} 份")

# ---------------------------------------------------------------- 截图
shot = capture_screen(tag="测试")
ok = shot is not None and os.path.exists(shot) and os.path.getsize(shot) > 0
check("截图生成成功", ok,
      f"{os.path.getsize(shot)} 字节" if shot else "返回 None")

# ---------------------------------------------------------------- 回放+日志联动
# 回放跑在后台线程，回调通过 after() 排进主循环 —— 必须真正 mainloop 才生效。
# 这里用「派发一个 quit 任务」把主循环跑起来，等回放结束后自动退出。
app.script = Script(name="回放联动")
app.script.speed = 4.0
app.script.events = [Event(t=0, type="click", x=700, y=450, button="left"),
                     Event(t=100, type="edit", key="select_all"),
                     Event(t=100, type="delay", dx=50)]
app._refresh_list()
app.clear_log()

done = {}
orig_on_done = app._on_play_done
app._on_play_done = lambda ok_, msg: (done.update(ok=ok_, msg=msg),
                                      orig_on_done(ok_, msg))

app._hotkey_mode = True
app._really_start_play()

def _watch(n=[0]):
    n[0] += 1
    if done or n[0] > 200:            # 最多等 10 秒
        app.quit()
    else:
        app.after(50, _watch)

app.after(50, _watch)
app.mainloop()                        # 真正跑主循环，让 after 回调生效

check("回放完成回调触发", "ok" in done, done.get("msg"))
replay_log = app.log_text.get("1.0", "end").strip()
lines = [l for l in replay_log.split("\n") if l.strip()]
check("回放日志已实时刷入面板", len(lines) >= 3, f"{len(lines)} 行")
check("回放日志含开始记录", "开始回放" in replay_log)
check("回放日志含步骤记录", "单击左键" in replay_log)
print("  ── 日志面板内容 ──")
for l in lines:
    print("   ", l)

# ---------------------------------------------------------------- 关闭
app._on_close()
print("=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：", FAIL)
print("=" * 62)
sys.exit(0 if not FAIL else 1)
