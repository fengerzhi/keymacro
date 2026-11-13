"""web_table.py —— 把网页上的表格抓成数据表

为什么不直接请求网址
─────────────────────────────────────────
后台系统（合同、订单、客户列表）基本都是登录后由 JS 渲染的：直接 GET
拿到的 HTML 里没有数据，还要处理登录态、验证码、分页接口签名。而这些
浏览器已经替我们做完了 —— 在网页里选中表格按 Ctrl+C，剪贴板里的纯文本
就是制表符分隔的表格：一行一条记录，一个 tab 一个单元格。

所以这里只做两件事：
  1. 把复制出来的文本里「最像表格」的那一块找出来（整页复制也行）；
  2. 解析成 表头 + 数据行，可直接存成 CSV 当数据源用。
"""

from __future__ import annotations

import csv
import platform
import re
import time
from pathlib import Path

from .data_source import DataTable
from . import clipboard

SYSTEM = platform.system()

# 单元格里的不可见字符（网页复制常带 NBSP / 零宽空格）
_JUNK = re.compile(r"[\u00a0\u2007\u202f\u200b\ufeff]")


def _clean(cell: str) -> str:
    return _JUNK.sub(" ", cell or "").strip()


def _cells(line: str) -> list[str]:
    return [_clean(c) for c in line.split("\t")]


def _is_table_line(cells: list[str]) -> bool:
    """至少两个非空单元格才算表格行（标题、导航这类只有一列的行会被排除）"""
    return sum(1 for c in cells if c) >= 2


def _blocks(lines: list[str]) -> list[list[list[str]]]:
    """把文本切成若干候选表格块（连续、且每行都像表格行）"""
    out: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for line in lines:
        cells = _cells(line)
        if _is_table_line(cells):
            cur.append(cells)
        else:
            if len(cur) >= 2:
                out.append(cur)
            cur = []
    if len(cur) >= 2:
        out.append(cur)
    return out


def _score(block: list[list[str]]) -> tuple[int, int]:
    """先比行数，再比列数（行多说明是主表，不是某个小工具栏）"""
    cols = max((len(r) for r in block), default=0)
    return len(block), cols


def _headers(raw: list[str], width: int) -> list[str]:
    """表头归一：补空列名、去重、裁/补到 width"""
    out: list[str] = []
    for i in range(width):
        name = raw[i] if i < len(raw) else ""
        if not name:
            name = f"列{i + 1}"
        base, n = name, 2
        while name in out:
            name = f"{base}({n})"
            n += 1
        out.append(name)
    return out


def parse_text_table(text: str, has_header: bool = True) -> DataTable | None:
    """从纯文本里挑出最大的表格块并解析；找不到返回 None

    兼容这些真实情况：
      · 整页复制（表格前后还有导航、按钮、页脚文字）
      · 首列是复选框（复制出来是空单元格）
      · 表头有空列名 / 重名列
      · 某些行列数不齐（合并单元格）
      · 只复制了一行（那就没有表头，用「列1、列2…」）
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = _blocks(lines)
    if not blocks:
        # 只复制了一行（或一块不成行的数据）：退而求其次，取列最多的一行当数据
        rows = [_cells(ln) for ln in lines]
        rows = [r for r in rows if sum(1 for c in r if c) >= 3]
        if not rows:
            return None
        blocks = [[max(rows, key=lambda r: sum(1 for c in r if c))]]
        has_header = False
    block = max(blocks, key=_score)

    width = max((len(r) for r in block), default=0)
    if width < 2:
        return None

    head_raw: list[str] = []
    body = block
    if has_header and len(block) >= 2:
        # 表头 = 块里第一条「列数等于最宽」的行。表格上方的筛选栏
        # （合同类型 / 付款方式 / 合同状态…）列数比正表少，会被跳过；
        # 表头以上的行一律当页面杂项丢掉，不进数据。
        head_idx = next((i for i, r in enumerate(block) if len(r) == width), 0)
        head_raw = block[head_idx]
        body = block[head_idx + 1:]

    headers = _headers(head_raw, width)
    rows: list[list[str]] = []
    for r in body:
        cells = (r + [""] * width)[:width]
        if not any(cells):
            continue                      # 整行空（分页条之类）丢掉
        rows.append(cells)
    if not rows:
        return None
    return DataTable(headers=headers, rows=rows)


# ================================================================ 剪贴板
def grab_from_clipboard(has_header: bool = True
                        ) -> tuple[DataTable | None, str, str]:
    """读剪贴板 → 解析表格。返回 (表, 原始文本, 错误说明)"""
    raw = clipboard.get_text()
    if not raw.strip():
        return None, raw, "剪贴板里没有文本，先在网页里选中表格按 Ctrl+C"
    table = parse_text_table(raw, has_header=has_header)
    if table is None or not table.rows:
        return None, raw, ("剪贴板里没找到表格（要有成列的数据）。"
                           "请先用鼠标从表格左上角拖选到右下角，再按 Ctrl+C。")
    return table, raw, ""


def _modifier():
    """复制用的修饰键：Windows/Linux 是 Ctrl，macOS 是 Command"""
    from pynput import keyboard

    return keyboard.Key.cmd if SYSTEM == "Darwin" else keyboard.Key.ctrl


def send_copy(controller=None, select_all: bool = True,
              wait: float = 0.1) -> bool:
    """给当前前台窗口发 Ctrl+A / Ctrl+C

    controller 传 Player 的键盘控制器可以复用；不传就自己建一个。
    返回 False 表示键盘注入不可用（权限/被拦截）。
    """
    try:
        from pynput import keyboard

        c = controller or keyboard.Controller()
        mod = _modifier()
        if select_all:
            c.press(mod)
            c.press("a")
            c.release("a")
            c.release(mod)
            time.sleep(wait)
        c.press(mod)
        c.press("c")
        c.release("c")
        c.release(mod)
        time.sleep(wait)
        return True
    except Exception:
        return False


# ================================================================ 落盘/预览
def save_csv(table: DataTable, path: str | Path) -> Path:
    """存成 CSV（utf-8-sig：Excel 双击打开不乱码）"""
    p = Path(path)
    if p.suffix.lower() != ".csv":
        p = p.with_suffix(".csv")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(table.headers)
        w.writerows(table.rows)
    return p


def preview(table: DataTable, max_rows: int = 8, max_cols: int = 8) -> str:
    """给对话框用的纯文本预览（行/列多时截断，避免一行撑爆）"""
    if not table.headers:
        return "（还没有数据）"
    total_cols = len(table.headers)
    cols = max(1, min(max_cols, total_cols))
    shows = table.rows[:max_rows]

    def width_of(i: int) -> int:
        w = len(str(table.headers[i]))
        for r in shows:
            if i < len(r):
                w = max(w, len(str(r[i])))
        return min(w, 18)

    widths = [width_of(i) for i in range(cols)]

    def fmt(cells: list) -> str:
        out = []
        for i, c in enumerate(list(cells)[:cols]):
            c = str(c)
            if len(c) > widths[i]:
                c = c[:max(0, widths[i] - 1)] + "…"
            out.append(c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt(table.headers)]
    lines.append("─" * min(max(len(lines[0]), 10), 100))
    for r in shows:
        lines.append(fmt(r))
    if len(table.rows) > max_rows:
        lines.append(f"… 还有 {len(table.rows) - max_rows} 行")
    if total_cols > cols:
        lines.append(f"（只显示前 {cols} 列，共 {total_cols} 列）")
    return "\n".join(lines)
