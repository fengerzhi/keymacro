"""data_source.py —— Excel / CSV 数据源 + 变量替换

用途：数据驱动回放。脚本里的键盘输入文本可以写成
    您好 {{姓名}}，您的订单 {{订单号}} 已发货
回放时按「当前轮次 → Excel 行」取数，把 {{列名}} 替换成对应单元格的值。

支持格式：.xlsx / .xls（openpyxl 读，xls 尽力而为）/ .csv / .tsv
第一行固定视为表头（列名）。

模式
─────────────────────────────────────
per_loop   每循环一轮取下一行（最常用：批量发消息/录单）
fixed      固定取某一行
off        不使用数据源
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
CELL_REF = re.compile(r"^[A-Za-z]{1,3}\d+$")       # 如 C5、AB12


def col_to_index(letters: str) -> int:
    """Excel 列字母 → 1 起序号（A=1, B=2, …, AA=27）"""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """'C5' → (列1起序号, Excel行号)"""
    m = re.match(r"^([A-Za-z]{1,3})(\d+)$", ref.strip())
    if not m:
        raise ValueError(f"无效的单元格引用：{ref}")
    return col_to_index(m.group(1)), int(m.group(2))


# ================================================================ 行数据
@dataclass
class DataTable:
    """一张数据表：headers + rows（均为字符串，空单元格为 ""）"""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    source_path: str = ""

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def row_dict(self, row_index: int) -> dict[str, str]:
        """row_index 从 0 起；返回 {列名: 值}"""
        if not (0 <= row_index < len(self.rows)):
            return {}
        return dict(zip(self.headers, self.rows[row_index]))

    def cell(self, ref: str) -> str:
        """按 Excel 坐标取单元格：cell("C5") = C 列第 5 行（含表头行）"""
        col1, row_no = parse_cell_ref(ref)
        data_idx = row_no - 2                    # 第 1 行表头，数据从第 2 行
        if data_idx < 0 or data_idx >= len(self.rows):
            return ""
        col0 = col1 - 1
        r = self.rows[data_idx]
        return r[col0] if col0 < len(r) else ""

    def column_values(self, header: str) -> list[str]:
        try:
            col = self.headers.index(header)
        except ValueError:
            return []
        return [r[col] if col < len(r) else "" for r in self.rows]


# ================================================================ 加载
def load_table(path: str) -> DataTable:
    """从 xlsx / csv / tsv 加载数据表；第一行为表头"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据文件不存在：{path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return _load_excel(path)
    return _load_csv(path)


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))          # 3.0 → "3"
    return str(v).strip()


def _load_excel(path: str) -> DataTable:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("读取 Excel 需要 openpyxl：pip install openpyxl") from exc
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows_raw:
        return DataTable(source_path=path)
    headers = [_cell_str(h) for h in rows_raw[0]]
    rows = [[_cell_str(c) for c in r] for r in rows_raw[1:]]
    # 去掉整行全空的尾巴
    rows = [r for r in rows if any(c for c in r)]
    return DataTable(headers=headers, rows=rows, source_path=path)


def _load_csv(path: str) -> DataTable:
    delim = "\t" if path.lower().endswith(".tsv") else ","
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                reader = list(csv.reader(fh, delimiter=delim))
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise RuntimeError(f"无法识别文件编码：{path}")
    if not reader:
        return DataTable(source_path=path)
    headers = [_cell_str(h) for h in reader[0]]
    rows = [[_cell_str(c) for c in r] for r in reader[1:]]
    rows = [r for r in rows if any(c for c in r)]
    return DataTable(headers=headers, rows=rows, source_path=path)


# ================================================================ 变量替换
def substitute(text: str, values: dict[str, str], *, strict: bool = False,
               table: "DataTable | None" = None) -> str:
    """把 {{列名}} 替换成 values 里的对应值。

    strict=False（默认）：找不到的列名原样保留 {{...}}，便于发现问题。
    strict=True：找不到替换为空串。

    除数据列外，还内置：
      {{行号}}    当前数据行（1 起）
      {{随机数}}   0-999999 随机整数
    """
    if not text or "{{" not in text:
        return text

    import random

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        # {{C5}} 形式：直接取 Excel 指定单元格（需要 table）
        if CELL_REF.match(name):
            if table is not None:
                return table.cell(name)
            return "" if strict else m.group(0)
        if name in values:
            return values[name]
        if name == "行号":
            return values.get("__row__", "")
        if name == "随机数":
            return str(random.randint(0, 999999))
        return "" if strict else m.group(0)

    return PLACEHOLDER.sub(_sub, text)


def has_placeholder(text: str | None) -> bool:
    return bool(text) and "{{" in text


def placeholders_in(text: str | None) -> list[str]:
    """列出文本中用到的所有变量名"""
    if not text:
        return []
    return PLACEHOLDER.findall(text)
