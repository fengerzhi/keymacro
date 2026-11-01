"""一键覆盖更新.py —— 把本文件夹（新版）覆盖到旧版程序目录

用途：程序里的「升级」按钮受旧版逻辑限制，可能失败。
      把新版解压出来，双击「一键覆盖更新.bat」，选一下旧版所在文件夹即可。

纯 Python 复制文件，不经过 cmd / bat 的代码页，中文/空格/括号路径都不会乱码。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", "logs", "dist", "build", ".git", ".idea",
             ".macro-sprite", "venv", ".venv", "node_modules", "update_backup"}


def _ui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    return tk, filedialog, messagebox


def copy_tree(src: Path, dst: Path) -> tuple[int, list[str]]:
    """把 src 下的程序文件覆盖到 dst（跳过日志/缓存/打包产物）"""
    total, failed = 0, []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = Path(dirpath).relative_to(src)
        target_dir = dst / rel if str(rel) != "." else dst
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            failed.append(f"{rel}: {exc}")
            continue
        for name in filenames:
            if name.startswith("."):
                continue
            try:
                shutil.copy2(Path(dirpath) / name, target_dir / name)
                total += 1
            except Exception as exc:
                failed.append(f"{rel / name}: {exc}")
    return total, failed


def main() -> int:
    tk, filedialog, messagebox = _ui()
    here = Path(__file__).resolve().parent
    if not (here / "main.py").exists():
        messagebox.showerror(
            "放错位置了",
            "请把「一键覆盖更新.bat」和「一键覆盖更新.py」\n"
            "放在新版程序的文件夹里（和 main.py 同一层）。")
        return 1

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    messagebox.showinfo(
        "覆盖更新",
        "接下来请选择【旧版程序所在的文件夹】。\n\n"
        "怎么找：在旧版程序里点工具栏的「打开所在文件夹」，\n"
        "或右键桌面快捷方式 → 打开文件所在位置。\n"
        "那个文件夹里应该有 main.py。")
    dst = filedialog.askdirectory(title="选择旧版「按键精灵」所在的文件夹")
    if not dst:
        return 0
    dst = Path(dst)
    if dst.resolve() == here.resolve():
        messagebox.showinfo("提示", "选的就是当前文件夹，不需要覆盖。")
        return 0
    if not (dst / "main.py").exists():
        if not messagebox.askyesno(
                "确认",
                "这个文件夹里没有 main.py，可能选错了。\n\n仍然继续复制吗？"):
            return 0

    total, failed = copy_tree(here, dst)

    msg = f"已复制 {total} 个文件到：\n{dst}"
    if failed:
        msg += (f"\n\n有 {len(failed)} 个失败（文件被占用？先关掉按键精灵再试）：\n"
                + "\n".join(failed[:6]))
    messagebox.showinfo("完成", msg)

    if failed:
        return 1
    if messagebox.askyesno("启动", "覆盖完成，现在启动新版本吗？"):
        starter = dst / "启动按键精灵.bat"
        try:
            if starter.exists():
                os.startfile(str(starter))       # type: ignore[attr-defined]
            else:
                os.startfile(str(dst / "main.py"))   # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showwarning("启动失败", f"请手动双击启动脚本。\n{exc}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:      # 兜底：别让窗口一闪而过
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("出错", str(exc))
        sys.exit(1)
