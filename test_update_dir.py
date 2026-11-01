"""升级文件夹模式测试：识别 zip 版本 / 取最高版本 / 打包内 version.txt"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_zip(path: Path, version: str, notes: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("version.txt", version)
        if notes:
            zf.writestr("notes.txt", notes)
        zf.writestr("main.py", "# fake\n")
    return path


def main() -> int:
    from core import updater
    fails = []

    def check(name, cond):
        print(("  ok  " if cond else "  FAIL") + "  " + name)
        if not cond:
            fails.append(name)

    tmp = Path(tempfile.mkdtemp(prefix="upddir_"))
    # 目录里放两个包 + notes，应取版本最高的那个
    make_zip(tmp / "keymacro-1.1.0.zip", "1.1.0")
    make_zip(tmp / "keymacro-1.4.0.zip", "1.4.0", "修复若干问题")
    make_zip(tmp / "keymacro-2.0.0.zip", "2.0.0", "大版本")

    m = updater.check_update_dir(str(tmp))
    check("取最高版本", m["version"] == "2.0.0")
    check("note 读取", "大版本" in m["notes"])
    check("本地包路径", Path(m["package"]).exists())
    check("判定为更新", updater.is_newer(m["version"], updater.APP_VERSION))

    check("fetch_package 直取本地", updater.fetch_package(m).exists())

    # 只有 version.json 的情况
    d2 = tmp / "vj"
    d2.mkdir()
    make_zip(d2 / "pkg.zip", "1.9.0")
    (d2 / "version.json").write_text(
        '{"version": "1.9.0", "package": "pkg.zip", "notes": "json 模式"}',
        encoding="utf-8")
    m2 = updater.check_update_dir(str(d2))
    check("version.json 模式", m2["version"] == "1.9.0"
          and Path(m2["package"]).exists())

    # 空目录应报错
    d3 = tmp / "empty"
    d3.mkdir()
    try:
        updater.check_update_dir(str(d3))
        check("空目录报错", False)
    except FileNotFoundError:
        check("空目录报错", True)

    # 自己打的包带 version.txt
    zp = updater.build_update_zip(tmp / "self.zip")
    check("打包带 version.txt", updater.zip_version(zp) == updater.APP_VERSION)

    # 保存/读取升级文件夹
    updater.set_update_dir(str(tmp))
    check("记住文件夹", updater.update_dir() == str(tmp))
    check("has_source", updater.has_source())
    check("active_manifest", updater.active_manifest()["version"] == "2.0.0")
    updater.set_update_dir("")

    print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
