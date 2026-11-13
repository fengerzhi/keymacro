"""test_web_table.py —— 网页表格抓取：文本解析 / 表头归一 / CSV 落盘"""
import os
import tempfile
from pathlib import Path

from core import web_table
from core.data_source import load_table

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")


# ---------------------------------------------------------------- 样例
# 真实场景：Ctrl+A 复制整页，表格前后都有导航、筛选栏、分页
PAGE = "\n".join([
    "首页 > 合同管理 > 全部合同",
    "合同类型\t付款方式\t合同状态",
    "\t工程名称\t合同号\t下单时间\t客户名称\t合同总额",
    "\t兴安盟蒙医院\tB26035541214010001\t2026-11-13\t内蒙古达亿安全设备有限公司\t¥ 2 384.8",
    "\t嘉宇化工\tB26035520637370001\t2026-11-13\t内蒙古达亿安全设备有限公司\t¥ 861.45",
    "\tmp3分配程\tS26111357407\t2026-11-13\t内蒙古达亿安全设备有限公司\t¥ 1705",
    "",
    "上一页 1 2 3 下一页",
])

t = web_table.parse_text_table(PAGE)
check("整页文本里能找到表格", t is not None)
check("跳过筛选栏，表头取正表", t.headers[:5] == ["列1", "工程名称", "合同号",
                                                 "下单时间", "客户名称"],
      t.headers[:5])
check("数据 3 行（导航/分页没混进来）", t.n_rows == 3, t.n_rows)
check("首列复选框留空", t.rows[0][0] == "", t.rows[0])
check("金额原样保留", t.rows[0][5] == "¥ 2 384.8", t.rows[0][5])
check("列数对齐", all(len(r) == len(t.headers) for r in t.rows),
      [len(r) for r in t.rows])

# ---------------------------------------------------------------- 手动只选表格
SELECTION = "\t工程名称\t合同号\n\t甲工程\tA001\n\t乙工程\tA002"
t2 = web_table.parse_text_table(SELECTION)
check("只选表格也能解析", t2 is not None and t2.n_rows == 2)
check("空列名自动补「列N」", t2.headers[0] == "列1", t2.headers)

# ---------------------------------------------------------------- 无表头
t3 = web_table.parse_text_table("a\tb\tc\nd\te\tf", has_header=False)
check("无表头时自动命名列", t3.headers == ["列1", "列2", "列3"], t3.headers)
check("无表头时 2 行都算数据", t3.n_rows == 2, t3.n_rows)
check("无表头数据不错位", t3.rows[0] == ["a", "b", "c"], t3.rows[0])

# ---------------------------------------------------------------- 重名列 / 缺列
t4 = web_table.parse_text_table("金额\t金额\t备注\n1\t2\t\n3\t4\t补充", )
check("重名列自动加序号", t4.headers[0] == "金额" and t4.headers[1] == "金额(2)",
      t4.headers)
check("列数不齐的行被补齐", len(t4.rows[0]) == len(t4.headers), t4.rows[0])

# ---------------------------------------------------------------- 杂项文本
check("没有任何表格时返回 None", web_table.parse_text_table("只是普通一句话") is None)
check("空文本返回 None", web_table.parse_text_table("") is None)

# ---------------------------------------------------------------- 只复制一行
one = web_table.parse_text_table("兴安盟蒙医院\tB2603\t2026-11-13\t达亿")
check("只复制一行也能拿到数据", one is not None and one.n_rows == 1,
      one and one.rows)
check("单行时用列1..N", one.headers[0] == "列1", one and one.headers)

# ---------------------------------------------------------------- CSV 落盘
tmp = Path(tempfile.mkdtemp(prefix="web-table-"))
p = web_table.save_csv(t, tmp / "网页表格")
check("保存自动补 .csv 后缀", p.suffix == ".csv", p.name)
back = load_table(str(p))
check("存下来能被数据源读回", back.n_rows == t.n_rows and
      back.headers == t.headers, (back.headers[:3], back.n_rows))
check("值逐格一致", back.rows[0] == t.rows[0], back.rows[0])
check("中文不乱码（utf-8-sig）", back.rows[0][1] == "兴安盟蒙医院", back.rows[0][1])

# ---------------------------------------------------------------- 预览
prev = web_table.preview(t, max_rows=2, max_cols=4)
check("预览有表头", "工程名称" in prev)
check("预览按行截断", "还有 1 行" in prev, prev)


# ---------------------------------------------------------------- 自动复制
class FakeKbd:
    def __init__(self):
        self.log = []

    def press(self, k):
        self.log.append(("press", k))

    def release(self, k):
        self.log.append(("release", k))


kb = FakeKbd()
ok = web_table.send_copy(controller=kb, wait=0)
keys = [k for act, k in kb.log if act == "press"]
check("自动复制发了 Ctrl 和 a/c", ok and "a" in keys and "c" in keys, keys)
check("按键成对释放", len([1 for a, _ in kb.log if a == "release"]) ==
      len([1 for a, _ in kb.log if a == "press"]), kb.log)

print("\n" + "=" * 50)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败：" + "、".join(FAIL))
raise SystemExit(1 if FAIL else 0)
