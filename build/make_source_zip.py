#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成源码分发包：dist/按键精灵-<版本>-Windows打包.zip

包里是「源码 + 打包工具」：
    解压后双击 一键打包.py（或 build/build_windows.bat）就能自己打 exe，
    也可以直接双击 安装按键精灵.bat 装到本机（需要 Python）。

这个包同时是**源码版**的升级包：解压后覆盖程序目录即完成升级
（version.json 里的 package 字段指向的就是它）。

用法：
    python build/make_source_zip.py
    python build/make_source_zip.py --out D:\\发布     # 指定输出目录

注意：打包版（exe）升级要用 exe 形态的包，在程序里点
「检查更新 → 生成升级包」得到，不能用这个源码包。
"""
from __future__ import annotations

import os
import sys
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "按键精灵"
DIST = os.path.join(ROOT, "dist")

# 不进源码包的东西：构建产物、缓存、旧包、日志
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pyinstaller", "dist",
    ".idea", ".vscode", "node_modules", ".codebuddy", ".pytest_cache",
}
EXCLUDE_FILES = {"打包日志.txt"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip", ".log", ".part", ".tmp"}


def version() -> str:
    p = os.path.join(ROOT, "version.txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            v = f.read().strip().splitlines()[0].strip()
            if v:
                return v
    except (OSError, IndexError):
        pass
    return "1.0.0"


def collect() -> list[str]:
    """列出要打包的文件（相对路径，用 / 分隔）"""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDE_FILES:
                continue
            if os.path.splitext(name)[1].lower() in EXCLUDE_SUFFIX:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if rel.startswith("../"):
                continue
            out.append(rel)
    return out


def main() -> int:
    out_dir = DIST
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 < len(sys.argv):
            out_dir = sys.argv[i + 1]
    os.makedirs(out_dir, exist_ok=True)

    ver = version()
    top = f"{APP_NAME}-{ver}-Windows打包"
    out = os.path.join(out_dir, top + ".zip")

    files = collect()
    if "main.py" not in files:
        print("[错误] 没找到 main.py，确认脚本在项目的 build/ 下运行。")
        return 1

    # 先写临时文件，成功后再改名：中途失败不会留下半个 zip
    tmp = out + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            zf.write(os.path.join(ROOT, rel), f"{top}/{rel}")
        # 包内必须有 version.txt：升级器优先按它识别版本
        if "version.txt" not in files:
            zf.writestr(f"{top}/version.txt", ver)

    os.replace(tmp, out)

    size = os.path.getsize(out) / 1024 / 1024
    print("=" * 58)
    print(f"  源码包已生成    版本 v{ver}")
    print("=" * 58)
    print(f"  文件：{out}")
    print(f"  大小：{size:.1f} MB，{len(files) + 1} 个文件")
    print()
    print("  发给别人：解压后双击 一键打包.py 自己打 exe，")
    print("            或双击 安装按键精灵.bat 直接装（需 Python）。")
    print("  升级用：  放到升级文件夹 / 上传 Release 附件即可。")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
