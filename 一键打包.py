#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一步到位打包启动器（Windows 推荐双击 run_me.py 或 build_windows.bat）

与 build/build.py 的区别：
    这个脚本会先自己装好依赖再调用打包，出错时**停住窗口**，
    因此在 Windows 上双击运行时不会一闪而过。

用法：
    双击本文件（.py 关联到 Python 时）
    或命令行：python 一键打包.py
"""
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PKGS = ["pynput", "pillow", "pyinstaller"]


def pause(msg="按回车键关闭窗口…"):
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


def step(n, total, text):
    print(f"\n[{n}/{total}] {text}")
    print("-" * 58)


def run(cmd, **kw):
    print("  $", " ".join(cmd) if isinstance(cmd, list) else cmd)
    return subprocess.call(cmd, cwd=HERE, **kw)


def main() -> int:
    print("=" * 58)
    print("  按键精灵 · 一键打包")
    print("=" * 58)
    print(f"Python：{sys.version.split()[0]}  ({sys.executable})")
    print(f"项目目录：{HERE}")

    if not os.path.exists(os.path.join(HERE, "main.py")):
        print("\n[错误] 当前目录下找不到 main.py，请把本文件放在项目根目录。")
        return 1

    # 1) 装依赖
    step(1, 2, "安装 / 检查依赖")
    if run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        print("  (pip 升级失败，通常不影响继续)")

    args = [sys.executable, "-m", "pip", "install", *PKGS]
    if run(args) != 0:
        print("\n[!] 从默认源安装失败，尝试清华镜像…")
        mirror = args + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
        if run(mirror) != 0:
            print("\n[错误] 依赖安装失败，请检查网络连接。")
            return 1

    # 2) 打包
    step(2, 2, "调用 PyInstaller 打包")
    py = os.path.join(HERE, "build", "build.py")
    if not os.path.exists(py):
        print(f"[错误] 找不到 {py}")
        return 1

    rc = subprocess.call([sys.executable, py] + sys.argv[1:], cwd=HERE)
    if rc != 0:
        print("\n[错误] 打包失败，请把上面的完整日志发给我分析。")
        return rc

    dist = os.path.join(HERE, "dist")
    print("\n" + "=" * 58)
    out = os.path.join("dist", "按键精灵")
    print(f"  打包成功！产物在：{out}")
    print("=" * 58)
    if sys.platform == "win32" and os.path.isdir(dist):
        try:
            os.startfile(dist)                 # 自动打开产物文件夹
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        print("\n[未捕获异常]")
        traceback.print_exc()
    finally:
        if sys.platform == "win32":
            pause()
    sys.exit(code)
