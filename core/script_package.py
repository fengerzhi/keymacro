"""单文件脚本包（.ajjl）

把脚本和它引用的图片、数据表打进一个 zip，发到别的机器上不会出现
「找图失败」「数据文件打不开」。包内部结构：

    script.json      脚本本体，路径被改写成 @attach/<文件名>
    package.json     格式版本、创建时间、附件清单（供人工查看）
    attach/<文件名>  被引用的图片 / 数据表

解包时把附件释放到 ~/.macro-sprite/packages/<包名>-<内容摘要>/attach，
再把 @attach/ 引用还原成该目录下的绝对路径。用内容摘要做目录名，
同一个包重复打开不会互相覆盖，包内容变了自然换新目录。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

from .script_model import Script

ATTACH_PREFIX = "@attach/"
PKG_SCRIPT = "script.json"
PKG_META = "package.json"
PKG_ATTACH = "attach"

SUFFIX = ".ajjl"


# ---------------------------------------------------------------- 路径工具
def base_name(p: str | None) -> str:
    """取文件名，兼容 Windows 反斜杠（Linux 上 Path.name 切不开 'C:\\a\\b.png'）"""
    return (p or "").replace("\\", "/").rsplit("/", 1)[-1]


def default_root() -> Path:
    """附件释放目录"""
    d = Path.home() / ".macro-sprite" / "packages"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 引用扫描
def iter_refs(script: Script):
    """遍历脚本里的文件引用：产出 (事件下标, 字段名, 路径)"""
    for i, ev in enumerate(script.events):
        if ev.type in ("find_image", "if_image") and ev.text:
            yield i, "text", ev.text
        elif ev.type == "if" and (ev.cond or "") == "image" and ev.left:
            yield i, "left", ev.left


def collect(script: Script) -> tuple[dict[str, Path], list[str]]:
    """收集要带走的文件。

    返回 (包内名 → 本地路径, 找不到的文件列表)。
    同名文件来自不同目录时加事件序号前缀，避免互相覆盖。
    """
    items: dict[str, Path] = {}
    missing: list[str] = []
    for i, _field, p in iter_refs(script):
        fp = Path(p)
        if not fp.is_file():
            if p not in missing:
                missing.append(p)
            continue
        name = base_name(p) or f"img_{i}.png"
        if name in items:
            name = f"{i}_{name}"
        items[name] = fp
    if script.data_file:
        fp = Path(script.data_file)
        if fp.is_file():
            name = base_name(script.data_file) or "data.xlsx"
            if name in items:
                name = f"data_{name}"
            items[name] = fp
        elif script.data_file not in missing:
            missing.append(script.data_file)
    return items, missing


# ---------------------------------------------------------------- 打包
def pack(script: Script, out_path: str | Path) -> tuple[int, list[str]]:
    """把脚本写成单文件包；返回 (附件数量, 找不到的文件列表)"""
    out = Path(out_path)
    if out.suffix.lower() != SUFFIX:
        out = out.with_suffix(SUFFIX)
    out.parent.mkdir(parents=True, exist_ok=True)

    items, missing = collect(script)
    # 本地路径 → 包内名
    mapping = {str(fp): name for name, fp in items.items()}

    data = script.to_dict()
    for i, field, p in iter_refs(script):
        name = mapping.get(str(Path(p)))
        if name and i < len(data["events"]):
            data["events"][i][field] = ATTACH_PREFIX + name
    if script.data_file:
        name = mapping.get(str(Path(script.data_file)))
        if name:
            data["data_file"] = ATTACH_PREFIX + name

    meta = {
        "format": "ajjl-package",
        "format_version": 1,
        "name": script.name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": _app_version(),
        "event_count": len(script.events),
        "attachments": sorted(items),
        "missing": missing,
    }

    tmp = out.with_suffix(out.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PKG_SCRIPT,
                    json.dumps(data, ensure_ascii=False, indent=2))
        zf.writestr(PKG_META,
                    json.dumps(meta, ensure_ascii=False, indent=2))
        for name, fp in items.items():
            zf.write(fp, f"{PKG_ATTACH}/{name}")
    tmp.replace(out)
    return len(items), missing


def _app_version() -> str:
    try:
        from .updater import APP_VERSION
        return APP_VERSION
    except Exception:                              # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 解包
def is_package(path: str | Path) -> bool:
    """是不是本程序的脚本包（而不是普通 json 脚本）"""
    p = Path(path)
    if p.suffix.lower() == SUFFIX:
        return True
    if not zipfile.is_zipfile(p):
        return False
    try:
        with zipfile.ZipFile(p) as zf:
            return PKG_SCRIPT in zf.namelist()
    except Exception:                              # noqa: BLE001
        return False


def unpack(pkg_path: str | Path, work_root: str | Path | None = None) -> Script:
    """解包并返回脚本（附件已释放到磁盘，路径可直接使用）"""
    pkg = Path(pkg_path)
    if not zipfile.is_zipfile(pkg):
        raise ValueError(f"不是有效的脚本包：{pkg.name}")
    digest = hashlib.sha1(pkg.read_bytes()).hexdigest()[:10]
    root = Path(work_root) if work_root else default_root()
    work = root / f"{pkg.stem}-{digest}"
    attach_dir = work / PKG_ATTACH
    attach_dir.mkdir(parents=True, exist_ok=True)
    attach_root = attach_dir.resolve()

    with zipfile.ZipFile(pkg) as zf:
        names = zf.namelist()
        if PKG_SCRIPT not in names:
            raise ValueError("包里没有 script.json，可能不是按键精灵的脚本包")
        for info in zf.infolist():
            name = info.filename
            if not name.startswith(PKG_ATTACH + "/") or name.endswith("/"):
                continue
            rel = name[len(PKG_ATTACH) + 1:]
            target = (attach_dir / rel).resolve()
            if not str(target).startswith(str(attach_root)):
                raise ValueError(f"包内含非法路径：{name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        raw = zf.read(PKG_SCRIPT).decode("utf-8-sig", "replace")

    data = json.loads(raw)

    def fix(p):
        if isinstance(p, str) and p.startswith(ATTACH_PREFIX):
            rel = p[len(ATTACH_PREFIX):]
            return str(attach_dir / rel)
        return p

    for e in data.get("events", []):
        for k in ("text", "left"):
            if k in e:
                e[k] = fix(e[k])
    if "data_file" in data:
        data["data_file"] = fix(data["data_file"]) or ""

    return Script.from_dict(data)


# ---------------------------------------------------------------- 统一入口
def save(script: Script, path: str | Path) -> tuple[str, int, list[str]]:
    """按扩展名分派：.ajjl 打包，.json 存纯文本。返回 (实际路径, 附件数, 缺失)"""
    p = Path(path)
    if p.suffix.lower() == SUFFIX:
        n, missing = pack(script, p)
        return str(p if p.suffix.lower() == SUFFIX else p.with_suffix(SUFFIX)), n, missing
    script.save(str(p))
    return str(p), 0, []


def load(path: str | Path) -> Script:
    """按内容判断：脚本包 → 解包，普通 json → 直接读"""
    if is_package(path):
        return unpack(path)
    return Script.load(str(path))
