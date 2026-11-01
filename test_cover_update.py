"""test_cover_update.py —— 一键覆盖更新的复制逻辑（中文/空格/括号目录）"""
import os, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

import importlib.util
spec = importlib.util.spec_from_file_location(
    "cover", os.path.join(os.path.dirname(os.path.abspath(__file__)), "一键覆盖更新.py"))
cover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cover)

tmp = Path(tempfile.mkdtemp(prefix="cover-"))
src = tmp / "新版" / "按键精灵-1.4.4-Windows打包 (1)"
dst = tmp / "Desktop" / "按键精灵-Windows打包 (1) (2)"

# 新版文件
(src / "core").mkdir(parents=True)
(src / "ui").mkdir()
(src / "logs").mkdir()              # 应被跳过
(src / "__pycache__").mkdir()       # 应被跳过
(src / "dist").mkdir()              # 应被跳过
(src / "main.py").write_text("NEW", encoding="utf-8")
(src / "core" / "updater.py").write_text('APP_VERSION = "1.4.4"', encoding="utf-8")
(src / "core" / "script_model.py").write_text("NEW2", encoding="utf-8")
(src / "ui" / "app.py").write_text("NEW3", encoding="utf-8")
(src / "使用说明.txt").write_text("新的说明", encoding="utf-8")
(src / "启动按键精灵.bat").write_text("x", encoding="utf-8")
(src / "logs" / "old.log").write_text("不要复制", encoding="utf-8")
(src / "__pycache__" / "a.pyc").write_bytes(b"\0")
(src / "dist" / "big.exe").write_bytes(b"\0")

# 旧版目录
dst.mkdir(parents=True)
(dst / "main.py").write_text("OLD", encoding="utf-8")
(dst / "core").mkdir()
(dst / "使用说明.txt").write_text("旧说明", encoding="utf-8")

total, failed = cover.copy_tree(src, dst)
check("没有失败项", not failed, failed)
check("覆盖了 main.py", (dst / "main.py").read_text(encoding="utf-8") == "NEW")
check("新建了 core/updater.py", (dst / "core" / "updater.py").exists())
check("覆盖了中文文件名文件", (dst / "使用说明.txt").read_text(encoding="utf-8") == "新的说明")
check("复制了启动脚本", (dst / "启动按键精灵.bat").exists())
check("跳过 logs", not (dst / "logs").exists())
check("跳过 __pycache__", not (dst / "__pycache__").exists())
check("跳过 dist", not (dst / "dist").exists())
check("文件数正确（6 个有效文件，不含 logs/缓存/dist）", total == 6, total)

# 再次执行应当幂等
total2, failed2 = cover.copy_tree(src, dst)
check("重复执行不报错", not failed2, failed2)
check("重复执行覆盖同样数量", total2 == total, total2)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + "、".join(FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)
