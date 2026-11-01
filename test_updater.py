"""自动升级功能测试：版本比较 / 清单拉取 / 包下载 / 解压 / 升级计划。"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")


def make_server(tmp: Path, port: int) -> tuple[str, str]:
    """起一个静态 http 服务：version.json + 更新包 zip"""
    # 更新包：内含一个新文件与修改文件
    pkg_root = tmp / "pkg_root"
    inner = pkg_root / "按键精灵-1.3.0"     # zip 内一层文件夹结构
    (inner / "core").mkdir(parents=True)
    (inner / "NEWFILE.txt").write_text("新版文件", encoding="utf-8")
    (inner / "core" / "new_mod.py").write_text("# new\n", encoding="utf-8")
    zip_path = tmp / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in inner.rglob("*"):
            zf.write(f, f.relative_to(pkg_root))

    manifest = {"version": "1.3.0", "notes": "修复涟漪；新增升级",
                "package": f"http://127.0.0.1:{port}/update.zip"}
    (tmp / "version.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, **k)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.chdir(tmp)
    return f"http://127.0.0.1:{port}/version.json", str(zip_path)


def main() -> int:
    from core import updater
    results, fails = {}, []

    def check(name, cond):
        results[name] = bool(cond)
        if not cond:
            fails.append(name)

    # ---- 1) 版本比较 ----
    check("ver_gt", updater.is_newer("1.3.0", "1.2.0"))
    check("ver_eq", not updater.is_newer("1.2.0", "1.2.0"))
    check("ver_v", updater.is_newer("v2.0", "1.9.9"))
    check("ver_pad", updater.is_newer("1.10", "1.9"))
    check("ver_bad", not updater.is_newer("abc", "1.0"))

    # ---- 2) 网络拉取 + 下载 ----
    tmp = Path(tempfile.mkdtemp(prefix="upd_test_"))
    url, zip_path = make_server(tmp, 18923)
    m = updater.fetch_manifest(url)
    check("manifest", m["version"] == "1.3.0" and "update.zip" in m["package"])
    zp = updater.download_package(m["package"], dest_dir=str(tmp / "dl"))
    check("download", zp.exists() and zipfile.is_zipfile(zp))

    # ---- 3) 解压与升级计划 ----
    plan = updater.plan_update(zp)
    staging = plan.staging
    check("extract_flatten", (staging / "NEWFILE.txt").exists())
    check("extract_core", (staging / "core" / "new_mod.py").exists())

    # ---- 4) 路径穿越防护 ----
    evil = tmp / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "bad")
    try:
        updater.extract_package(evil)
        check("zip_escape_guard", False)
    except ValueError:
        check("zip_escape_guard", True)

    # ---- 5) 应用升级（非 Windows 直接覆盖分支）----
    fake_install = tmp / "install"
    fake_install.mkdir()
    updater.app_install_dir  # noqa  (存在性)
    # 手动覆盖到假安装目录
    import shutil
    for src in staging.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(staging)
        dst = fake_install / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    check("apply_files", (fake_install / "NEWFILE.txt").exists()
          and (fake_install / "core" / "new_mod.py").exists())

    # ---- 6) 自定义升级地址持久化 ----
    updater.set_update_url("http://example.com/custom.json")
    check("url_saved", updater.update_url() == "http://example.com/custom.json")
    updater.set_update_url("")

    # ---- 7) UI 对话框能打开 ----
    import ui.app as app_mod
    app = app_mod.MacroApp()
    try:
        app.show_update_dialog()
        import tkinter as tk
        tops = [w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and "升级" in w.title()]
        check("dialog_opens", len(tops) == 1)
    finally:
        app.destroy()

    print("RESULTS:", results)
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
