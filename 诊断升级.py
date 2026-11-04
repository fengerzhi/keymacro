#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断升级.py —— 双击即可，检查「为什么点了升级没反应」

它只做读取和自检，不改任何文件。把生成的报告发给维护者即可定位问题。

用法：
    双击本文件；或  python 诊断升级.py
报告位置：
    程序数据目录\\logs\\诊断_时间.txt（窗口里也会显示路径）
"""
from __future__ import annotations

import os
import platform
import sys
import time
import traceback
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def line(title: str) -> str:
    return "\n" + "=" * 62 + f"\n{title}\n" + "=" * 62


def collect() -> str:
    from core import updater

    out: list[str] = []

    out.append(line("1. 运行环境"))
    out.append(f"时间      ：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    out.append(f"系统      ：{platform.platform()}")
    out.append(f"Python    ：{sys.version.split()[0]}  可执行：{sys.executable}")
    out.append(f"打包运行  ：{bool(getattr(sys, 'frozen', False))}")
    out.append(f"脚本位置  ：{HERE}")
    out.append(f"当前版本  ：v{updater.APP_VERSION}")
    out.append(f"安装目录  ：{updater.app_install_dir()}")
    out.append(f"安装形态  ：{updater.install_shape() or '未知'}")
    out.append(f"数据目录  ：{updater.log_dir().parent}")

    out.append(line("2. 升级来源"))
    d = updater.update_dir()
    u = updater.update_url()
    out.append(f"升级文件夹：{d or '(未设置)'}")
    out.append(f"升级网址  ：{u or '(未设置)'}")
    out.append(f"GitHub 仓库：{updater.github_repo() or '(未设置)'}")
    try:
        out.append(f"has_source()：{updater.has_source()}")
    except Exception as exc:
        out.append(f"has_source() 出错：{exc}")

    out.append(line("3. 升级文件夹里有什么"))
    folder = Path(d).expanduser() if d else None
    if folder is None:
        out.append("没有设置升级文件夹，跳过。")
    elif not folder.exists():
        out.append(f"✗ 文件夹不存在：{folder}")
    else:
        try:
            items = sorted(folder.iterdir(), key=lambda p: p.name)
        except Exception as exc:
            items = []
            out.append(f"✗ 读不了目录：{exc}")
        for p in items:
            try:
                size = f"{p.stat().st_size / 1024:.0f} KB" if p.is_file() else "<目录>"
            except Exception:
                size = "?"
            out.append(f"  {p.name}   [{size}]")
            if p.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(p) as zf:
                        names = zf.namelist()
                    ver = updater.zip_version(p)
                    shape = updater.package_shape(p)
                    has_main = any(n.replace("\\", "/").rsplit("/", 1)[-1] == "main.py"
                                   for n in names)
                    out.append(f"      条目 {len(names)} 个，版本={ver or '(读不出)'}，"
                               f"形态={shape or '未知'}，含 main.py={has_main}")
                    if "version.txt" in names:
                        out.append("      含 version.txt：是")
                    if "version.json" in names:
                        out.append("      含 version.json：是")
                except Exception as exc:
                    out.append(f"      ✗ 不是有效的 zip：{exc}")

    out.append(line("4. 程序看到的「最新版本」"))
    try:
        m = updater.active_manifest() if updater.has_source() else {}
        if not m:
            out.append("没有配置升级来源。")
        else:
            out.append(f"version ：{m.get('version') or '(空)'}")
            out.append(f"package ：{m.get('package') or '(空)'}")
            out.append(f"source  ：{m.get('source') or '(空)'}")
            out.append(f"note    ：{m.get('note') or '(无)'}")
            ver = str(m.get("version") or "")
            if ver:
                newer = updater.is_newer(ver, updater.APP_VERSION)
                out.append(f"是否比本机新：{newer}"
                           f"（{ver} vs {updater.APP_VERSION}）")
            else:
                out.append("⚠ 读不出版本号：会按「已是最新版本」处理，"
                           "不会覆盖安装（包里缺 version.txt / version.json）")
    except Exception as exc:
        out.append(f"✗ 检查失败：{exc}")
        out.append(traceback.format_exc(limit=3))

    out.append(line("5. 升级包能否取到 / 能否解开"))
    try:
        m = updater.active_manifest() if updater.has_source() else {}
        pkg = str(m.get("package") or "")
        if not pkg:
            out.append("没有升级包。")
        elif pkg.lower().startswith(("http://", "https://")):
            out.append(f"是网址，需要联网下载：{pkg}")
        else:
            p = Path(pkg)
            out.append(f"本地文件：{p}  存在={p.exists()}")
            if p.exists():
                hint = updater.shape_hint(p)
                out.append(f"形态提醒：{hint or '(没问题)'}")
                staging = updater.extract_package(p)
                cnt = sum(1 for _ in staging.rglob("*") if _.is_file())
                out.append(f"✓ 解压成功，{cnt} 个文件 → {staging}")
    except Exception as exc:
        out.append(f"✗ 失败：{exc}")
        out.append(traceback.format_exc(limit=3))

    out.append(line("6. 上一次升级的结果"))
    res = updater.read_last_result()
    if res:
        for k, v in res.items():
            out.append(f"{k} = {v}")
    else:
        out.append("没有 _upgrade_last.txt（说明升级脚本没跑到最后一步）")
    pend = updater.read_pending()
    out.append(f"待核对标记：{pend if pend else '(无)'}")

    out.append(line("7. 日志文件"))
    try:
        logs = sorted(updater.log_dir().glob("upgrade_*.log"))
        if not logs:
            out.append("还没有升级日志（说明从未点过升级）")
        for p in logs[-4:]:
            out.append(f"\n---- {p.name} ----")
            try:
                raw = p.read_bytes()
                # PowerShell 转录是 UTF-16，Python 写的是 UTF-8
                txt = ""
                for enc in ("utf-8", "utf-16", "gbk"):
                    try:
                        t = raw.decode(enc)
                    except Exception:
                        continue
                    if t.count("\x00") == 0:
                        txt = t
                        break
                if not txt:
                    txt = raw.decode("utf-8", "replace").replace("\x00", "")
                out.extend(txt.splitlines()[-40:])
            except Exception as exc:
                out.append(f"(读不了：{exc})")
    except Exception as exc:
        out.append(f"(读不了：{exc})")

    return "\n".join(out)


def main() -> int:
    print("按键精灵 · 升级诊断\n正在收集信息，请稍候…")
    try:
        report = collect()
    except Exception:
        report = "诊断本身出错了：\n" + traceback.format_exc()

    target = ""
    try:
        from core import updater
        target = str(updater.log_dir() / f"诊断_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        Path(target).write_text(report, encoding="utf-8")
    except Exception:
        target = ""

    print(report)
    print("\n" + "=" * 62)
    if target:
        print(f"报告已保存：{target}")
    print("=" * 62)
    try:
        input("\n按回车键关闭窗口…")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
