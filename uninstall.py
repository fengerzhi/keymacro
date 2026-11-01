#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uninstall.py —— 按键精灵卸载程序

删除安装目录、桌面/开始菜单快捷方式，以及本机配置与脚本数据。
如果只想删程序、保留脚本，用 --keep-data。
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

APP_NAME = "按键精灵"
HERE = Path(__file__).resolve().parent


def targets() -> list[Path]:
    out = [HERE]
    home = Path.home()
    if platform.system() == "Windows":
        out.append(home / "Desktop" / f"{APP_NAME}.lnk")
        out.append(Path(os.environ.get("APPDATA", ""))
                   / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                   / f"{APP_NAME}.lnk")
    return [p for p in out if p.exists()]


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
        print("  将删除用户数据：", data_dir())
    try:
        ans = input("\n确认卸载？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "y"
    if ans not in ("y", "yes"):
        print("已取消。")
        return 0

    for p in targets():
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  删除失败 {p}: {exc}")
    if not keep:
        shutil.rmtree(data_dir(), ignore_errors=True)
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
