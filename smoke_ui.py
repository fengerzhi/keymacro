"""smoke_ui.py —— 真实环境下启动完整 UI，截图存证"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")

from ui.app import MacroApp
from core.script_model import Script, Event
from core.logger import capture_screen

app = MacroApp()
app.geometry("1240x760")
app.update()

# 塞几个示例事件，让界面不至于空着
app.script = Script(name="示例脚本")
for i, ev in enumerate([
    Event(t=0, type="click", x=400, y=300, button="left"),
    Event(t=600, type="edit", key="select_all"),
    Event(t=900, type="keytype", text="hello world"),
    Event(t=1400, type="edit", key="copy"),
    Event(t=1700, type="edit", key="paste"),
    Event(t=2100, type="loop_start", text="3"),
    Event(t=2300, type="delay", dx=500),
    Event(t=2800, type="loop_end"),
    Event(t=3000, type="click", x=820, y=610, button="right"),
]):
    ev.t = [0, 600, 900, 1400, 1700, 2100, 2300, 2800, 3000][i]
    app.script.events.append(ev)
app._apply_script(app.script, None)

# 造几条日志展示配色
from core.logger import LogEntry
for lv, msg, step in [("info", "开始回放「示例脚本」：9 步，速度 1.0x", None),
                      ("ok", "单击左键 @ (400, 300)", 1),
                      ("ok", "【全选】", 2),
                      ("ok", "输入文本 \"hello world\"", 3),
                      ("warn", "找图未命中：btn_ok.png", 4),
                      ("error", "第 6 步失败：等待 500 ms → 超时", 6)]:
    app._append_log(LogEntry(ts="22:05:1%d.200" % (len(msg) % 10), level=lv,
                             message=msg, step=step))

# 库里放一份示例，让侧栏有内容
try:
    app.library.save_new(app.script, "示例脚本")
    app._refresh_library()
except Exception as e:
    print("库保存跳过:", e)

def shot():
    app.update_idletasks()
    app.update()
    p = capture_screen(tag="ui_smoke")
    print("UI 截图:", p)
    app._on_close()

app.after(900, shot)
app.mainloop()
