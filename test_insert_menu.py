"""右键插入功能测试：位置插入、索引清理、编辑菜单构建。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")


def main() -> int:
    from tkinter import simpledialog
    import ui.app as app_mod
    from core.script_model import Event

    app = app_mod.MacroApp()
    results: dict[str, object] = {}
    fails: list[str] = []

    def check(name, cond):
        results[name] = bool(cond)
        if not cond:
            fails.append(name)

    # 预置 3 条事件
    for i in range(3):
        app.script.events.append(Event(t=i * 1000, type="delay", dx=100))
    app._refresh_list()

    # 1) 普通插入到末尾
    app._insert_edit("copy")
    check("append_at_end", len(app.script.events) == 4
          and app.script.events[-1].type == "edit")
    check("insert_index_cleared", app._insert_index is None)

    # 2) 右键插入：位置 1 之前
    app._insert_at(1, lambda: app._insert_edit("paste"))
    check("insert_at_pos", app.script.events[1].type == "edit"
          and app.script.events[1].key == "paste")
    check("len_after_insert", len(app.script.events) == 5)
    check("insert_index_cleared2", app._insert_index is None)

    # 3) 插入位置越界保护
    app._insert_index = 999
    app._insert_edit("cut")
    check("oob_clamped", app.script.events[-1].key == "cut"
          and app._insert_index is None)

    # 4) 弹窗类指令（用 monkeypatch 模拟用户输入）→ 在位置 0 插入
    simpledialog.askinteger = lambda *a, **k: 700
    app._insert_at(0, app._cmd_delay)
    check("dialog_insert_pos0", app.script.events[0].type == "delay"
          and app.script.events[0].dx == 700)

    # 5) 用户取消弹窗 → 不插入、索引清理
    simpledialog.askstring = lambda *a, **k: None
    n = len(app.script.events)
    app._insert_at(2, app._cmd_type_text)
    app.update_idletasks()
    check("cancel_no_insert", len(app.script.events) == n)
    check("cancel_index_cleared", app._insert_index is None)

    # 6) 插入菜单构建（4 组，含全部指令）
    m = app._build_insert_menu(2)
    labels = m.entrycget(0, "label") if m.index("end") is not None else ""
    n_entries = m.index("end") + 1
    check("menu_groups", n_entries == 4)
    check("menu_cascades", all(m.type(i) == "cascade" for i in range(n_entries)))

    # 7) 编辑事件（edit 类型弹对话框 → 自动确认）
    from ui.app import _EditKeyDialog
    ev_edit = app.script.events[1]
    d = _EditKeyDialog(app, ["copy", "paste", "cut"], ev_edit.key or "copy")
    app.after(80, lambda: (setattr(d, "result", "cut"), d.destroy()))
    app.wait_window(d)
    check("edit_dialog_result", d.result == "cut")

    # 8) 时间顺序合法性：插入后 t 单调不减
    ts = [e.t for e in app.script.events]
    check("time_monotonic", all(a <= b for a, b in zip(ts, ts[1:])))

    print("RESULTS:", results)
    app.destroy()

    # 回归：全部通过？
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
