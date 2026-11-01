#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 macOS 安装包（.dmg）

先决条件：**必须在 macOS 上运行**。
PyInstaller 不能跨平台交叉编译——Windows 上跑不出 .app/.dmg，
同理 macOS 上也打不出 .exe。所以苹果版只能在一台 Mac 上打。

用法：
    python3 build/build.py       # 先生成 dist/按键精灵.app
    python3 build/make_macos.py  # 再打成 dist/按键精灵-<版本>-macOS.dmg

    python3 build/make_macos.py --rebuild   # 主程序没有 .app 时自动先打包

产物：
    dist/按键精灵.app                       （可直接拖进「应用程序」）
    dist/按键精灵-<版本>-macOS.dmg           （对外分发的安装包）
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:                      # noqa: BLE001
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "按键精灵"
DIST = os.path.join(ROOT, "dist")


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
    """确保 dist/按键精灵.app 存在，返回其路径"""
    app = os.path.join(DIST, f"{APP_NAME}.app")
    if rebuild or not os.path.isdir(app):
        log("主程序尚未生成，先调用 build/build.py 打包…")
        rc = subprocess.call([sys.executable,
                              os.path.join(ROOT, "build", "build.py")], cwd=ROOT)
        if rc != 0 or not os.path.isdir(app):
            raise SystemExit(f"主程序打包失败（退出码 {rc}），未生成应用包。")
    return app


def adhoc_sign(app: str) -> None:
    """无证书时的临时签名（ad-hoc）

    不签名的 .app 在 Apple Silicon 上会被直接拒绝运行；ad-hoc 签名足够本机
    和普通用户安装，只是仍然过不了「已公证」的 Gatekeeper 检查
    （用户首次打开需右键 →「打开」）。
    """
    if not shutil.which("codesign"):
        return
    log("临时签名（ad-hoc）…")
    rc = subprocess.call(["codesign", "--force", "--deep", "--sign", "-", app],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        log("[!] 临时签名失败，不影响生成 dmg；用户首次打开需右键 →「打开」")


def make_dmg(app: str, ver: str) -> str:
    """把 .app 打成 dmg：卷里放应用本体 + 指向 /Applications 的快捷方式"""
    stage = tempfile.mkdtemp(prefix="km_dmg_")
    try:
        shutil.copytree(app, os.path.join(stage, f"{APP_NAME}.app"), symlinks=True)
        link = os.path.join(stage, "Applications")
        try:
            os.symlink("/Applications", link)
        except OSError:
            pass                                    # 没有软链也能装，只是少了拖拽提示

        out = os.path.join(DIST, f"{APP_NAME}-{ver}-macOS.dmg")
        if os.path.exists(out):
            os.remove(out)

        log("生成 dmg…")
        rc = subprocess.call([
            "hdiutil", "create",
            "-volname", f"{APP_NAME} v{ver}",
            "-srcfolder", stage,
            "-ov", "-format", "UDZO",
            out,
        ])
        if rc != 0 or not os.path.exists(out):
            raise SystemExit(f"dmg 生成失败（hdiutil 退出码 {rc}）。")
        return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main() -> int:
    if platform.system() != "Darwin":
        log("=" * 60)
        log("这个脚本只能在 macOS 上运行。")
        log("")
        log("PyInstaller 不支持交叉编译：Windows 上无法生成 .app / .dmg，")
        log("请把整个项目文件夹拷到 Mac（Apple 芯片或 Intel 均可），")
        log("在 Mac 上执行：")
        log("    python3 build/build.py && python3 build/make_macos.py")
        log("或者双击 build/打包苹果版.command")
        log("=" * 60)
        return 1

    rebuild = "--rebuild" in sys.argv
    ver = version()
    log("=" * 60)
    log(f"  按键精灵 · 生成 macOS 安装包    版本 v{ver}")
    log("=" * 60)

    app = ensure_app(rebuild)
    log(f"  应用包：{app}")
    adhoc_sign(app)
    dmg = make_dmg(app, ver)

    size = os.path.getsize(dmg) / 1024 / 1024
    log("\n" + "=" * 60)
    log("安装包生成完成  [OK]")
    log(f"  {dmg}")
    log(f"  大小：{size:.1f} MB")
    log("")
    log("  分发说明：")
    log("   1) 用户双击 dmg，把「按键精灵」拖进 应用程序")
    log("   2) 首次打开请右键 →「打开」（未做 Apple 公证，双击会被拦一次）")
    log("   3) 首次录制前需在 系统设置 → 隐私与安全性 → 辅助功能 里勾选本程序")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
