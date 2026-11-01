"""单文件脚本包 / 回放失败定位 / 找图依赖提示 的测试"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="ajjl-test-")
os.environ["HOME"] = _SANDBOX
sys.path.insert(0, "/workspace/ajjl")
os.chdir("/workspace/ajjl")

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}  →  {detail}")
        FAILS.append(name)


def eq(name, got, want):
    check(name, got == want, f"期望 {want!r}，实际 {got!r}")


from core.script_model import Script, Event
from core import script_package as sp

print("=" * 62)
print("一、打包：图片与数据表一起带走")
print("=" * 62)

src = Path(tempfile.mkdtemp()) / "素材 目录(测试)"      # 含空格与括号
src.mkdir(parents=True)
img1 = src / "确定按钮.png"
img1.write_bytes(b"PNG-A" * 200)
img2 = src / "logo.png"
img2.write_bytes(b"PNG-B" * 200)
tab = src / "客户名单.xlsx"
tab.write_bytes(b"XLSX" * 100)

sc = Script(name="演示脚本", events=[
    Event(t=0, type="find_image", text=str(img1), dx=90),
    Event(t=300, type="click", x=10, y=20, button="left"),
    Event(t=600, type="if", cond="image", left=str(img2), op="img_hit"),
    Event(t=900, type="if_end"),
    Event(t=1000, type="find_image", text=str(src / "丢了.png"), dx=90),
    Event(t=1200, type="comment", text="说明"),
])
sc.data_file = str(tab)

out = Path(tempfile.mkdtemp()) / "演示.ajjl"
n_attach, missing = sp.pack(sc, out)

eq("附件数量 = 3（2 张图 + 1 个表）", n_attach, 3)
eq("缺失文件被报告", [Path(m).name for m in missing], ["丢了.png"])
check("包文件已生成", out.is_file(), out)
check("后缀自动补 .ajjl", out.suffix == ".ajjl", out.suffix)

with zipfile.ZipFile(out) as zf:
    names = zf.namelist()
    data = json.loads(zf.read(sp.PKG_SCRIPT).decode("utf-8"))
    meta = json.loads(zf.read(sp.PKG_META).decode("utf-8"))

check("包含 script.json / package.json", sp.PKG_SCRIPT in names and sp.PKG_META in names, names)
eq("附件数（zip 内）", len([x for x in names if x.startswith("attach/")]), 3)
eq("find_image 路径改写为 @attach", data["events"][0]["text"], "@attach/确定按钮.png")
eq("if 条件图路径改写", data["events"][2]["left"], "@attach/logo.png")
eq("data_file 改写", data["data_file"], "@attach/客户名单.xlsx")
eq("缺图保持原绝对路径（不假装打包）", data["events"][4]["text"], str(src / "丢了.png"))
eq("meta 记录附件清单", len(meta["attachments"]), 3)
eq("meta 记录缺失清单", len(meta["missing"]), 1)
eq("meta 格式标识", meta["format"], "ajjl-package")

print()
print("=" * 62)
print("二、解包：路径还原、内容一致、可重复打开")
print("=" * 62)

sc2 = sp.load(str(out))
eq("事件数量一致", len(sc2.events), 6)
eq("脚本名保留", sc2.name, "演示脚本")
eq("第一条时间保留", sc2.events[0].t, 0)

p0 = sc2.events[0].text
check("图片已释放到磁盘", Path(p0).is_file(), p0)
check("图片内容与原图一致", Path(p0).read_bytes() == img1.read_bytes(), p0)
check("第二张图也存在", Path(sc2.events[2].left).is_file())
check("数据表已释放", Path(sc2.data_file).is_file(), sc2.data_file)
check("释放目录在 packages 下",
      "/packages/" in str(Path(p0)).replace("\\", "/"), p0)
check("缺失图仍是原路径（不会被改坏）",
      str(sc2.events[4].text).endswith("丢了.png") and not str(sc2.events[4].text).startswith("@attach"),
      sc2.events[4].text)

sc3 = sp.load(str(out))
eq("重复打开用同一释放目录",
   Path(sc3.events[0].text).parent, Path(p0).parent)

print()
print("=" * 62)
print("三、兼容：纯 JSON 脚本仍可正常读写")
print("=" * 62)

plain = Path(tempfile.mkdtemp()) / "老脚本.json"
sc.save(str(plain))
eq("json 后缀存成纯文本", plain.is_file(), True)
check("是合法 json", isinstance(json.loads(plain.read_text("utf-8")), dict))
eq("纯 json 不算脚本包", sp.is_package(plain), False)
check("ajjl 算脚本包", sp.is_package(out), True)

back = sp.load(str(plain))
eq("纯 json 能读回", len(back.events), 6)

# save() 统一入口按扩展名分派
out2 = Path(tempfile.mkdtemp()) / "再存一次.ajjl"
real, n2, _miss = sp.save(sc, out2)
eq("统一入口按扩展名打包", n2, 3)
check("返回真实路径", Path(real).is_file(), real)

plain2 = Path(tempfile.mkdtemp()) / "再存一次.json"
real2, n3, _ = sp.save(sc, plain2)
eq("统一入口按扩展名存 json", n3, 0)
check("json 已写入", Path(real2).is_file(), real2)

print()
print("=" * 62)
print("四、异常输入不崩")
print("=" * 62)

bad = Path(tempfile.mkdtemp()) / "坏包.ajjl"
bad.write_bytes(b"not a zip at all")
try:
    sp.unpack(str(bad))
    check("坏包应抛错", False, "没有抛异常")
except Exception as exc:                          # noqa: BLE001
    check("坏包抛 ValueError", isinstance(exc, ValueError), type(exc).__name__)

empty = Path(tempfile.mkdtemp()) / "空.ajjl"
with zipfile.ZipFile(empty, "w") as zf:
    zf.writestr("readme.txt", "hi")
try:
    sp.unpack(str(empty))
    check("缺 script.json 应抛错", False, "没有抛异常")
except ValueError as exc:
    check("缺 script.json 抛 ValueError", "script.json" in str(exc), str(exc))

eq("不存在的文件不算包", sp.is_package(Path("/tmp/根本不存在.json")), False)

# 同名不同目录的图片不会互相覆盖
dupdir1 = Path(tempfile.mkdtemp()) / "a"
dupdir2 = Path(tempfile.mkdtemp()) / "b"
dupdir1.mkdir(); dupdir2.mkdir()
(dupdir1 / "btn.png").write_bytes(b"AAA")
(dupdir2 / "btn.png").write_bytes(b"BBB")
scd = Script(name="同名", events=[
    Event(t=0, type="find_image", text=str(dupdir1 / "btn.png")),
    Event(t=10, type="find_image", text=str(dupdir2 / "btn.png")),
])
outd = Path(tempfile.mkdtemp()) / "同名.ajjl"
n_dup, _ = sp.pack(scd, outd)
eq("同名图片各占一份", n_dup, 2)
scd2 = sp.load(str(outd))
check("两张同名图内容各自正确",
      Path(scd2.events[0].text).read_bytes() == b"AAA"
      and Path(scd2.events[1].text).read_bytes() == b"BBB",
      [scd2.events[0].text, scd2.events[1].text])

print()
print("=" * 62)
print("五、回放失败定位所需字段")
print("=" * 62)

import core.player as pl
p = pl.Player()
check("player 有 last_fail_event 字段", hasattr(p, "last_fail_event"), dir(p))
check("player 有 last_fail_step 字段", hasattr(p, "last_fail_step"), dir(p))
eq("初始失败步骤号 0", p.last_fail_step, 0)
check("有 image_engine_available 公有方法",
      hasattr(p, "image_engine_available") and callable(p.image_engine_available))

print()
print("=" * 62)
if FAILS:
    print(f"✗ {len(FAILS)} 项失败：")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("✓ ALL PASS")
