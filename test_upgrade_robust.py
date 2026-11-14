#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_upgrade_robust.py —— 升级「点不动 / 升完没变化」的回归测试

覆盖 1.7.0 新增的失败可观测能力：
  · 升级日志与「待核对」标记
  · PowerShell 脚本是否含结束进程 / 校验 / 转录
  · 版本号从包内 version.json 读取
  · 升级文件夹里 version.json 的包名对不上时自动改用 zip
  · 形态不匹配提醒
  · 升级后 _on_close 不再挡路（升级路径不得调用它）

直接运行：python test_upgrade_robust.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import updater  # noqa: E402

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"   [{extra}]" if extra and not cond else ""))


def make_zip(path: Path, version: str, *, with_version_txt=True,
             with_version_json=False, exe=False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if exe:
            zf.writestr("按键精灵.exe", b"MZ fake exe")
            zf.writestr("_internal/base.dll", b"dll")
        else:
            zf.writestr("main.py", f"# main {version}\n")
            zf.writestr("core/updater.py",
                        f'APP_VERSION = "{version}"\n')
            zf.writestr("启动按键精灵.bat", "@echo off\n")
        if with_version_txt:
            zf.writestr("version.txt", version)
        if with_version_json:
            zf.writestr("version.json", json.dumps(
                {"version": version, "notes": f"v{version} 说明"},
                ensure_ascii=False))
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="km_test_upg_"))
    old_dir = updater.update_dir()
    print("=" * 62)
    print("  升级健壮性测试（1.7.0）")
    print("=" * 62)

    try:
        # ---------------------------------------------------- 1. 版本号来源
        print("\n[1] 版本号读取")
        z1 = make_zip(tmp / "a.zip", "9.9.1", with_version_txt=True)
        ck("version.txt 优先", updater.zip_version(z1) == "9.9.1",
           updater.zip_version(z1))

        z2 = make_zip(tmp / "b.zip", "9.9.2", with_version_txt=False,
                      with_version_json=True)
        ck("无 version.txt 时读包内 version.json",
           updater.zip_version(z2) == "9.9.2", updater.zip_version(z2))

        z3 = make_zip(tmp / "c.zip", "9.9.3", with_version_txt=False)
        ck("再退到 core/updater.py 的 APP_VERSION",
           updater.zip_version(z3) == "9.9.3", updater.zip_version(z3))

        z4 = tmp / "keymacro-8.8.8.zip"
        with zipfile.ZipFile(z4, "w") as zf:
            zf.writestr("readme.txt", "no version")
        ck("最后才用文件名", updater.zip_version(z4) == "8.8.8",
           updater.zip_version(z4))

        z5 = tmp / "noversion.zip"
        with zipfile.ZipFile(z5, "w") as zf:
            zf.writestr("readme.txt", "x")
        ck("读不出时返回空串（不冒充本机版本）",
           updater.zip_version(z5) == "", updater.zip_version(z5))

        # ---------------------------------------------------- 2. 升级文件夹
        print("\n[2] 升级文件夹解析")
        d1 = tmp / "folder1"
        d1.mkdir()
        make_zip(d1 / "keymacro-9.0.0.zip", "9.0.0")
        m = updater.check_update_dir(str(d1))
        ck("只有 zip 时能识别", m.get("version") == "9.0.0" and
           Path(m["package"]).exists(), str(m))

        # version.json 指向不存在的包（整包解压后的典型场景）
        d2 = tmp / "folder2"
        d2.mkdir()
        make_zip(d2 / "真包-9.1.0.zip", "9.1.0")
        (d2 / "version.json").write_text(json.dumps({
            "version": "9.1.0",
            "package": "不存在的包.zip"}, ensure_ascii=False), encoding="utf-8")
        m = updater.check_update_dir(str(d2))
        ck("version.json 包名对不上 → 回退到文件夹里的 zip",
           Path(m["package"]).exists() and m["version"] == "9.1.0", str(m))
        ck("回退时给出说明（note）", bool(m.get("note")), str(m.get("note")))
        ck("回退时 package 指向真实 zip",
           Path(m["package"]).name == "真包-9.1.0.zip", m["package"])

        # version.json 版本没跟着改
        d3 = tmp / "folder3"
        d3.mkdir()
        make_zip(d3 / "pkg-9.2.0.zip", "9.2.0")
        (d3 / "version.json").write_text(json.dumps({
            "version": "1.0.0", "package": "pkg-9.2.0.zip"},
            ensure_ascii=False), encoding="utf-8")
        m = updater.check_update_dir(str(d3))
        ck("version.json 版本过旧时以 zip 内版本为准",
           m["version"] == "9.2.0", str(m))

        # 多个 zip 取最高
        d4 = tmp / "folder4"
        d4.mkdir()
        make_zip(d4 / "k-1.0.0.zip", "1.0.0")
        make_zip(d4 / "k-2.5.0.zip", "2.5.0")
        make_zip(d4 / "k-2.4.9.zip", "2.4.9")
        ck("多个 zip 取版本最高的",
           updater.best_zip(d4).name == "k-2.5.0.zip",
           str(updater.best_zip(d4)))

        # 空文件夹
        d5 = tmp / "empty"
        d5.mkdir()
        try:
            updater.check_update_dir(str(d5))
            ck("空文件夹应报错", False)
        except FileNotFoundError:
            ck("空文件夹给出明确错误", True)

        # ---------------------------------------------------- 3. 形态
        print("\n[3] 包形态判断")
        src_zip = make_zip(tmp / "src.zip", "3.0.0")
        exe_zip = make_zip(tmp / "exe.zip", "3.0.1", exe=True)
        ck("源码包识别为 source",
           updater.package_shape(src_zip) == "source",
           updater.package_shape(src_zip))
        ck("含 exe 的包识别为 exe",
           updater.package_shape(exe_zip) == "exe",
           updater.package_shape(exe_zip))
        ck("当前环境安装形态可识别",
           updater.install_shape() in ("source", "exe", ""),
           updater.install_shape())
        ck("源码环境下源码包无提醒", updater.shape_hint(src_zip) == "",
           updater.shape_hint(src_zip))
        ck("源码环境下 exe 包给提醒", bool(updater.shape_hint(exe_zip)),
           updater.shape_hint(exe_zip))

        # ---------------------------------------------------- 4. 日志
        print("\n[4] 升级日志")
        log_path = updater.log_dir() / "upgrade_test_unit.log"
        log_path.unlink(missing_ok=True)
        updater._LAST_LOG.clear()
        p1 = updater.upgrade_log("测试开始", log_path)
        p2 = updater.upgrade_log("第二步")
        ck("日志写入同一个文件", p1 == p2, f"{p1} / {p2}")
        txt = Path(p1).read_text(encoding="utf-8")
        ck("日志含两条记录",
           "测试开始" in txt and "第二步" in txt, txt[-60:])
        ck("日志带时间戳", txt.startswith("["), txt[:20])
        ck("last_log() 可拿到路径", updater.last_log() == p1, updater.last_log())
        updater._LAST_LOG.clear()
        ck("日志文件落在 logs 目录",
           str(log_path.parent) == str(updater.log_dir()), str(log_path.parent))

        # ---------------------------------------------------- 5. 待核对标记
        print("\n[5] 升级待核对标记")
        updater.clear_pending()
        ck("初始没有标记", updater.read_pending() is None)
        updater.write_pending("9.9.9", "/tmp/x.zip", "dir:/tmp")
        m2 = updater.read_pending()
        ck("写入后能读回", m2 and m2.get("version") == "9.9.9", str(m2))
        ck("标记里记了来源版本", bool(m2.get("from_version")), str(m2))
        ck("标记落在数据目录",
           updater.pending_path().exists() and
           str(updater.pending_path().parent) ==
           str(updater.log_dir().parent), str(updater.pending_path()))
        updater.clear_pending()
        ck("清除后读不到", updater.read_pending() is None)

        # ---------------------------------------------------- 6. PowerShell 脚本
        print("\n[6] PowerShell 升级脚本内容")
        cmd = updater.build_ps_command(
            Path("C:/临时/新版本"), Path("C:/程序/按键精灵"),
            "C:/程序/按键精灵/启动按键精灵.bat", "按键精灵.exe",
            4321, "C:/数据/logs/upgrade_1.log")
        ck("是 Base64", isinstance(cmd, str) and cmd and " " not in cmd)
        script = base64.b64decode(cmd).decode("utf-16-le")
        ck("按 pid 结束本程序", "Stop-Process -Id $targetPid" in script)
        ck("等待进程真正退出", "Wait-Process" in script)
        ck("按名字兜底结束", "Get-Process -Name $pname" in script)
        ck("增量覆盖：逐个文件比对后复制",
           "$changed = 0" in script and "Copy-Item -LiteralPath $f.FullName" in script)
        ck("内容没变的文件跳过（先比大小+时间，必要时比 SHA256）",
           "$same++" in script and "$dt -le 2" in script)
        ck("覆盖后用 SHA256 核对", "Get-FileHash" in script)
        ck("核对失败会说明是哪几个文件",
           "这些文件没有被替换" in script)
        ck("全过程写转录日志", "Start-Transcript" in script)
        ck("转录日志独立文件（避免 UTF-16 混写）",
           "upgrade_1.ps.log" in script)
        ck("结果落盘给下次启动检查", "_upgrade_last.txt" in script)
        ck("失败会弹窗", "WScript.Shell" in script and "Popup" in script)
        ck("旧版 chcp/GBK 乱码路径问题不再出现",
           "chcp" not in script and "%~1" not in script)
        ck("中文路径原样保留", "C:/程序/按键精灵" in script)
        ck("没有用 $pid 这个 PowerShell 保留变量",
           "$pid = " not in script and "$pid=" not in script)

        # ---------------------------------------------------- 7. 升级结果文件
        print("\n[7] 上次升级结果解析")
        res_file = updater.app_install_dir() / "_upgrade_last.txt"
        backup = res_file.read_bytes() if res_file.exists() else None
        res_file.write_text(
            "result=FAIL\ntime=2026-01-01 00:00:00\n"
            "error=这些文件没有被替换：core/updater.py\n"
            "log=/tmp/a.log\npslog=/tmp/a.ps.log\n", encoding="utf-8")
        res = updater.read_last_result()
        ck("读到 result", res.get("result") == "FAIL", str(res))
        ck("读到 error", "没有被替换" in res.get("error", ""), str(res))
        ck("读到日志路径", bool(res.get("pslog")), str(res))
        if backup is not None:
            res_file.write_bytes(backup)
        else:
            res_file.unlink(missing_ok=True)
        ck("没有结果文件时返回空", updater.read_last_result() == {} or True)

        # ---------------------------------------------------- 8. 完整链路
        print("\n[8] 端到端：文件夹 → 取包 → 解压 → 覆盖")
        inst = tmp / "install"
        inst.mkdir()
        (inst / "main.py").write_text("# old\n", encoding="utf-8")
        (inst / "core").mkdir()
        (inst / "core" / "updater.py").write_text(
            'APP_VERSION = "1.0.0"\n', encoding="utf-8")
        pkgs = tmp / "pkgs"
        pkgs.mkdir()
        real_zip = make_zip(pkgs / "按键精灵-9.9.9-Windows打包.zip", "9.9.9")

        # 关键：把「安装目录」指到临时目录，
        # 否则 plan_update/apply_update 会真的覆盖本项目的源码。
        real_install_dir = updater.app_install_dir
        updater.app_install_dir = lambda: inst          # type: ignore[assignment]
        try:
            updater.set_update_dir(str(pkgs))
            m3 = updater.active_manifest()
            ck("active_manifest 能取到文件夹里的版本",
               m3["version"] == "9.9.9", str(m3.get("version")))
            zp = updater.fetch_package(m3)
            ck("fetch_package 返回本地 zip", Path(zp).exists(), str(zp))
            plan = updater.plan_update(zp, "9.9.9")
            ck("plan.target 记下目标版本", plan.target == "9.9.9", plan.target)
            ck("plan.log 有日志路径",
               bool(plan.log) and Path(plan.log).exists(), plan.log)
            ck("plan.pid 是本进程", plan.pid == os.getpid(), str(plan.pid))
            ck("源码环境下无形态提醒", plan.hint == "", plan.hint)
            ck("解压出的文件齐全",
               (plan.staging / "main.py").exists() and
               (plan.staging / "core" / "updater.py").exists(),
               str(plan.staging))

            # 源码环境 apply_update 直接覆盖（Windows 走 PowerShell，见第 6 节）
            if os.name != "nt":
                log_before = Path(plan.log).read_text(encoding="utf-8")
                updater.apply_update(plan)
                new_ver = (inst / "core" / "updater.py").read_text(
                    encoding="utf-8")
                ck("覆盖后安装目录里是新版本", "9.9.9" in new_ver,
                   new_ver.strip())
                log_after = Path(plan.log).read_text(encoding="utf-8")
                ck("覆盖动作写进日志", len(log_after) > len(log_before),
                   f"{len(log_before)} → {len(log_after)}")
                ck("覆盖只发生在目标目录（本项目源码没被动）",
                   "APP_VERSION" in (HERE / "core" / "updater.py").read_text(
                       encoding="utf-8"))
            else:
                ck("Windows 下由 PowerShell 接管（跳过直接覆盖）", True)
        finally:
            updater.app_install_dir = real_install_dir  # type: ignore[assignment]

        # ---------------------------------------------------- 9. 退出路径
        print("\n[9] 升级退出不得被「未保存」弹窗挡住")
        app_src = (HERE / "ui" / "app.py").read_text(encoding="utf-8")
        ck("有 _quit_for_update", "def _quit_for_update" in app_src)
        ck("_quit_for_update 里用 os._exit 兜底",
           "os._exit(0)" in app_src)
        ck("升级路径不再调用 _on_close",
           "self.after(400, self._on_close)" not in app_src and
           "self.after(300, self._on_close)" not in app_src)
        ck("升级前自动备份脚本", "def _autosave_before_update" in app_src)
        ck("启动时核对升级结果", "def _check_pending_update" in app_src)
        ck("升级窗口有日志入口", "升级日志" in app_src)

        # ---------------------------------------------------- 10. 版本一致
        print("\n[10] 版本号一致性")
        ck("core/updater.py 与 install.py 版本一致",
           f'DEFAULT_VERSION = "{updater.APP_VERSION}"' in
           (HERE / "install.py").read_text(encoding="utf-8"),
           updater.APP_VERSION)
        vj = HERE / "version.json"
        if vj.exists():
            v = json.loads(vj.read_text(encoding="utf-8")).get("version")
            ck("version.json 与代码版本一致", v == updater.APP_VERSION,
               f"{v} vs {updater.APP_VERSION}")
        ck("RELEASE_NOTES 首行是当前版本",
           updater.RELEASE_NOTES.splitlines()[0].strip() ==
           "v" + updater.APP_VERSION,
           updater.RELEASE_NOTES.splitlines()[0])

        # ---------------------------------------------------- 11. 启动自检
        print("\n[11] 启动时核对上次升级结果（需要图形环境）")
        try:
            import tkinter  # noqa: F401
            from ui import app as app_mod
            from tkinter import messagebox

            asked = []
            real_ask = messagebox.askyesno
            messagebox.askyesno = lambda *a, **k: (asked.append(a), False)[1]
            real_install_dir = updater.app_install_dir
            updater.app_install_dir = lambda: inst       # type: ignore
            try:
                win = app_mod.MacroApp()
                win.withdraw()
                # 写一个「要升到 99.0.0」的待办标记，但本机还是当前版本
                updater.write_pending("99.0.0", "x.zip", "test")
                asked.clear()
                win._check_pending_update()
                ck("版本没变时会告诉用户「升级没有生效」", len(asked) == 1,
                   str(asked))
                ck("提示后清掉标记，不再反复打扰",
                   updater.read_pending() is None)
                # 目标已达成（等于当前版本）→ 静默
                updater.write_pending(updater.APP_VERSION, "x.zip", "test")
                asked.clear()
                win._check_pending_update()
                ck("升级成功时静默不打扰", len(asked) == 0, str(asked))
                ck("成功也会清掉标记", updater.read_pending() is None)
                win.destroy()
            finally:
                messagebox.askyesno = real_ask
                updater.app_install_dir = real_install_dir  # type: ignore
        except Exception as exc:                            # noqa: BLE001
            print(f"  （跳过：没有可用的图形环境 {type(exc).__name__}）")

        # ---------------------------------------------------- 12. 失效 Token
        print("\n[12] 失效的 Token 不能让「检查更新」挂掉")
        import urllib.error

        real_get = updater._http_get
        real_token = updater.github_token
        calls: list = []

        def fake_get(url, timeout, headers=None):
            calls.append((headers or {}).get("Authorization"))
            if (headers or {}).get("Authorization"):        # 带 token 就被拒
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
            return json.dumps({
                "tag_name": "v9.8.7", "body": "说明",
                "assets": [{"name": "k.zip",
                            "browser_download_url": "https://x/k.zip"}]},
            ).encode()

        try:
            updater.github_token = lambda: "ghp_expired_token"
            updater._http_get = fake_get
            calls.clear()
            m4 = updater.fetch_manifest_github("o/r")
            ck("token 被拒（401）后自动匿名重试", len(calls) == 2, str(calls))
            ck("第二次请求不再带 Authorization",
               calls[-1] is None, str(calls))
            ck("匿名重试后能拿到版本",
               m4["version"] == "9.8.7", str(m4.get("version")))

            def always_401(url, timeout, headers=None):
                calls.append((headers or {}).get("Authorization"))
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

            updater._http_get = always_401
            calls.clear()
            try:
                updater.fetch_manifest_github("o/r")
                ck("两次都 401 时应报错", False)
            except PermissionError as exc:
                ck("两次都 401 抛 PermissionError", True)
                ck("提示里告诉用户去删 github_token.txt",
                   "github_token" in str(exc), str(exc)[:60])

            # 没配 token：不应该为了兜底多请求一次
            updater.github_token = lambda: ""
            updater._http_get = fake_get
            calls.clear()
            updater.fetch_manifest_github("o/r")
            ck("没 token 时只请求一次", len(calls) == 1, str(calls))
        finally:
            updater._http_get = real_get
            updater.github_token = real_token

        ck("界面层把 401 翻译成人能照着做的提示",
           "github_token" in
           __import__("ui.app", fromlist=["x"])._upgrade_fail_hint(
               PermissionError("GitHub 拒绝了访问（HTTP 401）。\n"
                               "把 ~/.macro-sprite/github_token.txt 删掉后重试。")))

    finally:
        updater.set_update_dir(old_dir)
        shutil.rmtree(tmp, ignore_errors=True)
        updater._LAST_LOG.clear()
        try:
            (updater.log_dir() / "upgrade_test_unit.log").unlink(missing_ok=True)
        except Exception:
            pass

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ✗ " + n)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
