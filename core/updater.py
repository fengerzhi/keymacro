"""updater.py —— 自动升级

工作方式
─────────────────────────────────────────────────────────
1. 升级服务器就是一个静态 URL，上面放一个 version.json：
       {
         "version": "1.4.0",
         "notes": "修复 xxx；新增 yyy",
         "package": "https://.../按键精灵-1.4.0.zip"
       }
   package 指向的 zip 里是**新版程序的文件**（main.py / core/ / ui/ …，
   打包版则是 exe 同级的文件结构）。
2. 点「检查升级」→ 拉取 version.json → 与本地版本比较。
3. 用户确认后下载 zip → 校验 → 解压到临时目录 → 生成升级脚本 →
   提示用户点「立即升级」→ 程序退出，升级脚本等 2 秒后覆盖文件并重启。
   （Windows 下 exe 正在运行无法覆盖自己，必须借助重启脚本）

本地升级：没有网络时也可以选一个本地 zip 直接升级。

自定义升级地址：在应用数据目录放一个 update_url.txt，内容为 URL，
即可覆盖内置默认地址。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.8.4"

# 当前版本的更新说明：生成升级包时写入 notes.txt，升级窗口会自动显示
RELEASE_NOTES = """v1.8.4
- 修复升级时下载升级包失败的问题：以前一次把整包读进内存，网络被掐断一次就整体失败（WinError 10054 远程主机强迫关闭了一个现有的连接），重试也从 0 开始；现在改成流式写入 + 断点续传，断线后从断点接着下，最多重试 8 次
- 下载完成会校验长度，不会再把半包当成完整包去解压"""



# 内置升级地址（可被 update_url.txt 覆盖）。
# 支持两种形式：
#   1) 静态 version.json 的 http(s) 直链
#   2) GitHub 仓库（https://github.com/用户名/仓库名），
#      自动取该仓库最新 Release 的 tag / 说明 / zip 附件
# 内置默认地址：装好就能点「检查更新」，不必自己填仓库或 token。
# 仓库为公开仓库，升级时匿名读取 Release 的 tag / 说明 / zip 附件。
DEFAULT_UPDATE_URL = "https://github.com/fengerzhi/keymacro"

UPDATE_TIMEOUT = 15          # 网络超时（秒）
DOWNLOAD_TIMEOUT = 300       # 下载超时（秒）

# 本机升级仓库的默认所有者（用户在「升级」窗口里填过一次 owner/repo 后会被记住）
_OWNER_CACHE: list[str] = []


# ================================================================ 升级日志
# 升级是「程序退出后由另一个进程接力」的动作：出问题时主界面已经没了，
# 用户能看到的只有「和之前一样」。所以每一步都必须落盘。
_LAST_LOG: list[str] = []


def log_dir() -> Path:
    """升级日志目录（与崩溃日志放在一起）"""
    try:
        from .library import app_dir
        d = app_dir() / "logs"
    except Exception:
        d = Path.home() / ".macro-sprite" / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def upgrade_log(msg: str, path: str | Path | None = None) -> str:
    """追加一条升级记录，返回日志文件路径。

    同一个进程内第一次调用会定下日志文件名，后续调用都写同一份，
    这样「准备 → 启动脚本 → 结果」能连起来看。
    """
    if _LAST_LOG:
        p = Path(_LAST_LOG[0])
    elif path:
        p = Path(path)
    else:
        p = log_dir() / f"upgrade_{time.strftime('%Y%m%d_%H%M%S')}.log"
    if not _LAST_LOG:
        _LAST_LOG.append(str(p))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    return str(p)


def last_log() -> str:
    """本次（或最近一次）升级日志路径；还没写过就返回空串"""
    return _LAST_LOG[0] if _LAST_LOG else ""


def recent_log() -> str:
    """最近一份升级日志（跨进程重启也能找到）"""
    if _LAST_LOG:
        return _LAST_LOG[0]
    try:
        logs = sorted(log_dir().glob("upgrade_*.log"))
        return str(logs[-1]) if logs else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- 升级待办标记
# 升级前写一份「我要升到 x.y.z」，下次启动读到它就知道上一轮到底成没成。
PENDING_NAME = "pending_update.json"


def pending_path() -> Path:
    try:
        from .library import app_dir
        return app_dir() / PENDING_NAME
    except Exception:
        return Path.home() / ".macro-sprite" / PENDING_NAME


def write_pending(version: str, package: str = "", source: str = "") -> str:
    p = pending_path()
    data = {
        "version": (version or "").strip(),
        "package": str(package or ""),
        "source": str(source or ""),
        "from_version": APP_VERSION,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "log": last_log(),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return str(p)
    except Exception:
        return ""


def read_pending() -> dict | None:
    p = pending_path()
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def clear_pending() -> None:
    try:
        pending_path().unlink(missing_ok=True)
    except Exception:
        pass


def read_last_result() -> dict:
    """读升级脚本留在安装目录里的结果文件（result=OK/FAIL 等）"""
    p = app_install_dir() / "_upgrade_last.txt"
    out: dict = {}
    try:
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8-sig",
                                errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 包 / 安装形态
def _basenames(names) -> list[str]:
    return [str(n).replace("\\", "/").rsplit("/", 1)[-1].lower() for n in names]


def package_shape(zip_path: str | Path) -> str:
    """升级包形态：'exe'（打包版，含 exe/_internal） / 'source'（源码） / ''"""
    try:
        with zipfile.ZipFile(Path(zip_path)) as zf:
            names = zf.namelist()
    except Exception:
        return ""
    low = [n.replace("\\", "/").lower() for n in names]
    if any(n.endswith(".exe") for n in low) or any(
            n.startswith("_internal/") or "/_internal/" in n for n in low):
        return "exe"
    if any(b == "main.py" for b in _basenames(names)):
        return "source"
    return ""


def install_shape() -> str:
    """当前安装形态：'exe' / 'source' / ''"""
    d = app_install_dir()
    try:
        if any(p.suffix.lower() == ".exe" for p in d.iterdir() if p.is_file()):
            return "exe"
        if (d / "main.py").exists():
            return "source"
    except Exception:
        pass
    return ""


def shape_hint(zip_path: str | Path) -> str:
    """包与安装形态不匹配时返回一句人话提醒，匹配则返回空串。"""
    pk, ins = package_shape(zip_path), install_shape()
    if pk == "exe" and ins == "source":
        return ("这个升级包是打包版（含 .exe），而当前装的是源码版。\n"
                "覆盖后启动的仍是源码，版本不会变——请改用源码包升级。")
    if pk == "source" and ins == "exe":
        return ("这个升级包是源码版（.py），而当前装的是打包版（.exe）。\n"
                "覆盖后 .exe 还是旧的，版本不会变——请改用打包版升级包。")
    return ""


def github_headers(auth: bool = False) -> dict:
    """GitHub 请求头；auth=True 时带上 token（私有仓库 / 打 Release 用）"""
    h = {"User-Agent": f"KeyMacro/{APP_VERSION}",
         "Accept": "application/vnd.github+json"}
    if auth:
        tk = github_token()
        if tk:
            h["Authorization"] = f"Bearer {tk}"
            h["X-GitHub-Api-Version"] = "2022-11-28"
    return h


def github_token() -> str:
    """GitHub Token：数据目录 github_token.txt 优先，其次环境变量 GITHUB_TOKEN"""
    try:
        from .library import app_dir
        f = app_dir() / "github_token.txt"
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    return (os.environ.get("GITHUB_TOKEN") or "").strip()


def set_github_token(token: str) -> None:
    try:
        from .library import app_dir
        (app_dir() / "github_token.txt").write_text(
            (token or "").strip(), encoding="utf-8")
    except Exception:
        pass


def github_repo() -> str:
    """用户/程序填写的升级仓库 'owner/repo'（空 = 未配置）"""
    if _OWNER_CACHE:
        return _OWNER_CACHE[0]
    try:
        from .library import app_dir
        f = app_dir() / "update_repo.txt"
        if f.exists():
            r = f.read_text(encoding="utf-8").strip()
            if r:
                _OWNER_CACHE.append(r)
                return r
    except Exception:
        pass
    return ""


def set_github_repo(repo: str) -> None:
    repo = (repo or "").strip().strip("/")
    if repo.lower().startswith(("http://", "https://")):
        repo = _github_repo_from_url(repo) or repo
    _OWNER_CACHE[:] = [repo] if repo else []
    try:
        from .library import app_dir
        (app_dir() / "update_repo.txt").write_text(repo, encoding="utf-8")
    except Exception:
        pass


def update_url() -> str:
    """升级地址：update_url.txt 优先，其次已保存的 GitHub 仓库，最后内置默认"""
    try:
        from .library import app_dir
        cfg = app_dir() / "update_url.txt"
        if cfg.exists():
            u = cfg.read_text(encoding="utf-8").strip()
            if u:
                return u
    except Exception:
        pass
    if github_repo():
        return f"https://github.com/{github_repo()}"
    return DEFAULT_UPDATE_URL


# ================================================================ 版本比较
def parse_version(v: str) -> tuple[int, ...]:
    """'1.3.0' → (1, 3, 0)；忽略 v 前缀与非数字尾巴"""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for seg in v.split("."):
        digits = ""
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits or 0))
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


# ================================================================ 地址
def app_install_dir() -> Path:
    """程序安装目录（打包版 = exe 所在目录；源码版 = 项目根）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def set_update_url(url: str) -> None:
    try:
        from .library import app_dir
        cfg = app_dir() / "update_url.txt"
        cfg.write_text((url or "").strip(), encoding="utf-8")
    except Exception:
        pass


# ================================================================ 网络
def _http_get(url: str, timeout: int, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": f"KeyMacro/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _github_repo_from_url(url: str) -> str | None:
    """从 GitHub 链接里解析 'owner/repo'；不是 GitHub 仓库链接则返回 None。

    支持：github.com/owner/repo 、github.com/owner/repo/releases 、
          api.github.com/repos/owner/repo/releases/latest 等。
    """
    import re
    u = url.strip()
    if "api.github.com" in u.lower():
        m = re.search(r"api\.github\.com/repos/([\w.-]+)/([\w.-]+)", u, re.I)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
        return None
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$",
                  u, re.I)
    if m and m.group(1).lower() != "github":
        return f"{m.group(1)}/{m.group(2)}"
    return None


def fetch_manifest_github(repo: str) -> dict:
    """通过 GitHub Releases API 获取最新版本信息。

    repo 形如 'owner/repo'。返回与 version.json 同构的 dict：
      version = 最新 Release 的 tag（去掉 v 前缀）
      notes   = Release 正文
      package = Release 里第一个 .zip 附件（没有则用 zipball）
    仓库还没发布过 Release 时抛 LookupError，调用方据此区分「无 Release」
    与「已是最新」。
    """
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        raw = _http_get(api, UPDATE_TIMEOUT, github_headers(auth=True))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LookupError("该仓库还没有发布 Release（或仓库不存在/私有未授权）")
        raise
    data = json.loads(raw.decode("utf-8"))
    tag = (data.get("tag_name") or data.get("name") or "").strip()
    if not tag:
        raise LookupError("该仓库没有可用的 Release")
    assets = [a for a in (data.get("assets") or [])
              if str(a.get("name", "")).lower().endswith(".zip")]
    package = assets[0]["browser_download_url"] if assets \
        else data.get("zipball_url", "")
    if not package:
        raise ValueError("Release 里没有可下载的 zip 升级包")
    return {
        "version": tag.lstrip("vV"),
        "notes": (data.get("body") or "").strip(),
        "package": package,
        "source": f"github:{repo}",
    }


def fetch_manifest(url: str | None = None) -> dict:
    """拉取远端版本信息。字段：version / notes / package"""
    u = (url or update_url()).strip()
    if not u.lower().startswith(("http://", "https://")):
        raise ValueError(f"升级地址不是有效的 http(s) 链接：{u}")
    # GitHub 仓库链接 → 走 Releases API
    repo = _github_repo_from_url(u)
    if repo and not u.lower().endswith((".json", ".txt")):
        return fetch_manifest_github(repo)
    raw = _http_get(u, UPDATE_TIMEOUT)
    data = json.loads(raw.decode("utf-8"))
    if not data.get("version") or not data.get("package"):
        raise ValueError("version.json 缺少 version 或 package 字段")
    return data


def download_package(url: str, dest_dir: str | None = None,
                     progress=None) -> Path:
    """下载升级包到临时目录，返回 zip 路径

    为什么要自己写断点续传
    ─────────────────────────────────────────
    升级包 25 MB 上下，国内直连 GitHub 的 objects.githubusercontent.com
    经常被中途掐断（"WinError 10054 远程主机强迫关闭了一个现有的连接"）。
    原来是一次 read() 读完整包再落盘，断一次就整体失败，重试还从 0 开始。

    现在改成流式写入 + Range 续传：连接断了就从 .part 的断点接着下，
    最多 8 轮（间隔 1/2/4/8 秒退避）。只要网络不是彻底不通，就能下完。
    """
    dest = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / "keymacro_update.zip"
    # .part 名字带 URL 指纹：换了版本/换了源，不会把旧残片接在新包前面
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    part = dest / f"keymacro_update.{key}.part"
    if final.exists():
        final.unlink()

    last: Exception | None = None
    for attempt in range(1, 9):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": f"KeyMacro/{APP_VERSION}"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                resumed = have > 0 and resp.status == 206
                length = int(resp.headers.get("Content-Length") or 0)
                total = length + have if resumed else length
                if not resumed:
                    have = 0                       # 服务器不认 Range，只能从头写
                with open(part, "ab" if resumed else "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if progress:
                            try:
                                progress(have, total)
                            except Exception:      # noqa: BLE001
                                pass
            size = part.stat().st_size
            if total and size < total:
                raise IOError(f"下载不完整：{size}/{total} 字节")
            shutil.move(str(part), str(final))
            return final
        except Exception as exc:                    # noqa: BLE001
            last = exc
            if attempt < 8:
                time.sleep(min(2 ** (attempt - 1), 8))

    raise RuntimeError(
        f"下载升级包失败（已重试 8 次）：{last}\n\n"
        "到 GitHub 的连接不太稳定。可以改用「升级文件夹」（把升级包放到"
        "本地文件夹再指定路径），或者挂上代理后重试。"
    ) from last


# ================================================================ 应用升级
@dataclass
class UpdatePlan:
    staging: Path          # 解压出来的新文件目录
    install: Path          # 当前安装目录
    restart_bat: Path      # 生成的升级脚本（Windows，兜底用）
    launch: str = ""       # 升级完成后要启动的目标（完整路径）
    proc: str = ""         # 升级前要结束的进程名
    ps_command: str = ""   # PowerShell -EncodedCommand（Base64/UTF-16，主路径）
    pid: int = 0           # 当前进程号（升级前精确结束自己）
    mark: str = ""         # 升级脚本「已开始执行」的标记文件（确认脚本真的跑起来了）
    log: str = ""          # 升级日志路径
    target: str = ""       # 目标版本号
    hint: str = ""         # 形态不匹配等提醒（空 = 没问题）


def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串转义"""
    return "'" + str(s).replace("'", "''") + "'"


def build_ps_command(staging: Path, install: Path, launch: str, proc: str,
                     pid: int = 0, log_file: str = "",
                     mark_file: str = "") -> str:
    """生成升级用 PowerShell 脚本并转成 -EncodedCommand 需要的 Base64。

    为什么不继续用 .bat：cmd 的代码页（chcp）与文件编码只要不一致，
    含中文的路径就会被解成乱码，robocopy 于是创建一个乱码名字的新目录。
    -EncodedCommand 是 UTF-16LE 的 Base64，由 PowerShell 自己解码，
    完全不经过 cmd 的代码页，中文/空格/括号/& 等字符都原样保留。

    相比旧版，这里多做三件事（都是为了「升级失败能被看见」）：
      1) Start-Transcript 把全过程写进日志文件；
      2) 用 pid 精确结束本程序，再等它真正退出，之后才覆盖；
      3) 覆盖完用 SHA256 逐个核对关键文件，没换掉就判失败并说明是哪些文件。
    """
    pname = (proc or "").strip()
    if pname.lower().endswith(".exe"):
        pname = pname[:-4]
    # PowerShell 的转录文件固定是 UTF-16，和 Python 写的 UTF-8 日志混在一起
    # 会乱码，所以单独用一个 .ps.log。
    ps_log = ""
    if log_file:
        ps_log = (log_file[:-4] + ".ps.log" if log_file.lower().endswith(".log")
                  else log_file + ".ps.log")
    # 升级后要核对的文件：源码版看 .py，打包版看 exe
    verify = ["core/updater.py", "main.py"]
    if proc:
        verify.append(proc if proc.lower().endswith(".exe") else proc + ".exe")

    lines = [
        "$ErrorActionPreference = 'Continue'",
        # 第一件事：写标记文件。Python 侧据此确认脚本**真的开始执行**了。
        # Popen 成功 ≠ 脚本跑起来：DETACHED_PROCESS 下 PowerShell 会立刻退出，
        # 一行都不执行（实测），升级于是「看起来交接成功、实际什么都没换」。
        f"$mark = {_ps_quote(mark_file or '')}",
        "if ($mark) { try { Set-Content -LiteralPath $mark -Value $PID"
        " -Encoding ASCII } catch { } }",
        f"$log = {_ps_quote(log_file or '')}",
        f"$pslog = {_ps_quote(ps_log)}",
        "if ($pslog) {",
        "  try {",
        "    $ld = Split-Path -Parent $pslog",
        "    if ($ld -and -not (Test-Path -LiteralPath $ld)) {"
        " New-Item -ItemType Directory -Force -Path $ld | Out-Null }",
        "    Start-Transcript -Path $pslog -Append -Force | Out-Null",
        "  } catch { }",
        "}",
        f"$targetPid = {int(pid or 0)}",
        "Start-Sleep -Seconds 2",
        "if ($targetPid -gt 0) {",
        "  try {",
        "    Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue",
        "    Wait-Process -Id $targetPid -Timeout 8 -ErrorAction SilentlyContinue",
        "  } catch { }",
        "}",
        f"$pname = {_ps_quote(pname)}",
        "$left = -1",
        "if ($pname) {",
        "  try { Get-Process -Name $pname -ErrorAction SilentlyContinue |"
        " Stop-Process -Force -ErrorAction SilentlyContinue } catch { }",
        # 必须等进程真的消失再覆盖：exe 还被占着时 robocopy 一定失败，
        # 表现就是「升级包对了、日志说交接成功，但 exe 没换掉」
        "  for ($w = 0; $w -lt 40; $w++) {",
        "    $left = @(Get-Process -Name $pname"
        " -ErrorAction SilentlyContinue).Count",
        "    if ($left -le 0) { break }",
        "    if ($w -eq 12) {",
        "      Get-Process -Name $pname -ErrorAction SilentlyContinue |"
        " Stop-Process -Force -ErrorAction SilentlyContinue",
        "    }",
        "    Start-Sleep -Milliseconds 500",
        "  }",
        "}",
        "if ($left -lt 0) { $left = 0 }",
        f"$src = {_ps_quote(str(staging))}",
        f"$dst = {_ps_quote(str(install))}",
        "if (-not (Test-Path -LiteralPath $dst)) {"
        " New-Item -ItemType Directory -Force -Path $dst | Out-Null }",
        "$ok = $false",
        "$err = ''",
        # ---- 增量覆盖：只复制内容真的变了的文件 ----
        # 以前 robocopy /E /IS /IT 里的 /IS 会把「同名同内容」的文件也重写一遍，
        # 每次升级都把整个目录刷一遍：又慢，exe/dll 被占用时也更容易失败。
        # 现在先比大小和修改时间；时间对不上再算 SHA256（升级包解压出来的
        # 时间戳几乎全都跟已装的文件不同，所以哈希这条路的命中率很高，
        # 用 .NET 的 SHA256 直算，比 Get-FileHash cmdlet 快一个数量级）。
        "$sha = [System.Security.Cryptography.SHA256]::Create()",
        "function Get-Sha([string]$p) {",
        "  $fs = $null",
        "  try {",
        "    $fs = [System.IO.File]::OpenRead($p)",
        "    [BitConverter]::ToString($sha.ComputeHash($fs))",
        "  } catch { '' } finally { if ($fs) { $fs.Dispose() } }",
        "}",
        "$changed = 0",
        "$same = 0",
        "$failed = @()",
        "$srcLen = $src.TrimEnd('\\').Length",
        "foreach ($f in @(Get-ChildItem -LiteralPath $src -Recurse -File -Force"
        " -ErrorAction SilentlyContinue)) {",
        "  $rel = $f.FullName.Substring($srcLen).TrimStart('\\')",
        "  $tgt = Join-Path $dst $rel",
        "  $need = $true",
        "  if (Test-Path -LiteralPath $tgt -PathType Leaf) {",
        "    $ti = Get-Item -LiteralPath $tgt -ErrorAction SilentlyContinue",
        "    if ($ti -and $ti.Length -eq $f.Length) {",
        "      $dt = [math]::Abs(($ti.LastWriteTimeUtc -"
        " $f.LastWriteTimeUtc).TotalSeconds)",
        "      if ($dt -le 2) { $need = $false }",
        "      elseif ((Get-Sha $f.FullName) -eq (Get-Sha $tgt)) {"
        " $need = $false }",
        "    }",
        "  }",
        "  if (-not $need) { $same++; continue }",
        "  for ($t = 0; $t -lt 3; $t++) {",
        "    try {",
        "      $pd = Split-Path -Parent $tgt",
        "      if ($pd -and -not (Test-Path -LiteralPath $pd)) {"
        " New-Item -ItemType Directory -Force -Path $pd | Out-Null }",
        "      Copy-Item -LiteralPath $f.FullName -Destination $tgt -Force"
        " -ErrorAction Stop",
        "      $changed++",
        "      break",
        "    } catch {",
        "      if ($t -eq 2) {",
        "        $failed += $rel",
        "        $err = $_.Exception.Message",
        "      } else { Start-Sleep -Milliseconds 400 }",
        "    }",
        "  }",
        "}",
        "$ok = ($failed.Count -eq 0)",
        "if (-not $ok) {",
        "  $err = \"这些文件没能替换：\" + ($failed -join ', ') +"
        " \"（被占用或没有写入权限）\"",
        "}",
        # ---- 核对：关键文件必须真的换了 ----
        "$bad = @()",
        f"foreach ($rel in @({', '.join(_ps_quote(v) for v in verify)})) {{",
        "  $s = Join-Path $src $rel",
        "  $d = Join-Path $dst $rel",
        "  if ((Test-Path -LiteralPath $s) -and (Test-Path -LiteralPath $d)) {",
        "    try {",
        "      $hs = (Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash",
        "      $hd = (Get-FileHash -LiteralPath $d -Algorithm SHA256).Hash",
        "      if ($hs -ne $hd) { $bad += $rel }",
        "    } catch { $bad += $rel }",
        "  }",
        "}",
        "if ($ok -and $bad.Count -gt 0) {",
        "  $ok = $false",
        "  $err = \"这些文件没有被替换：\" + ($bad -join ', ') +"
        " \"（多半被占用或没有写入权限）\"",
        "}",
        f"$launch = {_ps_quote(launch or '')}",
        "$done = $false",
        "if ($ok -and $launch -and (Test-Path -LiteralPath $launch)) {",
        "  try { Start-Process -FilePath $launch -WorkingDirectory $dst;"
        " $done = $true }",
        "  catch { $err = $_.Exception.Message }",
        "}",
        # ---- 结果落盘：程序下次启动会读它 ----
        "$res = 'FAIL'",
        "if ($ok) { $res = 'OK' }",
        "try {",
        "  $txt = @(",
        "    \"result=$res\",",
        "    \"time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')\",",
        "    \"src=$src\",",
        "    \"dst=$dst\",",
        "    \"changed=$changed\",",
        "    \"skipped=$same\",",
        "    \"running=$left\",",
        "    \"restarted=$done\",",
        "    \"error=$err\",",
        "    \"log=$log\",",
        "    \"pslog=$pslog\")",
        "  Set-Content -LiteralPath (Join-Path $dst '_upgrade_last.txt')"
        " -Encoding UTF8 -Value $txt",
        "} catch { }",
        "$msg = ''",
        "if (-not $ok) {",
        "  $extra = ''",
        "  if ($left -gt 0) { $extra = \"`n`n注意：还有 $left 个「$pname」进程没退出，"
        "先把它们全部关掉，再点一次升级。\" }",
        "  $msg = \"升级文件没有替换成功。`n`n原因：$err$extra`n`n请手动把升级包里的文件"
        "复制到：`n$dst`n`n详细日志：`n$log\" + \"`n$pslog\"",
        "} elseif (-not $done) {",
        "  $msg = \"升级文件已复制完成，但没能自动启动。`n`n请手动打开：$dst\"",
        "}",
        "if ($msg) {",
        # 90 秒自动消失：0 = 一直等用户点，会留下僵尸 PowerShell 占着日志文件
        "  try { (New-Object -ComObject WScript.Shell).Popup($msg, 90,"
        " '按键精灵 · 升级', 48) | Out-Null } catch { }",
        "}",
        "if ($pslog) { try { Stop-Transcript | Out-Null } catch { } }",
    ]
    script = "\r\n".join(lines)
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _entry_name(info: zipfile.ZipInfo) -> str:
    """还原 zip 条目名。

    Python 自己打的包带 UTF-8 标志位，直接用；WinRAR / 好压 / 360 压缩
    打的包没这个标志，zipfile 会按 CP437 解码 → 中文变乱码文件名。
    这里把名字还原成原始字节，再按 GBK（中文 Windows 常见）重解一次。
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except Exception:
        return name
    for enc in ("gbk", "utf-8"):
        try:
            fixed = raw.decode(enc)
        except Exception:
            continue
        if fixed != name:
            return fixed
    return name


def extract_package(zip_path: str | Path, staging_dir: str | None = None) -> Path:
    """解压升级包，返回新文件所在目录。

    兼容两种 zip 结构：
      a) zip 根下直接是文件（main.py / core/ ...）
      b) zip 里有一层同名文件夹
    中文文件名会自动纠正编码，避免解出乱码名。
    """
    zip_path = Path(zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"不是有效的 zip 文件：{zip_path}")
    staging = Path(staging_dir) if staging_dir else Path(
        tempfile.mkdtemp(prefix="keymacro_update_"))
    staging.mkdir(parents=True, exist_ok=True)
    root = staging.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        plan: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in infos:
            name = _entry_name(info)
            target = (staging / name).resolve()
            # 防路径穿越：用 Path 的语义判断，不依赖 os.sep
            if target != root and not target.is_relative_to(root):
                raise ValueError(f"zip 内含非法路径：{name}")
            plan.append((info, staging / name))
        for info, target in plan:
            if info.is_dir() or info.filename.endswith(("/", "\\")):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    # 若只有一层目录，进入它
    entries = [e for e in staging.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return staging


def plan_update(zip_path: str | Path, target_version: str = "") -> UpdatePlan:
    """解压并生成升级计划（含 Windows 重启脚本）"""
    zip_path = Path(zip_path)
    staging = extract_package(zip_path)
    install = app_install_dir()
    log = upgrade_log(
        f"开始准备升级：包={zip_path}",
        log_dir() / f"upgrade_{time.strftime('%Y%m%d_%H%M%S')}.log")
    ver = (target_version or "").strip() or zip_version(zip_path)
    hint = shape_hint(zip_path)
    upgrade_log(f"包版本={ver or '(读不出)'} 当前版本={APP_VERSION}")
    upgrade_log(f"安装目录={install}")
    upgrade_log(f"包形态={package_shape(zip_path) or '未知'} "
                f"安装形态={install_shape() or '未知'}")
    if hint:
        upgrade_log(f"警告：形态提醒={hint}")

    bat = staging / "_apply_update.bat"
    launch = ""
    proc = ""
    if os.name == "nt":
        if getattr(sys, "frozen", False):
            proc = Path(sys.executable).name
            launch = str(install / proc)
        else:
            # 源码模式：目录里没有 exe，优先用自带的启动脚本重启
            starter = install / "启动按键精灵.bat"
            if not starter.exists():
                starter = staging / "启动按键精灵.bat"
            launch = str(starter) if starter.exists() else ""
        # 关键：脚本里**不写任何中文路径字面量**。
        # 旧版把 staging / install 直接写进 bat，而 bat 是 GBK 编码、
        # cmd 却按 chcp 65001(UTF-8) 解析 → 中文路径全被解成乱码，
        # robocopy 就把文件拷到一个乱码名字的新目录里（升级看起来"成功"但没生效）。
        # 现在路径全部由 %~dp0 / %~1 / %~2 / %~3 传入，文件内容与代码页无关。
        lines = [
            "@echo off",
            "setlocal",
            "chcp 936 >nul",
            'cd /d "%~dp0"',
            "title 按键精灵 · 正在升级",
            "echo 正在关闭旧版本并替换文件，请稍候…",
            "timeout /t 2 /nobreak >nul",
            'if not "%~3"=="" taskkill /f /im "%~3" >nul 2>nul',
            "timeout /t 1 /nobreak >nul",
            "set /a TRY=0",
            ":copy",
            "set /a TRY+=1",
            # 不加 /IS /IT：默认只复制「源较新或目标不存在」的文件，
            # 已经是同一份的文件不再重复写一遍（和 PowerShell 主流程同一个意思）
            'robocopy "." "%~1" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP',
            "if errorlevel 8 goto :retry",
            "goto :ok",
            ":retry",
            "if %TRY% LSS 5 (timeout /t 2 /nobreak >nul & goto :copy)",
            "echo 复制失败，文件可能被占用；请关闭杀毒软件后重试。",
            "echo 也可以手动把本文件夹里的文件复制到安装目录。",
            "echo 安装目录：%~1",
            "pause",
            "goto :eof",
            ":ok",
            'if exist "%~2" (',
            '  start "" "%~2"',
            ") else (",
            "  echo 升级文件已复制完成，请手动启动程序。",
            "  pause",
            ")",
            "endlocal",
            "exit",
        ]
        # 脚本用 GBK 写（配合 chcp 936），中文提示才不会乱码。
        # 注意：这只是兜底手段；Windows 上真正执行的是下面的 PowerShell
        # 编码命令，不依赖 bat 与代码页。
        try:
            bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("gbk"))
        except Exception:
            bat.write_text("\r\n".join(lines), encoding="utf-8")
    # 升级脚本写下的「我已经开始执行」标记（每次升级一个独立文件）
    mark = str(Path(tempfile.gettempdir()) /
               f"keymacro_apply_{os.getpid()}_{int(time.time())}.mark")
    ps_command = build_ps_command(staging, install, launch, proc,
                                  os.getpid(), log, mark) if os.name == "nt" else ""
    upgrade_log(f"启动目标={launch or '(无)'} "
                f"要结束的进程={proc or '(源码进程)'} pid={os.getpid()}")
    return UpdatePlan(staging=staging, install=install, restart_bat=bat,
                     launch=launch, proc=proc, ps_command=ps_command,
                     pid=os.getpid(), mark=mark, log=log,
                     target=ver, hint=hint)


def _start_ps(b64: str, cwd: str, mark: str) -> bool:
    """启动 PowerShell 升级脚本，并确认它**真的开始执行**了

    Popen 返回成功只代表进程创建成功。实测 DETACHED_PROCESS 下 PowerShell
    会立刻退出、脚本一行都不执行 —— 升级于是「看起来交接成功、实际一个文件
    都没换」。所以这里靠脚本自己写的标记文件来判断：写不出来就换下一种方式。
    """
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    for flags, name in ((no_win, "CREATE_NO_WINDOW"),
                        (no_win | detached, "CREATE_NO_WINDOW+DETACHED"),
                        (0, "默认")):
        if mark:
            try:
                os.remove(mark)
            except OSError:
                pass
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", b64],
                cwd=cwd,
                creationflags=flags,
                # 句柄一律 DEVNULL：继承了失效的控制台句柄时 PowerShell 会直接跪
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:                   # noqa: BLE001
            upgrade_log(f"PowerShell 启动失败（{name}）：{exc}")
            continue
        if not mark:
            upgrade_log(f"已启动 PowerShell 升级脚本（{name}）")
            return True
        for _ in range(30):                        # 最多等 3 秒
            time.sleep(0.1)
            if os.path.exists(mark):
                upgrade_log(f"已启动 PowerShell 升级脚本（{name}，已确认执行）")
                return True
        upgrade_log(f"PowerShell 没真正跑起来（{name}），换下一种启动方式")
    upgrade_log("PowerShell 各种启动方式都没成功，改用 bat 兜底")
    return False


def apply_update(plan: UpdatePlan) -> None:
    """执行升级：非 Windows 直接覆盖；Windows 启动升级脚本并退出由其接管"""
    if os.name != "nt":
        # 源码/Linux 环境：直接覆盖（除正在运行的脚本自身外一般无锁）
        upgrade_log("非 Windows 环境，直接覆盖文件")
        for src in plan.staging.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(plan.staging)
            dst = plan.install / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        upgrade_log("覆盖完成（非 Windows 不重启）")
        return
    # Windows：优先用 PowerShell 编码命令（不受代码页影响），
    # 失败才退回 bat（bat 已参数化，不再写中文路径）。
    if plan.ps_command and _start_ps(plan.ps_command, str(plan.staging),
                                     plan.mark):
        return
    try:
        subprocess.Popen(
            ["cmd", "/c", "_apply_update.bat",
             str(plan.install), plan.launch, plan.proc],
            cwd=str(plan.staging),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        upgrade_log("已启动 bat 升级脚本（兜底路径）")
    except Exception as exc:
        upgrade_log(f"bat 升级脚本也启动失败：{exc}")
        raise


# ================================================================ 一键初始化升级
GITHUB_API = "https://api.github.com"


def _gh_api(method: str, path: str, payload: dict | None = None) -> dict:
    """带 token 调用 GitHub REST API"""
    token = github_token()
    if not token:
        raise PermissionError("还没有配置 GitHub Token，无法自动创建升级仓库")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        GITHUB_API + path, data=body, method=method,
        headers={**github_headers(auth=True), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def build_update_zip(out_path: str | Path | None = None) -> Path:
    """把当前程序文件打成一个升级 zip（源码版 & 打包版都能用）。

    - 源码版：打包项目根下的 main.py / core / ui / assets 等，排除 .git、
      缓存、build、dist 与旧 zip。
    - 打包版：打包 exe 所在目录（排除升级脚本与临时文件），这样升级包
      解压覆盖后即可得到同结构的新版本。
    """
    install = app_install_dir()
    out = Path(out_path) if out_path else Path(
        tempfile.gettempdir()) / f"keymacro-{APP_VERSION}.zip"
    skip_dirs = {".git", "__pycache__", "build", "dist", ".idea", ".vscode",
                 "node_modules", ".codebuddy"}
    skip_suffix = {".zip", ".pyc", ".part", ".log"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in install.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(install)
            if any(p in skip_dirs for p in rel.parts):
                continue
            if src.suffix.lower() in skip_suffix:
                continue
            if src.name.startswith(("_apply_update", "_upgrade_last")):
                continue
            zf.write(src, rel.as_posix())
        # 版本号与更新说明写进包内，供「升级文件夹」模式直接读取，无需 version.json
        zf.writestr("version.txt", APP_VERSION)
        if RELEASE_NOTES:
            zf.writestr("notes.txt", RELEASE_NOTES)
    return out


def setup_github_repo(repo: str, zip_path: str | Path | None = None,
                      notes: str = "") -> dict:
    """一键把当前版本发布到 GitHub，并记住为升级仓库。

    步骤：确保仓库存在 → 生成升级 zip → 建/查 Release（tag = v当前版本）
    → 上传 zip 附件 → 保存 repo 到 update_repo.txt。
    返回 {'repo':..., 'release_url':..., 'tag':..., 'created_repo':bool}
    """
    repo = (repo or "").strip().strip("/")
    if repo.lower().startswith(("http://", "https://")):
        repo = _github_repo_from_url(repo) or repo
    if repo.count("/") != 1:
        raise ValueError("仓库格式应为 用户名/仓库名，例如 zhangsan/keymacro")

    # 1) 仓库不存在就创建（私有仓库，避免脚本被公开）
    created = False
    try:
        _gh_api("GET", f"/repos/{repo}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        _gh_api("POST", "/user/repos", {
            "name": repo.split("/")[1],
            "private": True,
            "has_issues": False,
            "has_wiki": False,
            "description": "按键精灵自动升级仓库",
        })
        created = True

    # 2) 生成升级包
    zp = Path(zip_path) if zip_path else build_update_zip()
    tag = f"v{APP_VERSION}"
    body = notes or f"按键精灵 v{APP_VERSION}"

    # 3) Release（已存在则跳过创建，直接补传附件）
    release = None
    try:
        release = _gh_api("GET", f"/repos/{repo}/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    if release is None:
        release = _gh_api("POST", f"/repos/{repo}/releases", {
            "tag_name": tag,
            "name": f"v{APP_VERSION}",
            "body": body,
            "draft": False,
            "prerelease": False,
        })

    # 4) 上传附件（同名已存在则先删）
    asset_name = f"keymacro-{APP_VERSION}.zip"
    for a in release.get("assets") or []:
        if a.get("name") == asset_name:
            _gh_api("DELETE", f"/repos/{repo}/releases/assets/{a['id']}")
    up_url = (release.get("upload_url") or "").split("{")[0]
    if not up_url:
        raise ValueError("Release 创建失败，缺少上传地址")
    data = zp.read_bytes()
    req = urllib.request.Request(
        up_url + f"?name={urllib.parse.quote(asset_name)}",
        data=data, method="POST",
        headers={**github_headers(auth=True),
                 "Content-Type": "application/zip",
                 "Content-Length": str(len(data))})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        resp.read()

    set_github_repo(repo)
    return {"repo": repo, "tag": tag, "created_repo": created,
            "release_url": release.get("html_url", "")}


# ================================================================ 本地方案
def update_dir() -> str:
    """「升级文件夹」路径（可填共享盘 / 网盘同步目录 / U 盘）"""
    try:
        from .library import app_dir
        f = app_dir() / "update_dir.txt"
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def set_update_dir(path: str) -> None:
    try:
        from .library import app_dir
        (app_dir() / "update_dir.txt").write_text(
            (path or "").strip(), encoding="utf-8")
    except Exception:
        pass


_VER_IN_SOURCE = (
    r'^\s*APP_VERSION\s*=\s*["\']([0-9][0-9.]*)["\']',
    r'^\s*DEFAULT_VERSION\s*=\s*["\']([0-9][0-9.]*)["\']',
)


def _version_from_package(zf) -> str:
    """从源码包内部读版本号。

    「完整打包 zip」里没有 version.txt，但一定带着 core/updater.py
    （或 install.py），从里面读 APP_VERSION / DEFAULT_VERSION 即可。
    """
    try:
        names = zf.namelist()
    except Exception:
        return ""
    def base(n: str) -> str:
        return n.replace("\\", "/").rsplit("/", 1)[-1]
    cands = [n for n in names if base(n) in ("updater.py", "install.py")]
    # core/updater.py 优先，install.py 次之
    cands.sort(key=lambda n: 0 if base(n) == "updater.py" else 1)
    for n in cands:
        try:
            txt = zf.read(n).decode("utf-8", "ignore")
        except Exception:
            continue
        for pat in _VER_IN_SOURCE:
            m = re.search(pat, txt, re.M)
            if m:
                return m.group(1)
    return ""


def zip_version(zip_path: str | Path) -> str:
    """读取升级包版本号。

    依次尝试：包内 version.txt → 包内 version.json → 包内源码里的
    APP_VERSION（完整打包 zip）→ 文件名里的版本号。都找不到返回空串。
    """
    import re
    zp = Path(zip_path)
    try:
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            # 版本文件：根目录优先，也容忍包里套了一层文件夹（发版 zip 常见）
            cands = [n for n in names
                     if n.replace("\\", "/").rsplit("/", 1)[-1].lower()
                     in ("version.txt", "version")]
            cands.sort(key=lambda n: n.count("/"))
            if cands:
                v = zf.read(cands[0]).decode("utf-8", "ignore").strip()
                if v:
                    return v.splitlines()[0].strip()
            # 包里自带的 version.json（「Windows打包」zip 里就有这一份）
            for name in names:
                if name.replace("\\", "/").rsplit("/", 1)[-1] == "version.json":
                    try:
                        data = json.loads(
                            zf.read(name).decode("utf-8-sig", "ignore"))
                        v = str(data.get("version") or "").strip()
                        if v:
                            return v
                    except Exception:
                        pass
                    break
            v = _version_from_package(zf)
            if v:
                return v
    except Exception:
        pass
    m = re.search(r"(\d+(?:\.\d+)+)", zp.stem)
    return m.group(1) if m else ""


def manifest_from_zip(zip_path: str | Path) -> dict:
    """把一个 zip 当成升级包，生成 manifest（供本地/文件夹模式使用）

    版本号读不出来时返回空字符串，绝不拿「当前程序版本」冒充——
    否则会被当成「已是最新」，用户永远升不了级。
    """
    zp = Path(zip_path)
    ver = zip_version(zp)
    notes = ""
    try:
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            for want in ("notes.txt", "更新说明.txt", "readme.txt"):
                cands = [n for n in names
                         if n.replace("\\", "/").rsplit("/", 1)[-1].lower() == want]
                cands.sort(key=lambda n: n.count("/"))     # 根目录优先
                if cands:
                    notes = zf.read(cands[0]).decode("utf-8", "ignore").strip()
                    break
    except Exception:
        pass
    return {"version": ver, "notes": notes, "package": str(zp),
            "source": f"local:{zp}", "version_known": bool(ver)}


def best_zip(d: Path) -> Path | None:
    """文件夹里版本最高的那个 zip（读不出版本的排最后）"""
    zips = [z for z in d.glob("*.zip") if z.is_file()]
    if not zips:
        return None
    best, best_v = None, ()
    for z in zips:
        v = parse_version(zip_version(z))
        if best is None or v > best_v:
            best, best_v = z, v
    return best


def check_update_dir(dir_path: str) -> dict:
    """检查「升级文件夹」里的最新版本。

    文件夹里放什么都可以，程序按以下顺序找：
      1) version.json（含 version / package）
      2) 名字里带版本号的 zip（如 keymacro-1.3.0.zip），取版本最高的一个
      3) 只有一个 zip 时就当它是升级包

    version.json 里的 package 找不到对应文件时**不再直接失败**：
    最常见的情况是用户把整包解压进了文件夹，包名对不上，
    这时改用文件夹里实际的 zip，并把原因写进 note 让界面能显示。
    """
    d = Path(dir_path).expanduser()
    if not d.exists() or not d.is_dir():
        raise FileNotFoundError(f"升级文件夹不存在：{dir_path}")

    note = ""
    vj = d / "version.json"
    if vj.exists():
        data = None
        try:
            data = json.loads(vj.read_text(encoding="utf-8-sig"))
        except Exception:
            note = "version.json 解析失败，已改用文件夹里的 zip"
        if isinstance(data, dict):
            pkg = str(data.get("package") or "").strip()
            local = ""
            if pkg.lower().startswith(("http://", "https://")):
                local = pkg
            elif pkg:
                cand = d / pkg
                if cand.exists():
                    local = str(cand.resolve())
                else:
                    note = (f"version.json 里写的升级包「{pkg}」不在这个文件夹里，"
                            "已改用文件夹里的 zip")
            if not local:
                z = best_zip(d)
                if z is not None:
                    m = manifest_from_zip(z)
                    m["source"] = f"dir:{d}"
                    if note:
                        m["note"] = note
                    upgrade_log(f"升级文件夹：{d} → 选定 {z.name}"
                                f"（{m['version']}）{note}")
                    return m
                if note:
                    raise FileNotFoundError(f"{note}；文件夹里也没有 zip：{d}")
            else:
                data["package"] = local
                data.setdefault("source", f"dir:{d}")
                is_remote = local.lower().startswith(("http://", "https://"))
                if not is_remote:
                    # 以 zip 里读到的版本为准：version.json 容易忘记跟着改，
                    # 那会让程序误判「已是最新」。
                    zv = zip_version(Path(local))
                    dv = str(data.get("version") or "").strip()
                    if zv and (not dv or
                               parse_version(zv) > parse_version(dv)):
                        data["version"] = zv
                    if not str(data.get("notes") or "").strip():
                        data["notes"] = manifest_from_zip(
                            Path(local)).get("notes", "")
                if note:
                    data["note"] = note
                upgrade_log(f"升级文件夹：{d} → version.json 版本 "
                            f"{data.get('version')} {note}")
                return data

    z = best_zip(d)
    if z is None:
        raise FileNotFoundError(f"文件夹里没有升级包（*.zip）：{d}")
    m = manifest_from_zip(z)
    m["source"] = f"dir:{d}"
    if note:
        m["note"] = note
    upgrade_log(f"升级文件夹：{d} → 选定 {z.name}"
                f"（{m['version'] or '版本未知'}）")
    return m


def active_manifest() -> dict:
    """按优先级取当前配置的升级源：文件夹 → 网址 → 报错"""
    d = update_dir()
    if d:
        return check_update_dir(d)
    u = update_url().strip()
    if u:
        return fetch_manifest(u)
    raise ValueError("还没有设置升级来源")


def has_source() -> bool:
    return bool(update_dir().strip() or update_url().strip())


def fetch_package(m: dict, dest_dir: str | None = None) -> Path:
    """拿到升级包本地路径：本地文件直接返回，网址则下载"""
    pkg = str(m.get("package") or "")
    if not pkg:
        raise ValueError("升级信息里没有升级包地址")
    if not pkg.lower().startswith(("http://", "https://")):
        p = Path(pkg)
        if not p.exists():
            raise FileNotFoundError(f"升级包不存在：{pkg}")
        return p
    return download_package(pkg, dest_dir)
