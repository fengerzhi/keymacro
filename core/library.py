"""library.py —— 脚本库

脚本直接存放在**程序安装目录下的「脚本」文件夹**里，打开「目录」就能看到、
直接改文件，不用再去 AppData 里翻。

存储结构
─────────────────────────────────────────
    <安装目录>/                     # 打包版 = exe 所在目录
    ├── 按键精灵.exe
    └── 脚本/
        ├── index.json          # 元数据索引（名称/时间/事件数/使用次数）
        ├── 打卡签到.json
        └── 数据导出.json

    ~/.macro-sprite/                # 用户数据（配置/日志/回收站）
    ├── logs/
    │   └── screenshots/
    ├── trash/                      # 删除的脚本先挪到这里
    └── config.json                 # 应用级配置（热键等）

设计要点
─────────────────────────────────────────
1. 以文件为真相源：index.json 只是加速缓存，损坏或缺失时可从
   目录里的 .json 文件自动重建，不会丢脚本。
2. 文件夹即接口：往「脚本」文件夹里丢 .json、或直接改文件，列表刷新就能看到。
3. 安装目录不可写（例如装到了 Program Files）时自动退回用户目录，
   不会因为一处没权限就存不了脚本。
4. 文件名安全化 + 重名保护：脚本名可含中文/空格，同名自动加序号。
5. 原子写入：先写临时文件再替换，避免中途崩溃导致文件损坏。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .script_model import Script

APP_DIR_NAME = ".macro-sprite"

# 安装目录下放脚本的文件夹名
SCRIPTS_DIR_NAME = "脚本"

# 文件名非法字符（Windows 更严格，统一按最严标准过滤）
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def install_dir() -> Path:
    """程序安装目录（打包版 = exe 所在目录；源码版 = 项目根）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """应用数据目录（配置 / 日志 / 回收站）"""
    d = Path.home() / APP_DIR_NAME
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "logs" / "screenshots").mkdir(parents=True, exist_ok=True)
    return d


def _writable(d: Path) -> bool:
    """目录能不能真的写进去（能建目录 ≠ 能建文件）"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".write_test_{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def scripts_dir() -> Path:
    """脚本库目录：优先 `<安装目录>/脚本`，不可写时退回用户目录"""
    cand = install_dir() / SCRIPTS_DIR_NAME
    if _writable(cand):
        return cand
    d = app_dir() / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(name: str, fallback: str = "未命名脚本") -> str:
    """把用户输入的名称转成安全文件名（不含扩展名）"""
    n = _ILLEGAL.sub("_", (name or "").strip())
    n = n.strip(" .")                       # Windows 不允许结尾点/空格
    if not n:
        n = fallback
    # 限制长度，避免超长路径
    return n[:60]


@dataclass
class ScriptMeta:
    """脚本库条目元数据"""
    name: str                       # 显示名
    filename: str                   # scripts/ 下的文件名（含 .json）
    created_at: str = ""
    modified_at: str = ""
    last_used: str = ""
    event_count: int = 0
    duration_ms: int = 0
    use_count: int = 0
    # 文件 mtime（精确到浮点）：用来发现「文件被改过」，字符串只有秒级精度会漏
    mtime: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptMeta":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in allowed})

    @property
    def display(self) -> str:
        """UI 列表显示文本"""
        return f"{self.name}"

    @property
    def subtitle(self) -> str:
        return f"{self.event_count} 步 · {self.duration_ms/1000:.1f}s"


class ScriptLibrary:
    """脚本库管理器"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else app_dir()
        if root:
            self.scripts_dir = self.root / "scripts"
        else:
            self.scripts_dir = scripts_dir()
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.scripts_dir / "index.json"
        self._index: dict[str, ScriptMeta] = {}
        # 老版本把脚本放在 ~/.macro-sprite/scripts：搬家一次，别让用户以为脚本丢了
        self.migrated = 0 if root else self._migrate_from(app_dir() / "scripts")
        self._load_index()

    def _migrate_from(self, old: Path) -> int:
        """把旧位置的脚本搬进「安装目录/脚本」（新目录已有脚本则不动）"""
        try:
            if not old.is_dir() or old.resolve() == self.scripts_dir.resolve():
                return 0
            olds = [p for p in old.glob("*.json") if p.name != "index.json"]
            if not olds:
                return 0
            if any(p.name != "index.json"
                   for p in self.scripts_dir.glob("*.json")):
                return 0                    # 新目录已经有脚本，不覆盖
            for p in olds:
                shutil.copy2(p, self.scripts_dir / p.name)
            # 索引一起搬过来，保住「最近使用」排序与使用次数
            old_index = old / "index.json"
            if old_index.exists() and not self.index_path.exists():
                shutil.copy2(old_index, self.index_path)
            return len(olds)
        except Exception:
            return 0

    # ------------------------------------------------------------ 索引
    def _load_index(self) -> None:
        """读取索引；失败则从目录重建"""
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            self._index = {
                k: ScriptMeta.from_dict(v) for k, v in raw.get("scripts", {}).items()
            }
        except Exception:
            self._index = {}
        self.refresh()                  # 与磁盘对齐（补新增、删已删）

    def _save_index(self) -> None:
        data = {"version": 1, "scripts": {k: v.to_dict()
                                          for k, v in self._index.items()}}
        _atomic_write_json(self.index_path, data)

    def _disk_stamp(self) -> dict[str, float]:
        """目录里每个 .json 的修改时间（不含索引本身）"""
        out: dict[str, float] = {}
        for p in self.scripts_dir.glob("*.json"):
            if p.name == "index.json":
                continue
            try:
                out[p.name] = p.stat().st_mtime
            except Exception:
                pass
        return out

    def signature(self) -> tuple[int, int]:
        """目录快照（文件数 + 最新修改时间），用来判断列表要不要刷新"""
        st = self._disk_stamp()
        return len(st), int(max(st.values(), default=0))

    def refresh(self) -> bool:
        """扫描目录，补齐 / 清理 / 更新索引；返回索引是否有变化

        文件夹就是接口：用户在外面往「脚本」里丢 .json、或者直接改文件，
        这里按 mtime 就能发现，不用重启程序。
        """
        stamps = self._disk_stamp()
        changed = False

        # 移除索引里已不存在的文件
        for fname in list(self._index.keys()):
            if fname not in stamps:
                del self._index[fname]
                changed = True

        # 新增的、以及文件被改过的，重新解析
        for fname, mtime in stamps.items():
            meta = self._index.get(fname)
            if meta is not None and abs(meta.mtime - mtime) < 1e-6:
                continue
            new = self._meta_from_file(fname)
            if not new:
                continue                    # 损坏文件跳过，不阻塞
            if meta is not None:            # 保住使用统计
                new.last_used = meta.last_used
                new.use_count = meta.use_count
                new.created_at = new.created_at or meta.created_at
            self._index[fname] = new
            changed = True

        if changed:
            self._save_index()
        return changed

    def _meta_from_file(self, fname: str) -> ScriptMeta | None:
        path = self.scripts_dir / fname
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = data.get("events", [])
            dur = max((int(e.get("t", 0)) for e in events), default=0)
            st = path.stat()
            mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(st.st_mtime))
            return ScriptMeta(
                name=data.get("name") or path.stem,
                filename=fname,
                created_at=data.get("createdAt") or mtime,
                modified_at=mtime,
                event_count=len(events),
                duration_ms=dur,
                mtime=st.st_mtime,
            )
        except Exception:
            return None                 # 损坏文件跳过，不阻塞

    # ------------------------------------------------------------ 查询
    def list(self, keyword: str = "", sort: str = "recent") -> list[ScriptMeta]:
        """列出脚本

        :param keyword: 按名称模糊搜索
        :param sort: recent(最近使用) / name / created
        """
        items = list(self._index.values())
        if keyword:
            kw = keyword.lower()
            items = [m for m in items if kw in m.name.lower()]
        if sort == "name":
            items.sort(key=lambda m: m.name)
        elif sort == "created":
            items.sort(key=lambda m: m.created_at, reverse=True)
        else:
            items.sort(key=lambda m: (m.last_used or m.modified_at or ""),
                       reverse=True)
        return items

    def get(self, filename: str) -> ScriptMeta | None:
        return self._index.get(filename)

    def load(self, filename: str) -> Script | None:
        """加载脚本，并更新使用统计"""
        path = self.scripts_dir / filename
        if not path.exists():
            return None
        try:
            script = Script.load(str(path))
        except Exception:
            return None
        meta = self._index.get(filename)
        if meta:
            meta.last_used = _now()
            meta.use_count += 1
            self._save_index()
        return script

    def exists(self, name: str) -> bool:
        return any(m.name == name for m in self._index.values())

    # ------------------------------------------------------------ 写入
    def _unique_filename(self, base: str) -> str:
        """生成不冲突的文件名"""
        fname = f"{base}.json"
        if fname not in self._index and not (self.scripts_dir / fname).exists():
            return fname
        i = 2
        while True:
            fname = f"{base} ({i}).json"
            if fname not in self._index and not (self.scripts_dir / fname).exists():
                return fname
            i += 1

    def save_new(self, script: Script, name: str | None = None) -> ScriptMeta:
        """另存为新脚本（重名自动加序号）"""
        script.name = (name or script.name or "未命名脚本").strip()
        base = safe_filename(script.name)
        fname = self._unique_filename(base)
        path = self.scripts_dir / fname
        script.save(str(path))

        now = _now()
        meta = ScriptMeta(
            name=script.name, filename=fname,
            created_at=script.created_at or now,
            modified_at=now, last_used=now,
            event_count=len(script.events), duration_ms=script.duration_ms,
            mtime=_mtime_of(path),
        )
        self._index[fname] = meta
        self._save_index()
        return meta

    def save(self, script: Script, filename: str | None = None) -> ScriptMeta:
        """保存（filename 已存在则覆盖，否则新建）"""
        if not filename or filename not in self._index:
            return self.save_new(script)
        script.name = script.name or self._index[filename].name
        path = self.scripts_dir / filename
        script.save(str(path))
        meta = self._index[filename]
        meta.name = script.name
        meta.modified_at = _now()
        meta.event_count = len(script.events)
        meta.duration_ms = script.duration_ms
        meta.mtime = _mtime_of(path)
        self._save_index()
        return meta

    def rename(self, filename: str, new_name: str) -> ScriptMeta | None:
        """重命名（只改显示名与文件名，内容不动）"""
        meta = self._index.get(filename)
        if not meta:
            return None
        new_name = new_name.strip()
        if not new_name:
            return None
        new_fname = self._unique_filename(safe_filename(new_name))
        old_path = self.scripts_dir / filename
        new_path = self.scripts_dir / new_fname
        try:
            old_path.rename(new_path)
        except Exception:
            return None
        self._index.pop(filename, None)
        meta.name = new_name
        meta.filename = new_fname
        meta.modified_at = _now()
        self._index[new_fname] = meta
        # 同步文件内的 name 字段
        try:
            script = Script.load(str(new_path))
            script.name = new_name
            script.save(str(new_path))
        except Exception:
            pass
        meta.mtime = _mtime_of(new_path)
        self._save_index()
        return meta

    def duplicate(self, filename: str) -> ScriptMeta | None:
        """复制一份"""
        src = self.scripts_dir / filename
        meta = self._index.get(filename)
        if not src.exists() or not meta:
            return None
        new_name = f"{meta.name} 副本"
        try:
            script = Script.load(str(src))
        except Exception:
            return None
        script.name = new_name
        return self.save_new(script, new_name)

    def delete(self, filename: str) -> bool:
        """删除（文件移到回收子目录，避免误删无法恢复）"""
        path = self.scripts_dir / filename
        if not path.exists():
            self._index.pop(filename, None)
            self._save_index()
            return False
        trash = self.root / "trash"
        trash.mkdir(exist_ok=True)
        try:
            shutil.move(str(path), str(trash / f"{int(time.time())}_{filename}"))
        except Exception:
            try:
                path.unlink()
            except Exception:
                return False
        self._index.pop(filename, None)
        self._save_index()
        return True

    def import_file(self, src_path: str) -> ScriptMeta | None:
        """从外部 .ajjl / .json 导入到库（.ajjl 的附件会一并释放到本地）"""
        try:
            from . import script_package       # 延迟导入，避免循环依赖
            script = script_package.load(src_path)
        except Exception:
            return None
        if not script.name or script.name == "未命名脚本":
            script.name = Path(src_path).stem
        return self.save_new(script)

    def export_file(self, filename: str, dest_path: str) -> bool:
        """导出到指定位置"""
        src = self.scripts_dir / filename
        if not src.exists():
            return False
        try:
            shutil.copy2(src, dest_path)
            return True
        except Exception:
            return False


# ================================================================ 工具
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _mtime_of(path: Path) -> float:
    """文件的 mtime；读不到返回 0（下次 refresh 会重新解析一遍）"""
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写入：先写 .tmp 再替换，防中途崩溃损坏"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ================================================================ 应用配置
CONFIG_FILE = "config.json"
CONFIG_DEFAULTS: dict[str, Any] = {
    "countdown": 3,             # 回放前倒计时秒数（0 = 关闭）
    "record_mode": "ask",       # ask | replace | append
    "last_dir": "",             # 上次保存/打开脚本的目录
}


def load_config() -> dict[str, Any]:
    """读取 ~/.macro-sprite/config.json；缺失或损坏时返回默认值"""
    data = dict(CONFIG_DEFAULTS)
    path = app_dir() / CONFIG_FILE
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in CONFIG_DEFAULTS})
    except Exception:
        pass
    return data


def save_config(patch: dict[str, Any]) -> None:
    """合并保存配置项（只认 CONFIG_DEFAULTS 里的键）"""
    data = load_config()
    data.update({k: v for k, v in patch.items() if k in CONFIG_DEFAULTS})
    try:
        _atomic_write_json(app_dir() / CONFIG_FILE, data)
    except Exception:
        pass
