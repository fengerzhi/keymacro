#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 按键精灵 Windows 安装程序（双击即装）

做的事：
  1. 把程序装到  %LOCALAPPDATA%\\Programs\\按键精灵  （无需管理员权限）
  2. 复制全部程序文件（源码模式装 .py 源码；打包模式的文件夹版会带上 exe 与 _internal）
  3. 生成卸载程序  uninstall.py / 卸载按键精灵.bat
  4. 创建桌面快捷方式与开始菜单快捷方式（.lnk，纯 Python 生成，不依赖 pywin32）
  5. 若本机有 Python：生成 按键精灵.bat 启动器，并把 exe 图标换成程序图标
  6. 可选：安装后直接启动

用法：
    双击  安装按键精灵.vbs     （推荐：无黑框，自动找 Python 或 exe）
    或    python install.py            安装
         python install.py --silent   静默安装（不弹确认）
         python install.py --dir "D:\\按键精灵"  指定安装目录

卸载：运行安装目录里的  卸载按键精灵.bat
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# 控制台编码兜底：遇到无法编码的字符用 ? 代替，绝不让安装中断
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

APP_NAME = "按键精灵"
APP_ID = "KeyMacro"
DEFAULT_VERSION = "1.8.0"


def app_version() -> str:
    """取程序真实版本号（读不到就用内置常量）

    刻意不做 import：安装器必须保持轻量，
    也不能把主程序的依赖链打进安装包里。
    """
    for name, key in (("version.txt", None), ("version.json", "version")):
        try:
            text = (SRC / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        v = ""
        if key:
            try:
                v = str(json.loads(text).get(key) or "").strip()
            except ValueError:
                v = ""
        else:
            v = text.strip().splitlines()[0].strip() if text.strip() else ""
        if v:
            return v
    return DEFAULT_VERSION


def candidate_roots() -> list[Path]:
    """可能放程序文件的目录（按优先级）"""
    out: list[Path] = []
    env = (os.environ.get("KEYMACRO_SRC") or "").strip().strip('"')
    if env:
        out.append(Path(env))
    if getattr(sys, "frozen", False):          # 打包后的安装器 exe
        here = Path(sys.executable).resolve().parent
    else:
        here = Path(__file__).resolve().parent
    out += [here, Path.cwd()]
    # 再往下一层找（压缩包被解成一层子文件夹时）
    for base in list(out):
        try:
            out += [d for d in base.iterdir() if d.is_dir()]
        except OSError:
            pass
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def embedded_payload() -> Path | None:
    """打进安装器内部的程序文件（自包含安装包用）

    PyInstaller 单文件运行时会把内嵌数据解压到 sys._MEIPASS，
    build/make_setup.py 把程序目录放在其中的 payload 里。
    """
    base = getattr(sys, "_MEIPASS", "")
    if not base:
        return None
    base_p = Path(base)
    for cand in (base_p / "payload", base_p / "payload" / APP_NAME):
        try:
            if (cand / "main.py").exists() or (cand / f"{APP_NAME}.exe").exists():
                return cand
        except OSError:
            continue
    return None


def find_source() -> Path | None:
    """找到真正含 main.py / 按键精灵.exe 的目录（优先用安装包内嵌的）"""
    emb = embedded_payload()
    if emb:
        return emb
    for p in candidate_roots():
        try:
            if (p / "main.py").exists() or (p / f"{APP_NAME}.exe").exists():
                return p
        except OSError:
            continue
    return None


SRC = find_source() or Path(__file__).resolve().parent


def default_dir() -> Path:
    """默认安装目录：当前用户，不需要管理员"""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "Programs" / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ 复制文件
SKIP_DIRS = {".git", "__pycache__", ".pyinstaller", "build", "dist", ".idea",
             ".vscode", "node_modules", ".codebuddy"}
# 注意：绝不能跳过 .zip！打包版运行必需的 _internal/base_library.zip
# （里面有 encodings 模块）就是 .zip，漏掉它程序会直接起不来。
SKIP_SUFFIX = {".pyc", ".part", ".log"}


def copy_app(src: Path, dst: Path) -> int:
    """把程序文件复制到安装目录，返回复制数量"""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        if any(p in SKIP_DIRS for p in rel.parts):
            continue
        if item.suffix.lower() in SKIP_SUFFIX:
            continue
        if item.name.startswith(("_apply_update", "_upgrade_last")):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, target)
            n += 1
        except OSError:
            pass
    return n


# ------------------------------------------------------------------ .lnk 生成
def make_shortcut(lnk_path: Path, target: Path, args: str = "",
                  workdir: Path | None = None, icon: Path | None = None,
                  desc: str = "") -> bool:
    """生成 Windows 快捷方式（.lnk）：PowerShell → pywin32 → 手工二进制"""
    if platform.system() != "Windows":
        return False

    # 方案一：PowerShell 调 WScript.Shell，Windows 自带，最可靠
    try:
        def _ps(s: str) -> str:
            return str(s).replace("'", "''")
        cmd = (
            "$s=(New-Object -ComObject WScript.Shell)"
            f".CreateShortcut('{_ps(lnk_path)}');"
            f"$s.TargetPath='{_ps(target)}';"
            f"$s.Arguments='{_ps(args)}';"
            f"$s.WorkingDirectory='{_ps(workdir or target.parent)}';"
            f"$s.Description='{_ps(desc or APP_NAME)}';"
            + (f"$s.IconLocation='{_ps(icon)}';" if icon else "")
            + "$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", cmd],
            capture_output=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if Path(lnk_path).exists():
            return True
    except Exception:
        pass

    try:
        import pythoncom  # noqa: F401  # 若装了 pywin32 用它更稳
    except Exception:
        pythoncom = None
    if pythoncom is not None:
        try:
            from win32com.client import Dispatch
            sh = Dispatch("WScript.Shell").CreateShortCut(str(lnk_path))
            sh.TargetPath = str(target)
            sh.Arguments = args
            sh.WorkingDirectory = str(workdir or target.parent)
            if icon:
                sh.IconLocation = str(icon)
            sh.Description = desc or APP_NAME
            sh.Save()
            return True
        except Exception:
            pass

    # 手工拼二进制 .lnk（Shell Link 头 + 若干段）
    try:
        link_flags = 0x00000080            # HasName? 见下：用描述段
        flags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000020 | 0x00000040
        #  1 HasLinkTargetIDList(不写) 2 HasLinkInfo 4 HasName 0x20 HasArguments
        #  0x40 HasWorkingDir
        flags &= ~0x00000001             # 不带 IDList，省很多麻烦

        def _strz(s: str, ansi: bool) -> bytes:
            if ansi:
                return s.encode("mbcs", "replace") + b"\0"
            return s.encode("utf-16-le") + b"\0\0"

        header = struct.pack("<I", 0x4C)             # HeaderSize
        header += struct.pack("<16s", bytes.fromhex(
            "0114020000000000C000000000000046"))     # CLSID ShellLink
        header += struct.pack("<I", flags)
        header += struct.pack("<I", 0x20)            # FileAttributes ARCHIVE
        header += b"\0" * 24                         # creation/access/write
        header += struct.pack("<I", 0)               # FileSize
        header += struct.pack("<i", 0)               # IconIndex
        header += struct.pack("<I", 1)               # ShowCommand SW_SHOWNORMAL
        header += struct.pack("<H", 0)               # HotKey
        header += b"\0" * 10                         # Reserved

        # LinkInfo（本地路径）
        drive, rest = os.path.splitdrive(str(target))
        local_base = (drive + "\\").encode("mbcs", "replace") + b"\0"
        common_tail = rest.encode("mbcs", "replace") + b"\0"
        suffix = common_tail
        vol_id = struct.pack("<IIII", 0x11, 0, 0x3E, 0x1C)   # offset,type,serial
        vol = struct.pack("<I", 0x11) + vol_id                # VolumeID 段
        path_off = 0x1C + len(vol)                            # LocalBasePath 偏移
        link_info = struct.pack("<I", 0x1C) + struct.pack("<I", path_off)
        link_info += struct.pack("<I", len(vol)) + b"\0" * 4
        link_info += struct.pack("<I", len(local_base))
        link_info += b"\0" * 4
        link_info += struct.pack("<I", len(suffix))
        link_info += vol + local_base + suffix
        link_info = struct.pack("<I", len(link_info)) + link_info[4:]

        parts = [header]
        parts.append(link_info)
        if desc:
            d = _strz(desc[:259], True)
            parts.append(struct.pack("<H", len(d)) + d)
        if args:
            a = _strz(args, True)
            parts.append(struct.pack("<H", len(a)) + a)
        if workdir:
            w = _strz(str(workdir), True)
            parts.append(struct.pack("<H", len(w)) + w)
        if icon:
            ip = _strz(str(icon)[:259], True)
            parts.append(struct.pack("<H", len(ip)) + ip)
        parts.append(struct.pack("<I", 0))           # Terminal
        lnk_path.write_bytes(b"".join(parts))
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 启动器
def write_launcher(dst: Path, pythonw: str | None) -> Path | None:
    """生成 按键精灵.bat 启动器（源码模式必须有）"""
    if not pythonw:
        return None
    lines = [
        "@echo off",
        "setlocal",
        "chcp 936 >nul",
        f'cd /d "%~dp0"',
        f'start "" "{pythonw}" "main.py"',
        "endlocal",
    ]
    bat = dst / f"{APP_NAME}.bat"
    try:
        bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("gbk"))
    except Exception:
        bat.write_text("\r\n".join(lines), encoding="utf-8")
    return bat


def find_pythonw() -> str | None:
    """找 pythonw.exe（无控制台窗口的解释器）"""
    if platform.system() != "Windows":
        return sys.executable
    # 安装启动器（bat）可能已探测到解释器，直接用，避免再猜路径
    env = (os.environ.get("KEYMACRO_PYTHONW") or "").strip().strip('"')
    if env and Path(env).exists():
        return env
    exe = Path(sys.executable)
    cand = []
    if not getattr(sys, "frozen", False):
        cand.append(exe.with_name("pythonw.exe"))
    for p in cand:
        if p.exists():
            return str(p)
    # 退而求其次：用 python.exe（会闪一下黑框，但能正常用）
    envp = (os.environ.get("KEYMACRO_PYTHONW") or "").strip().strip('"')
    if envp:
        pw = Path(envp).with_name("python.exe")
        if pw.exists():
            return str(pw)
    if exe.exists():
        return str(exe)
    # 常见安装位置
    for base in (os.environ.get("LOCALAPPDATA", ""),
                 os.environ.get("PROGRAMFILES", ""),
                 os.environ.get("PROGRAMFILES(X86)", "")):
        if not base:
            continue
        for ver in ("Python313", "Python312", "Python311", "Python310",
                    "Python39", "Python38"):
            q = Path(base) / "Programs" / ver / "pythonw.exe"
            if q.exists():
                return str(q)
            q2 = Path(base) / ver / "pythonw.exe"
            if q2.exists():
                return str(q2)
    return None


def write_uninstaller(dst: Path) -> None:
    """写卸载程序"""
    src_un = SRC / "uninstall.py"
    if src_un.exists():
        shutil.copy2(src_un, dst / "uninstall.py")
    bat = dst / "卸载按键精灵.bat"
    lines = [
        "@echo off",
        "setlocal",
        "chcp 936 >nul",
        "title 卸载 按键精灵",
        'set "D=%~dp0"',
        'if "%D:~-1%"=="\\" set "D=%D:~0,-1%"',
        "echo ============================================================",
        "echo    卸载 按键精灵",
        "echo ============================================================",
        'echo   安装目录：%D%',
        "echo.",
        "set /p OK=确认卸载？输入 Y 回车：",
        'if /i not "%OK%"=="Y" goto :end',
        'del /f /q "%USERPROFILE%\\Desktop\\按键精灵.lnk" >nul 2>nul',
        'del /f /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\按键精灵.lnk" >nul 2>nul',
        'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\KeyMacro" /f >nul 2>nul',
        '> "%TEMP%\\km_uninst.bat" echo ping 127.0.0.1 -n 3 ^>nul',
        '>> "%TEMP%\\km_uninst.bat" echo rmdir /s /q "%D%"',
        'start "" /min cmd /c "%TEMP%\\km_uninst.bat"',
        "echo.",
        "echo 已卸载（配置数据保留在 %APPDATA%\\.macro-sprite）。",
        ":end",
        "echo.",
        "pause >nul",
        "exit /b",
    ]
    try:
        bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("gbk"))
    except Exception:
        bat.write_text("\r\n".join(lines), encoding="utf-8")


def write_uninstall_reg(dst: Path, launcher: Path) -> None:
    """写入控制面板「程序和功能」条目（HKCU，免管理员）"""
    if platform.system() != "Windows":
        return
    try:
        key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyMacro"
        cmd = [
            ("DisplayName", APP_NAME),
            ("DisplayVersion", app_version()),
            ("Publisher", "KeyMacro"),
            ("InstallLocation", str(dst)),
            ("DisplayIcon", str(launcher)),
            ("UninstallString", f'"{dst / "卸载按键精灵.bat"}"'),
            ("NoModify", "1"),
            ("NoRepair", "1"),
        ]
        for name, val in cmd:
            subprocess.run(["reg", "add", key, "/v", name, "/t", "REG_SZ",
                            "/d", val, "/f"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(["reg", "add", key, "/v", "EstimatedSize", "/t",
                        "REG_DWORD", "/d", "51200", "/f"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


# ------------------------------------------------------------------ 主流程
def install(dst: Path, silent: bool = False, launch: bool = True) -> int:
    log("=" * 58)
    log(f"  {APP_NAME} · 安装程序")
    log("=" * 58)
    log(f"安装目录：{dst}")

    if not (SRC / "main.py").exists() and not any(SRC.glob("*.exe")):
        log("")
        log("[错误] 找不到 main.py 或 按键精灵.exe，无法安装。")
        log("")
        log("  已查找过这些位置：")
        for p in candidate_roots()[:6]:
            log(f"    - {p}")
        log("")
        log("  请这样做：")
        log("    1. 右键压缩包 → 全部解压缩（不要直接在压缩包里双击）")
        log("    2. 打开解压出来的文件夹，确认里面能看到 main.py")
        log("    3. 再双击 安装按键精灵.bat")
        return 1

    if not silent and sys.stdin and sys.stdin.isatty():
        try:
            ans = input("\n开始安装？直接回车继续，输入 n 取消：").strip().lower()
            if ans == "n":
                log("已取消。")
                return 0
        except (EOFError, KeyboardInterrupt):
            pass

    log("\n[1/4] 复制程序文件…")
    n = copy_app(SRC, dst)
    log(f"      已复制 {n} 个文件")

    log("[2/4] 生成启动器…")
    exe = dst / f"{APP_NAME}.exe"
    if exe.exists():
        target = exe
        log("      检测到打包版 exe（文件夹版）")
    else:
        pw = find_pythonw()
        if not pw:
            log("      [!] 本机未找到 Python，且安装包内没有 exe。")
            log("        请先安装 Python 3.9+（勾选 Add to PATH），"
                "或改用打包好的 exe 版本安装包。")
            return 1
        launcher = write_launcher(dst, pw) or exe
        target = launcher
        log(f"      已创建启动器：{launcher.name}")

    log("[3/4] 创建快捷方式…")
    icon = dst / "assets" / "icon.ico"
    icon_arg = icon if icon.exists() else None

    # 快捷方式尽量指向无控制台窗口的方式
    if exe.exists():
        s_target, s_args = exe, ""
    else:
        pwlink = find_pythonw()
        if pwlink and target.suffix.lower() == ".bat":
            s_target, s_args = Path(pwlink), "main.py"
        else:
            s_target, s_args = target, ""
    ok_links = []
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    dcs = [home / "Desktop",
           home / "OneDrive" / "Desktop",
           home / "OneDrive" / "桌面",
           Path(os.environ.get("OneDrive", str(home))) / "Desktop"]
    desktop = next((d for d in dcs if d.exists()), home / "Desktop")
    if desktop.exists():
        if make_shortcut(desktop / f"{APP_NAME}.lnk", s_target,
                         workdir=dst, icon=icon_arg, desc=APP_NAME,
                         args=s_args):
            ok_links.append("桌面")
    start = (Path(os.environ.get("APPDATA", ""))
             / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if start.exists():
        if make_shortcut(start / f"{APP_NAME}.lnk", s_target,
                         workdir=dst, icon=icon_arg, desc=APP_NAME,
                         args=s_args):
            ok_links.append("开始菜单")
    log("      " + ("已创建：" + "、".join(ok_links) if ok_links
                   else "（非 Windows 或创建失败，可手动运行安装目录里的启动器）"))

    log("[4/4] 写入卸载信息…")
    write_uninstaller(dst)
    write_uninstall_reg(dst, target)
    log("      卸载：安装目录里的「卸载按键精灵.bat」")

    log("\n" + "=" * 58)
    log("安装完成  [OK]")
    log(f"  程序位置：{dst}")
    log(f"  启动方式：桌面 / 开始菜单的「{APP_NAME}」快捷方式")
    log("=" * 58)

    if launch and platform.system() == "Windows":
        try:
            if target.suffix.lower() == ".bat":
                pw_run = find_pythonw() or sys.executable
                subprocess.Popen([pw_run, "main.py"], cwd=str(dst),
                                 creationflags=getattr(subprocess,
                                                       "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen([str(target)], cwd=str(dst),
                                 creationflags=getattr(subprocess,
                                                       "CREATE_NO_WINDOW", 0))
            log("已启动程序。")
        except Exception as exc:
            log(f"自动启动失败（可双击快捷方式）：{exc}")
    return 0


def main() -> int:
    silent = "--silent" in sys.argv
    launch = "--no-launch" not in sys.argv
    dst = default_dir()
    if "--dir" in sys.argv:
        i = sys.argv.index("--dir")
        if i + 1 < len(sys.argv):
            dst = Path(sys.argv[i + 1])
    try:
        code = install(dst, silent=silent, launch=launch)
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        log(f"\n[安装失败] {exc}")
        code = 1
    if sys.stdin and sys.stdin.isatty() and os.environ.get("KEYMACRO_NO_PAUSE") != "1":
        try:
            input("\n按回车键关闭窗口…")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
