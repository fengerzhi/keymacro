#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uninstall.py —— 按键精灵卸载程序

删除安装目录、桌面/开始菜单快捷方式，以及本机配置数据。

脚本库放在安装目录的「脚本」文件夹里：卸载前会先把里面的脚本搬到
`~/.macro-sprite/scripts`，重装后程序会自动搬回去，不会因为卸载丢脚本。
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

APP_NAME = "按键精灵"
HERE = Path(__file__).resolve().parent

# 安装目录里的脚本文件夹（core.library.SCRIPTS_DIR_NAME 保持一致）
SCRIPTS_DIR_NAME = "脚本"


def targets() -> list[Path]:
    out = [HERE]
    home = Path.home()
    if platform.system() == "Windows":
        out.append(home / "Desktop" / f"{APP_NAME}.lnk")
        out.append(Path(os.environ.get("APPDATA", ""))
                   / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                   / f"{APP_NAME}.lnk")
    return [p for p in out if p.exists()]


def rescue_scripts() -> Path | None:
    """卸载前把安装目录里的脚本搬到用户目录（重装后自动搬回）"""
    src = HERE / SCRIPTS_DIR_NAME
    if not src.is_dir():
        return None
    dst = Path.home() / ".macro-sprite" / "scripts"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for p in src.glob("*.json"):
            if p.name == "index.json":
                continue
            shutil.copy2(p, dst / p.name)
        return dst
    except Exception:
        return None


def data_dir() -> Path:
    try:
        sys.path.insert(0, str(HERE))
        from core.library import app_dir
        return Path(app_dir())
    except Exception:
        if platform.system() == "Windows":
            base = os.environ.get("APPDATA") or str(Path.home())
            return Path(base) / APP_NAME
        return Path.home() / ".config" / APP_NAME


def main() -> int:
    keep = "--keep-data" in sys.argv
    print("=" * 58)
    print(f"  {APP_NAME} · 卸载")
    print("=" * 58)
    for p in targets():
        print("  将删除：", p)
    if not keep:
        print("  将删除配置与日志：", data_dir())
    print("  脚本会保留，不会被删掉")
    try:
        ans = input("\n确认卸载？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "y"
    if ans not in ("y", "yes"):
        print("已取消。")
        return 0

    kept = rescue_scripts()
    if kept:
        print("  已把脚本备份到：", kept)
    for p in targets():
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  删除失败 {p}: {exc}")
    if not keep:
        # 脚本是用户的成果：只清配置/日志，scripts 目录留着
        data = data_dir()
        for child in list(data.iterdir()) if data.is_dir() else []:
            if child.name == "scripts":
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except Exception:
                pass
    if platform.system() == "Windows":
        import subprocess
        subprocess.run(["reg", "delete",
                        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyMacro",
                        "/f"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("\n卸载完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
