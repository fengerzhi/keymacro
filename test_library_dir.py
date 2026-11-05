"""test_library_dir.py —— 脚本库目录（安装目录/脚本）与外部改动刷新"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="macro-lib-test-"))
os.environ["HOME"] = str(_TMP)

import core.library as cl

_INSTALL = _TMP / "install"
_INSTALL.mkdir(parents=True, exist_ok=True)
cl.install_dir = lambda: _INSTALL          # 假装安装目录在临时位置
cl.app_dir = lambda: _TMP / ".macro-sprite"

from core.script_model import Script, Event

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")


def make_script(name, times):
    s = Script(name=name)
    s.events = [Event(t=t, type="comment", text=f"e{i}")
                for i, t in enumerate(times)]
    return s


# ------------------------------------------------------------ 目录位置
check("脚本目录 = 安装目录/脚本", cl.scripts_dir() == _INSTALL / "脚本",
      cl.scripts_dir())

# ------------------------------------------------------------ 老脚本自动搬过来
legacy = _TMP / ".macro-sprite" / "scripts"
legacy.mkdir(parents=True, exist_ok=True)
make_script("旧脚本", [0, 100]).save(str(legacy / "旧脚本.json"))

lib = cl.ScriptLibrary()
check("旧脚本已搬进安装目录", (lib.scripts_dir / "旧脚本.json").exists())
check("迁移计数 = 1", lib.migrated == 1, lib.migrated)
check("列表能看到旧脚本", [m.name for m in lib.list()] == ["旧脚本"],
      [m.name for m in lib.list()])

# ------------------------------------------------------------ 保存落在安装目录
lib.save_new(make_script("新脚本", [0, 200, 400]))
check("新脚本落在「脚本」里", (lib.scripts_dir / "新脚本.json").exists())
check("列表 2 个", len(lib.list()) == 2, len(lib.list()))

# ------------------------------------------------------------ 外部丢文件进来
before_sig = lib.signature()
make_script("外部丢进来的", [0, 50, 900]).save(
    str(lib.scripts_dir / "外部丢进来的.json"))
check("signature 变了", lib.signature() != before_sig, (before_sig, lib.signature()))
check("refresh 返回 True", lib.refresh() is True)
check("refresh 后列表 3 个", len(lib.list()) == 3, len(lib.list()))
check("外部脚本可加载", lib.load("外部丢进来的.json") is not None)

# ------------------------------------------------------------ 外部改了文件内容
make_script("外部丢进来的", [0, 50, 900, 1900]).save(
    str(lib.scripts_dir / "外部丢进来的.json"))
lib.refresh()
m = lib.get("外部丢进来的.json")
check("改后步数更新为 4", m.event_count == 4, m.event_count)
check("改后时长更新为 1.9s", m.duration_ms == 1900, m.duration_ms)

# ------------------------------------------------------------ 外部删文件
(lib.scripts_dir / "外部丢进来的.json").unlink()
lib.refresh()
check("删文件后列表回到 2 个", len(lib.list()) == 2, len(lib.list()))

# ------------------------------------------------------------ 删除进回收站
lib.delete("新脚本.json")
check("删除后文件不在「脚本」里", not (lib.scripts_dir / "新脚本.json").exists())
check("回收站里有备份",
      any((_TMP / ".macro-sprite" / "trash").glob("*新脚本.json")))

print("\n" + "=" * 50)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败：" + "、".join(FAIL))
raise SystemExit(1 if FAIL else 0)
