#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成真正的自包含 Windows 安装包

和 build/build.py 里那个「安装按键精灵.exe」的区别：
    那个安装器只是个搬运工，必须和「按键精灵」文件夹放在一起才能用；
    本脚本把整个程序目录**嵌进 exe 里**，产出的单个 exe 拷到任何一台
    Win10/11 电脑上双击即可安装，不需要 Python、不需要附带文件夹。

用法：
    python build/make_setup.py                 # 直接打包（需要 dist/按键精灵 已存在）
    python build/make_setup.py --rebuild       # 先重新打包主程序，再生成安装包

产物：
    dist/按键精灵-<版本>-安装程序.exe
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "按键精灵"
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, ".pyinstaller")
STAGE = os.path.join(WORK, "payload")

# 不用装进用户电脑的冗余文件
EXCLUDE_FILES = {"安装按键精灵.exe", "生成安装包.bat"}
EXCLUDE_SUFFIX = {".lnk", ".pyc", ".log"}


def log(msg: str) -> None:
    print(msg, flush=True)


def version() -> str:
    p = os.path.join(ROOT, "version.txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            v = f.read().strip().splitlines()[0].strip()
            if v:
                return v
    except OSError:
        pass
    return "1.0.0"


def ensure_app(rebuild: bool) -> str:
    """确保 dist/按键精灵 存在，返回其路径"""
    app_dir = os.path.join(DIST, APP_NAME)
    exe = os.path.join(app_dir, f"{APP_NAME}.exe")
    if rebuild or not os.path.exists(exe):
        log("主程序未就绪，先调用 build/build.py 打包主程序…")
        rc = subprocess.call([sys.executable, os.path.join(ROOT, "build", "build.py")],
                             cwd=ROOT)
        if rc != 0 or not os.path.exists(exe):
            raise SystemExit(f"主程序打包失败（退出码 {rc}），未生成安装包。")
    return app_dir


def make_payload(app_dir: str) -> str:
    """把程序目录整理成待内嵌的 payload（剔除冗余）"""
    import time

    # 每次换一个新目录：旧目录可能删不掉（删除保护/安全软件），
    # 复用它会把上一轮的旧文件一起打进新包
    target = os.path.join(STAGE, APP_NAME + "_" + time.strftime("%m%d%H%M%S"))
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)

    n = 0
    for item in os.scandir(app_dir):
        if item.is_dir():
            if item.name == "__pycache__":
                continue
            shutil.copytree(item.path, os.path.join(target, item.name),
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            continue
        if item.name in EXCLUDE_FILES or \
                os.path.splitext(item.name)[1].lower() in EXCLUDE_SUFFIX:
            continue
        shutil.copy2(item.path, os.path.join(target, item.name))
        n += 1

    # 快捷方式图标：install.py 会找 安装目录/assets/icon.ico
    ico = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.exists(ico):
        os.makedirs(os.path.join(target, "assets"), exist_ok=True)
        shutil.copy2(ico, os.path.join(target, "assets", "icon.ico"))
    return target


def run_stream(cmd: list[str]) -> int:
    """实时输出子进程日志（UTF-8 读取，避免 GBK 控制台解码报错中断）"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         bufsize=1, cwd=ROOT, env=env)
    assert p.stdout is not None
    for line in p.stdout:
        print(line, end="", flush=True)
    p.wait()
    return p.returncode


def build_installer(payload: str, ver: str) -> str:
    name = f"{APP_NAME}-{ver}-安装程序"
    ico = os.path.join(ROOT, "assets", "icon.ico")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", name,
        "--distpath", DIST,
        "--workpath", os.path.join(WORK, "setup"),
        "--specpath", os.path.join(WORK, "setup"),
        "--paths", ROOT,
        "--add-data", f"{payload}{os.pathsep}payload",
    ]
    if os.path.exists(ico):
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "install.py"))

    log("\n执行：" + " ".join(cmd) + "\n")
    rc = run_stream(cmd)
    exe = os.path.join(DIST, name + ".exe")
    if rc != 0 or not os.path.exists(exe):
        raise SystemExit(f"安装包生成失败（退出码 {rc}）。")
    return exe


def main() -> int:
    rebuild = "--rebuild" in sys.argv
    ver = version()
    log("=" * 60)
    log(f"  按键精灵 · 生成自包含安装包    版本 v{ver}")
    log("=" * 60)

    app_dir = ensure_app(rebuild)
    log(f"  程序目录：{app_dir}")

    payload = make_payload(app_dir)
    log(f"  内嵌文件：{payload}")

    exe = build_installer(payload, ver)
    size = os.path.getsize(exe) / 1024 / 1024
    log("\n" + "=" * 60)
    log("安装包生成完成  [OK]")
    log(f"  {exe}")
    log(f"  大小：{size:.1f} MB")
    log("")
    log("  双击即可安装，可单独发给别人（不需要 Python，不需要附带文件夹）。")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
