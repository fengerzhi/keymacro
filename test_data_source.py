"""数据源变量功能测试：Excel/CSV 加载、{{变量}} 替换、逐轮取行、回放集成。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")

TMP = tempfile.mkdtemp(prefix="macro_data_")


def make_test_files() -> dict[str, str]:
    from openpyxl import Workbook
    paths = {}

    # xlsx：3 行数据
    xlsx = os.path.join(TMP, "客户.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.append(["姓名", "订单号", "金额"])
    ws.append(["张三", "A1001", 250.0])
    ws.append(["李四", "A1002", None])
    ws.append(["王五", "A1003", 88.5])
    wb.save(xlsx)
    paths["xlsx"] = xlsx

    # csv（GBK 编码，模拟国内常见格式）
    csvp = os.path.join(TMP, "客户.csv")
    with open(csvp, "w", encoding="gbk", newline="") as fh:
        fh.write("姓名,订单号,金额\n张三,A1001,250\n李四,A1002,\n王五,A1003,88.5\n")
    paths["csv"] = csvp
    return paths


def main() -> int:
    from core.data_source import (DataTable, load_table, placeholders_in,
                                  substitute, has_placeholder)
    from core.script_model import Event, Script
    from core.player import Player
    results, fails = {}, []

    def check(name, cond):
        results[name] = bool(cond)
        if not cond:
            fails.append(name)

    files = make_test_files()

    # ---- 1) xlsx 加载 ----
    t = load_table(files["xlsx"])
    check("xlsx_headers", t.headers == ["姓名", "订单号", "金额"])
    check("xlsx_rows", t.n_rows == 3)
    check("xlsx_float", t.rows[0][2] == "250")       # 250.0 → "250"
    check("xlsx_empty_cell", t.rows[1][2] == "")

    # ---- 2) csv(GBK) 加载 ----
    t2 = load_table(files["csv"])
    check("csv_gbk", t2.headers == ["姓名", "订单号", "金额"] and t2.n_rows == 3)

    # ---- 3) 变量替换 ----
    vals = {"姓名": "张三", "订单号": "A1001", "__row__": "1"}
    check("subst_basic", substitute("您好 {{姓名}}，订单 {{订单号}} 已发货", vals)
          == "您好 张三，订单 A1001 已发货")
    check("subst_unknown_kept", substitute("hi {{不存在}}", vals) == "hi {{不存在}}")
    check("subst_strict", substitute("hi {{不存在}}", vals, strict=True) == "hi ")
    check("subst_random", substitute("{{随机数}}", vals).isdigit())
    check("subst_rowno", substitute("第{{行号}}行", vals) == "第1行")
    check("detect", placeholders_in("{{a}} {{b}} {{a}}") == ["a", "b", "a"])
    check("has", has_placeholder("{{x}}") and not has_placeholder("plain"))

    # ---- 4) Script 逐轮取行 ----
    sc = Script(name="t", data_file=files["xlsx"], data_mode="per_loop",
                data_start_row=2, loop=5)
    check("use_data", sc.use_data())
    rows = [sc.values_for_lap(i)["姓名"] for i in (1, 2, 3, 4, 5)]
    check("per_loop_seq", rows == ["张三", "李四", "王五", "张三", "李四"])  # 超行循环

    # 起始行 3 → 从李四开始
    sc2 = Script(data_file=files["xlsx"], data_mode="per_loop", data_start_row=3)
    check("start_row3", sc2.values_for_lap(1)["姓名"] == "李四")

    # fixed 模式
    sc3 = Script(data_file=files["xlsx"], data_mode="fixed", data_fixed_row=4)
    check("fixed_row", sc3.values_for_lap(1)["姓名"] == "王五"
          and sc3.values_for_lap(9)["姓名"] == "王五")

    # off 模式
    sc4 = Script(data_file=files["xlsx"], data_mode="off")
    check("off_no_values", sc4.values_for_lap(1) == {})

    # ---- 5) 序列化兼容 ----
    d = sc.to_dict()
    sc_back = Script.from_dict(d)
    check("persist", sc_back.data_mode == "per_loop"
          and sc_back.data_start_row == 2)
    old = Script.from_dict({"name": "旧脚本"})   # 旧格式无数据源字段
    check("backward_compat", old.data_mode == "off" and old.data_file == "")

    # ---- 6) 回放集成：keytype 事件实际替换 ----
    captured: list[str] = []
    pl = Player()
    pl._type_text = lambda text: captured.append(text)      # 拦截实际输入
    sc_loop = Script(name="t", loop=3, data_file=files["csv"],
                     data_mode="per_loop", data_start_row=2)
    sc_loop.events = [Event(t=0, type="keytype",
                            text="您好 {{姓名}}，单号 {{订单号}}")]
    # 手动模拟三轮替换（run_until 是异步线程，会干扰捕获，这里直接驱动 _dispatch）
    captured.clear()
    for lap in (1, 2, 3):
        pl._lap = lap
        pl._dispatch(sc_loop.events[0], sc_loop)
    check("playback_subst", captured == [
        "您好 张三，单号 A1001",
        "您好 李四，单号 A1002",
        "您好 王五，单号 A1003",
    ])

    # 无数据源时原样
    captured.clear()
    sc_plain = Script(loop=1)
    sc_plain.events = [Event(t=0, type="keytype", text="hi {{x}}")]
    pl._dispatch(sc_plain.events[0], sc_plain)
    check("no_data_passthrough", captured == ["hi {{x}}"])

    # ---- 7) UI 设置面板同步 ----
    import ui.app as app_mod
    app = app_mod.MacroApp()
    app.data_var.set(files["xlsx"])
    app.data_mode_var.set("per_loop")
    app.data_start_var.set("2")
    app._sync_settings()
    check("ui_sync", app.script.data_file == files["xlsx"]
          and app.script.data_mode == "per_loop")
    app.update_idletasks()
    check("ui_hint", "3 行数据" in app.data_hint.get())
    app.data_var.set(os.path.join(TMP, "不存在.xlsx"))
    app.update_idletasks()
    check("ui_hint_err", "读取失败" in app.data_hint.get())
    app.destroy()

    # ---- 10) 单元格引用 {{C5}} ----
    check("cell_C3", t.cell("C3") == "")            # C3 = 李四那行金额（空）
    check("cell_C2", t.cell("C2") == "250")         # C2 = 张三金额
    check("cell_A2", t.cell("A2") == "张三")
    check("cell_B4", t.cell("B4") == "A1003")
    check("cell_oob", t.cell("A999") == "")
    check("cell_lowcase", t.cell("b3") == "A1002")
    vals2 = {}
    check("subst_cell", substitute("单价 {{C2}} 元", vals2, table=t) == "单价 250 元")
    check("subst_cell_notable", substitute("x {{C2}}", vals2) == "x {{C2}}")
    # 列名优先于单元格解释（列名恰好像 C5 的情况极少见，按单元格处理）
    check("subst_mixed", substitute("{{A2}} 的单号 {{订单号}}", {"订单号": "X9"}, table=t)
          == "张三 的单号 X9")

    print("RESULTS:", results)
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
