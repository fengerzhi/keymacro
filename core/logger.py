"""logger.py —— 执行日志与失败截图

每次回放生成一份日志（JSONL 逐行追加，崩溃也不丢已写入内容），
同时可在 UI 里实时显示。

日志结构
─────────────────────────────────────────
    ~/.macro-sprite/logs/
    ├── 20261029_213000_打卡签到.jsonl     # 每行一条记录
    └── screenshots/
        └── 20261029_213015_找图失败.png

每条记录
    {"ts": "21:30:01.234", "level": "info",
     "step": 3, "event": "单击左键 @ (760,440)",
     "result": "ok", "elapsed_ms": 12, "lap": 1}

设计要点
─────────────────────────────────────────
1. 逐行追加写：JSONL 格式，即使程序崩溃也能读到已完成部分
2. 失败截图：找图未命中/回放异常时自动截屏，路径写进日志
3. 环形缓冲：UI 侧只保留最近 N 行，避免长脚本把内存吃满
4. 优雅降级：截图依赖（PIL）缺失或 Displays 不可用时只记日志不报错
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .library import app_dir


# ================================================================ 截图
def captain_screen_via_tool(path: Path) -> bool:
    """降级方案：调用系统截图命令（PIL 在部分 X11 环境会失败）"""
    import platform
    import shutil
    import subprocess

    sysname = platform.system()
    try:
        if sysname == "Windows":
            return False                      # Windows 上 PIL 一般可用
        if sysname == "Darwin":
            if shutil.which("screencapture"):
                subprocess.run(["screencapture", "-x", str(path)],
                               check=True, timeout=5,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                return path.exists() and path.stat().st_size > 0
            return False
        # Linux：优先 scrot，其次 import(ImageMagick)，最后 xwd+convert
        if shutil.which("scrot"):
            subprocess.run(["scrot", "-o", str(path)], check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return path.exists() and path.stat().st_size > 0
        if shutil.which("import"):
            subprocess.run(["import", "-window", "root", str(path)],
                           check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return path.exists() and path.stat().st_size > 0
        if shutil.which("xwd") and shutil.which("convert"):
            r = subprocess.run(
                f'xwd -root -silent | convert xwd:- "{path}"',
                shell=True, timeout=8,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return r.returncode == 0 and path.exists() and path.stat().st_size > 0
    except Exception:
        return False
    return False


def capture_screen(tag: str = "fail") -> str | None:
    """截取当前屏幕，返回保存路径；失败返回 None

    两条路：PIL.ImageGrab（首选）→ 系统截图命令（降级）
    """
    shot_dir = app_dir() / "logs" / "screenshots"
    try:
        shot_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    stamp = time.strftime("%Y%m%d_%H%M%S")
    ms = int((time.time() % 1) * 1000)     # 防同秒覆盖
    alias = "".join(c for c in tag if c.isalnum() or c in "-_")[:30] or "shot"
    path = shot_dir / f"{stamp}_{ms:03d}_{alias}.png"

    # 首选 PIL
    try:
        from PIL import ImageGrab  # type: ignore
        ImageGrab.grab().save(str(path))
        if path.exists() and path.stat().st_size > 0:
            return str(path)
    except Exception:
        pass

    # 降级到系统命令
    if captain_screen_via_tool(path):
        return str(path)
    return None


# ================================================================ 日志
@dataclass
class LogEntry:
    ts: str                       # 时间 HH:MM:SS.mmm
    level: str                    # info / ok / warn / error
    message: str
    step: int | None = None
    lap: int | None = None
    elapsed_ms: int | None = None
    shot: str | None = None       # 失败截图路径

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def display(self) -> str:
        """UI 里的一行文本"""
        icon = {"ok": "✓", "warn": "!", "error": "✗"}.get(self.level, "·")
        parts = [self.ts, icon]
        if self.lap is not None and self.lap > 1:
            parts.append(f"[第{self.lap}轮]")
        if self.step is not None:
            parts.append(f"#{self.step}")
        parts.append(self.message)
        if self.elapsed_ms is not None and self.elapsed_ms >= 1:
            parts.append(f"({self.elapsed_ms}ms)")
        if self.shot:
            parts.append(f"📷 {Path(self.shot).name}")
        return " ".join(parts)


class RunLogger:
    """一次回放的日志记录器"""

    def __init__(self, script_name: str = "脚本",
                 on_entry: Callable[[LogEntry], None] | None = None,
                 to_file: bool = True):
        self.script_name = script_name
        self.on_entry = on_entry
        self.entries: list[LogEntry] = []
        self.error_count = 0
        self._fp = None
        self._t0 = time.perf_counter()
        self.log_path: str | None = None

        if to_file:
            try:
                log_dir = app_dir() / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                alias = "".join(c for c in script_name
                                if c.isalnum() or c in "-_")[:24] or "script"
                self.log_path = str(log_dir / f"{stamp}_{alias}.jsonl")
                self._fp = open(self.log_path, "a", encoding="utf-8")
            except Exception:
                self._fp = None

    # ---------------------------------------------------------- 记录
    def add(self, level: str, message: str, step: int | None = None,
            lap: int | None = None, elapsed_ms: int | None = None,
            shot: str | None = None) -> LogEntry:
        now = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(now)) + \
             f".{int((now % 1) * 1000):03d}"
        e = LogEntry(ts=ts, level=level, message=message, step=step,
                     lap=lap, elapsed_ms=elapsed_ms, shot=shot)
        self.entries.append(e)
        if level == "error":
            self.error_count += 1

        # 落盘（逐行，崩溃也不丢）
        if self._fp:
            try:
                self._fp.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
                self._fp.flush()
            except Exception:
                pass

        if self.on_entry:
            try:
                self.on_entry(e)
            except Exception:
                pass
        return e

    # 便捷方法
    def info(self, msg: str, **kw) -> LogEntry:
        return self.add("info", msg, **kw)

    def ok(self, msg: str, **kw) -> LogEntry:
        return self.add("ok", msg, **kw)

    def warn(self, msg: str, **kw) -> LogEntry:
        return self.add("warn", msg, **kw)

    def error(self, msg: str, with_shot: bool = True,
              shot_tag: str = "fail", **kw) -> LogEntry:
        """记录错误，默认自动截图存证"""
        shot = capture_screen(tag=shot_tag) if with_shot else None
        return self.add("error", msg, shot=shot, **kw)

    def step_start(self, step: int, desc: str, lap: int = 1) -> float:
        return time.perf_counter()

    def step_done(self, step: int, desc: str, t_start: float,
                  result: str = "ok", lap: int = 1) -> LogEntry:
        elapsed = int((time.perf_counter() - t_start) * 1000)
        level = "ok" if result == "ok" else ("warn" if "未命中" in result else "error")
        return self.add(level, desc, step=step, lap=lap, elapsed_ms=elapsed)

    @property
    def total_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    # ---------------------------------------------------------- 收尾
    def close(self) -> None:
        if self._fp:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def summary(self) -> str:
        return (f"共 {len(self.entries)} 条记录，"
                f"错误 {self.error_count} 个，耗时 {self.total_ms/1000:.1f}s")


# ================================================================ 历史日志
def list_logs(limit: int = 50) -> list[dict[str, Any]]:
    """列出历史日志文件（按时间倒序）"""
    log_dir = app_dir() / "logs"
    if not log_dir.exists():
        return []
    items = []
    for p in sorted(log_dir.glob("*.jsonl"), reverse=True)[:limit]:
        st = p.stat()
        items.append({
            "path": str(p),
            "name": p.stem,
            "size": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(st.st_mtime)),
        })
    return items


def read_log(path: str, limit: int = 500) -> list[dict[str, Any]]:
    """读取日志文件内容"""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
                if len(out) >= limit:
                    break
    except Exception:
        return []
    return out


def cleanup_logs(keep_days: int = 30) -> int:
    """清理过期日志与截图，返回删除数量"""
    log_dir = app_dir() / "logs"
    if not log_dir.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    n = 0
    for pattern in ("*.jsonl", "screenshots/*.png"):
        for p in log_dir.glob(pattern):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except Exception:
                pass
    return n
