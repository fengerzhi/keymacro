"""HUD 冒烟测试：渲染三种状态并截图，验证布局与命中区域。"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DISPLAY", ":99")

import tkinter as tk  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs_hud")
os.makedirs(OUT, exist_ok=True)


def shot(name: str) -> None:
    time.sleep(0.45)
    path = os.path.join(OUT, name)
    subprocess.run(["import", "-window", "root", path],
                   check=False, capture_output=True)
    print("shot:", path, os.path.exists(path))


def main() -> None:
    from ui.hud import MiniBar

    root = tk.Tk()
    root.withdraw()

    hud = MiniBar(root)
    hud.support_window_bg = "#3a4157"

    # 让背景可见（虚拟屏幕是黑底，先铺一层浅灰底窗）
    bg = tk.Toplevel(root)
    bg.overrideredirect(True)
    bg.geometry("1400x900+0+0")
    bg.configure(bg="#3a4157")
    bg.lower()

    results = {}

    # 1) 录制中
    hud.show("recording", events=37, clicks=8, elapsed_ms=12400)
    hud.update_stats(37, 8, 12400)
    shot("01_recording.png")
    results["recording_geoms"] = hud.geometry_rect()

    # 2) 暂停
    hud.set_paused(True)
    shot("02_paused.png")

    # 3) 悬停（模拟进入）
    hud.set_paused(False)
    hud._hover = True
    hud._draw()
    shot("03_hover.png")

    # 4) 更多菜单
    hud._open_menu()
    hud.update_idletasks()
    shot("04_menu.png")
    results["menu_height"] = hud.menu.winfo_reqheight()
    results["window_height"] = hud.winfo_height()
    hud._close_menu()

    # 5) 回放中
    hud.show("playback")
    hud.update_play(2, 5, 18, 43)
    shot("05_playback.png")

    # 6) 空闲
    hud.show("idle")
    shot("06_idle.png")

    # 命中区域自检
    import ui.hud as H
    zones = hud._button_centers()
    results["zones_idle"] = [z for z, _ in zones]
    hud.set_state("recording")
    zones = hud._button_centers()
    results["zones_rec"] = [z for z, _ in zones]
    # 点击落在按钮上？
    cx = dict(zones)["stop"]
    results["hit_stop"] = hud._zone_at(cx, H.BAR_H // 2)
    results["hit_grip"] = hud._zone_at(6, H.BAR_H // 2)
    results["hit_body"] = hud._zone_at(200, H.BAR_H // 2)

    print("RESULTS:", results)

    root.after(400, root.destroy)
    root.mainloop()

    # 简单断言
    assert results["zones_rec"] == ["stop", "pause", "more"], results["zones_rec"]
    assert results["zones_idle"] == ["record", "play", "more"], results["zones_idle"]
    assert results["hit_stop"] == "stop"
    assert results["hit_grip"] == "grip"
    assert results["hit_body"] == "body"
    assert results["menu_height"] > 120, results["menu_height"]
    assert results["window_height"] == 48 + results["menu_height"]
    print("ALL OK")


if __name__ == "__main__":
    main()
