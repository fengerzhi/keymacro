"""HUD 端到端测试：App 启动 → HUD 录制 → 暂停/恢复 → 停止。

用 pynput 的 XTEST 注入真实事件（同一 X server 下监听器能收到）。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")


def main() -> None:
    from pynput.mouse import Button, Controller as MouseCtl

    import ui.app as app_mod
    app_mod.HUD_ONLY_RECORD = True

    app = app_mod.MacroApp()
    results: dict[str, object] = {}
    mouse = MouseCtl()

    def step2():
        app.update()
        results["hud_visible"] = bool(app.hud is not None and app.hud.winfo_viewable())
        results["hud_state"] = app.hud.state if app.hud else None
        results["main_hidden"] = app.state() == "withdrawn"
        results["excl_count"] = len(app.recorder.exclusion_rects)
        # 注入：移动到屏幕中部并单击两次
        app.after(300, step3)

    def step3():
        w, h = app.winfo_screenwidth(), app.winfo_screenheight()
        mouse.position = (w // 2, h // 2)
        time.sleep(0.15)
        mouse.click(Button.left, 1)
        time.sleep(0.25)
        mouse.position = (w // 2 + 60, h // 2 + 40)
        time.sleep(0.15)
        mouse.click(Button.left, 1)
        app.after(400, step4)

    def step4():
        results["events_after_clicks"] = len(app.recorder.events)
        # 暂停
        app.toggle_pause_recording()
        app.after(100, step5)

    def step5():
        results["paused"] = app.recorder.paused
        results["hud_paused_flag"] = app.hud.paused
        n0 = len(app.recorder.events)
        mouse.click(Button.left, 1)          # 暂停期间的点击不应被记录
        app.after(400, lambda: step6(n0))

    def step6(n0: int):
        results["events_during_pause_same"] = len(app.recorder.events) == n0
        app.toggle_pause_recording()          # 恢复
        app.after(100, step7)

    def step7():
        results["resumed"] = not app.recorder.paused
        n0 = len(app.recorder.events)
        mouse.click(Button.left, 1)
        app.after(400, lambda: step8(n0))

    def step8(n0: int):
        results["event_added_after_resume"] = len(app.recorder.events) > n0
        # 停止录制
        app.stop_recording()
        app.after(200, step9)

    def step9():
        results["hud_hidden_after_stop"] = not bool(app.hud.winfo_viewable())
        results["main_restored"] = app.state() != "withdrawn"
        results["script_events"] = len(app.script.events)
        print("RESULTS:", results)
        app.destroy()

    app.after(600, lambda: (app.start_recording(), app.after(900, step2)))
    app.mainloop()

    ok = (
        results["hud_visible"] is True
        and results["hud_state"] == "recording"
        and results["main_hidden"] is True
        and results["excl_count"] >= 1
        and results["events_after_clicks"] > 0
        and results["paused"] is True
        and results["events_during_pause_same"] is True
        and results["resumed"] is True
        and results["event_added_after_resume"] is True
        and results["hud_hidden_after_stop"] is True
        and results["main_restored"] is True
        and results["script_events"] > 0
    )
    print("E2E:", "PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
