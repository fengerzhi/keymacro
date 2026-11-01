#!/usr/bin/env python3
"""一键打包脚本 —— 零配置版

用法：
    python build/build.py                # 按当前系统打包（目录版，启动快）
    python build/build.py --onefile      # 单文件模式（不推荐：每次启动解压慢、易被杀软误报）
    python build/build.py --console      # 保留控制台窗口（排查闪退用）

打包过程会自动：
    1. 检查并安装 PyInstaller / pynput / pillow 等依赖
    2. 清理上一次的中间产物
    3. 调用 PyInstaller 生成可执行文件
    4. 打印产物路径并校验文件是否存在

产物：
    Windows → dist/按键精灵/按键精灵.exe  （或 --onefile 时 dist/按键精灵.exe）
    macOS   → dist/按键精灵.app
    Linux   → dist/按键精灵/按键精灵
"""
import os
import platform
import subprocess
import sys

# 控制台编码兜底：GBK 窗口下遇到无法编码的字符用 ? 代替，不让打包中断
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "按键精灵"
WORKPATH = os.path.join(ROOT, ".pyinstaller")     # 中间产物隔离，避免覆盖本目录

# 打包必需依赖（导入名 → pip 包名）
REQUIRED = {
    "PyInstaller": "pyinstaller",
    "pynput": "pynput",
    "PIL": "pillow",
    "numpy": "numpy",       # 找图用的模板匹配（core/image_match.py）
}

# 明确排除的模块（源码零引用，纯属 hook 误收 / 未用的图片编解码器）
EXCLUDES = (
    # numpy 不能排除：找图（core/image_match.py）靠它做模板匹配，
    # 打包后约 +20 MB，换来用户装完就能直接找图、不用再补依赖。
    "psutil",
    "yaml",
    "PIL._avif",             # 7.5 MB，AVIF 编解码
    "PIL._webp",
    "PIL._imagingcms",
    "PIL._imagingmath",
    "PIL.ImageQt",
    "PIL.ImageShow",
    "matplotlib",
    "pandas",
    "scipy",
)

# 打包后从产物里删掉的目录（按名字匹配）
PRUNE_DIRS = ("__pycache__",)

# 打包后删掉的具体路径（相对程序目录）。
# 都是 tkinter 用不到、但 PyInstaller 会整包收进来的东西：
#   tzdata/msgs  —— Tcl 的时区库与本地化消息（程序用 Python 的 time，不经 Tcl）
#   http-2.9.8   —— Tcl 的 HTTP 客户端包
#   各 CJK 编码表 —— Tcl 的 encoding 转换表（tkinter 内部走 UTF-8，用不到）
# 保留 ascii / cp1252 / cp936 / iso8859-1 等基础表，中文显示不受影响。
PRUNE_PATHS = (
    "_internal/_tcl_data/tzdata",
    "_internal/_tcl_data/msgs",
    "_internal/tcl8/8.6/http-2.9.8.tm",
    "_internal/_tcl_data/encoding/big5.enc",
    "_internal/_tcl_data/encoding/cns11643.enc",
    "_internal/_tcl_data/encoding/cp932.enc",
    "_internal/_tcl_data/encoding/cp949.enc",
    "_internal/_tcl_data/encoding/cp950.enc",
    "_internal/_tcl_data/encoding/euc-cn.enc",
    "_internal/_tcl_data/encoding/euc-jp.enc",
    "_internal/_tcl_data/encoding/euc-kr.enc",
    "_internal/_tcl_data/encoding/gb12345.enc",
    "_internal/_tcl_data/encoding/gb2312-raw.enc",
    "_internal/_tcl_data/encoding/jis0208.enc",
    "_internal/_tcl_data/encoding/jis0212.enc",
    "_internal/_tcl_data/encoding/ksc5601.enc",
    "_internal/_tcl_data/encoding/macJapan.enc",
    "_internal/_tcl_data/encoding/shiftjis.enc",
)


LOG_FILE = os.path.join(ROOT, "打包日志.txt")


def log(msg: str) -> None:
    print(msg, flush=True)


def run_stream(cmd: list[str], log_path: str = LOG_FILE) -> int:
    """执行命令：屏幕实时输出 + 同时写入日志文件（失败时凭日志定位）"""
    try:
        f = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        f = None
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", bufsize=1,
                             env=env)
        assert p.stdout is not None
        for line in p.stdout:
            print(line, end="", flush=True)
            if f:
                f.write(line)
        p.wait()
        return p.returncode
    finally:
        if f:
            f.close()


def ensure_deps(py: str) -> bool:
    """确保 py 解释器下依赖齐全，缺则自动安装"""
    missing = []
    for mod, pkg in REQUIRED.items():
        r = subprocess.run([py, "-c", f"import {mod}"],
                           capture_output=True)
        if r.returncode != 0:
            missing.append(pkg)
    if not missing:
        _ensure_optional(py)
        return True

    log(f"缺少依赖：{'、'.join(missing)}，正在自动安装…")
    if not pip_install(py, missing):
        log("\n自动安装失败。请手动执行：")
        log(f"    {py} -m pip install {' '.join(missing)}")
        return False
    _ensure_optional(py)
    return True


def pip_install(py: str, pkgs: list[str]) -> bool:
    """pip 安装：先默认源，失败自动换清华镜像"""
    if subprocess.run([py, "-m", "pip", "install", *pkgs]).returncode == 0:
        return True
    log("默认源安装失败，改用清华镜像重试…")
    return subprocess.run(
        [py, "-m", "pip", "install", *pkgs,
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    ).returncode == 0


def _ensure_optional(py: str) -> None:
    """可选依赖：openpyxl（Excel 数据源）。失败仅警告，不阻断打包。"""
    r = subprocess.run([py, "-c", "import openpyxl"], capture_output=True)
    if r.returncode != 0:
        log("可选依赖 openpyxl 未安装，尝试安装（Excel 数据源用，CSV 不受影响）…")
        pip_install(py, ["openpyxl"])
        r = subprocess.run([py, "-c", "import openpyxl"], capture_output=True)
    if r.returncode != 0:
        log("[!] openpyxl 安装失败：Excel 数据源将不可用（CSV 不受影响）")


def pick_python() -> str:
    """选一个能实际运行 PyInstaller 的解释器

    pyenv / 源码编译的 Python 常缺 --enable-shared，PyInstaller 导入正常
    但运行时报错，因此要实际执行一次 --version 验证。
    """
    if platform.system() == "Windows":
        candidates = [sys.executable]
    else:
        # 当前解释器优先（venv / CI 里依赖就装在它身上），
        # 它跑不动 PyInstaller 时再退回系统 Python。
        candidates = [sys.executable, "/usr/bin/python3", "/usr/local/bin/python3"]

    seen = set()
    for cand in candidates:
        if not cand or cand in seen or not os.path.exists(cand):
            continue
        seen.add(cand)
        r = subprocess.run([cand, "-m", "PyInstaller", "--version"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            if cand != sys.executable:
                log(f"使用 {cand} 打包（当前解释器无法运行 PyInstaller）")
            return cand
    return sys.executable          # 没装，交给 ensure_deps 装


def copy_runtime_files(dist_dir: str) -> None:
    """把安装/卸载脚本拷进程序目录

    拷进去之后，这个文件夹本身就能直接装机（双击里面的 安装按键精灵.bat），
    不需要任何额外工具。
    """
    target = os.path.join(dist_dir, APP_NAME)
    if not os.path.isdir(target):
        return
    # 只放装机用得上的；「生成安装包.bat」是开发用的，不发给用户
    for name in ("install.py", "uninstall.py", "安装按键精灵.bat",
                 "启动按键精灵.bat", "卸载按键精灵.bat", "使用说明.txt"):
        s = os.path.join(ROOT, name)
        if os.path.exists(s):
            try:
                import shutil
                shutil.copy2(s, os.path.join(target, name))
            except OSError:
                pass


def make_installer(dist_dir: str, py: str) -> str | None:
    """把 install.py 打成「安装按键精灵.exe」（需与程序文件夹配套使用）

    默认不再自动构建：外面已经用 build/make_setup.py 生成自包含安装包了，
    这个 exe 只是个搬运工，单独发出去会装不上。
    """
    ico = os.path.join(ROOT, "assets", "icon.ico")
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "安装按键精灵",
        "--distpath", dist_dir,
        "--workpath", os.path.join(WORKPATH, "installer"),
        "--specpath", os.path.join(WORKPATH, "installer"),
        "--paths", ROOT,
    ]
    if os.path.exists(ico):
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "install.py"))
    log("生成安装程序…")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log("[!] 安装程序生成失败，但主程序已打包完成；")
        log("  可复制 dist\按键精灵 文件夹到目标机器，双击里面的 安装按键精灵.bat 安装。")
        return None
    log("  提示：发给别人请用 build/make_setup.py 生成的自包含安装包。")
    # 注意：不把这个安装器再拷一份进程序目录——它是 27 MB 的冗余，
    # 会让每个用户机器上都多装一份、也让「生成升级包」把安装包一起打包。
    exe = os.path.join(dist_dir, "安装按键精灵.exe")
    return exe if os.path.exists(exe) else None


def _size_of(p: str) -> int:
    total = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def _remove_path(p: str) -> int:
    """删除文件/目录并返回释放的字节数。

    删除保护、安全软件都可能拦下删除，这里绝不让异常冒出去中断构建；
    目录删不掉时用「空目录镜像」再兜一次（robocopy 的删除不走那条路径）。
    """
    import shutil as _sh

    if not os.path.exists(p):
        return 0
    if not os.path.isdir(p):
        size = os.path.getsize(p)
        try:
            os.remove(p)
            return size
        except OSError:
            return 0

    size = _size_of(p)
    # Windows 上先用空目录镜像把文件删掉（robocopy 不走删除保护），
    # 让后面的 rmtree 只剩空目录要删——直接 rmtree 上千个文件会被拦，
    # 某些环境还会把构建进程一起打断。
    if platform.system() == "Windows":
        tmp = os.path.join(os.environ.get("TEMP", "."), "km_empty_dir")
        try:
            os.makedirs(tmp, exist_ok=True)
            subprocess.run(["robocopy", tmp, p, "/MIR", "/NFL", "/NDL",
                            "/NJH", "/NJS", "/NP"], capture_output=True)
        except Exception:              # noqa: BLE001
            pass
        _sh.rmtree(tmp, ignore_errors=True)
    try:
        _sh.rmtree(p, ignore_errors=True)
    except Exception:                  # noqa: BLE001
        pass
    return 0 if os.path.isdir(p) else size


def prune_dist(app_dir: str) -> int:
    """删掉产物里用不到的目录/文件，返回节省的字节数"""
    if not os.path.isdir(app_dir):
        return 0

    freed = 0
    # 1) 按名字删除（__pycache__ 等）
    for dirpath, dirnames, _ in os.walk(app_dir, topdown=True):
        for d in list(dirnames):
            if d in PRUNE_DIRS:
                freed += _remove_path(os.path.join(dirpath, d))
                dirnames.remove(d)
    # 2) 按相对路径删除（Tcl 数据等）
    for rel in PRUNE_PATHS:
        freed += _remove_path(os.path.join(app_dir, rel.replace("/", os.sep)))
    return freed


def main() -> int:
    onefile = "--onefile" in sys.argv
    want_console = "--console" in sys.argv
    sysname = platform.system()

    log("=" * 60)
    log(f"按键精灵 · 打包工具    平台：{sysname}    Python：{sys.version.split()[0]}")
    log("=" * 60)

    py = pick_python()
    if not ensure_deps(py):
        return 1

    # 再验证一次（依赖刚装好）
    r = subprocess.run([py, "-m", "PyInstaller", "--version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("PyInstaller 无法运行。若为 Linux，请用系统 Python 打包，或重装带共享库的 Python。")
        return 1
    log(f"PyInstaller 版本：{r.stdout.strip() or '未知'}\n")

    # 先自己清中间目录，再用 --noconfirm 构建。
    # 不用 --clean：它内部一次性删上千个文件，会被删除保护/安全软件拦下，
    # 直接中断构建；这里交给 _remove_path 用 robocopy 兜底清干净。
    removed = _remove_path(WORKPATH)
    if removed:
        log(f"清理中间目录：-{removed / 1024 / 1024:.1f} MB\n")

    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm",
        "--name", APP_NAME,
        "--workpath", WORKPATH,
        "--specpath", WORKPATH,
        "--distpath", os.path.join(ROOT, "dist"),
        # pynput 内部按平台动态导入后端，必须显式收集
        "--collect-submodules", "pynput",
        # 截图相关（PIL 是隐式导入）
        "--hidden-import", "PIL.ImageGrab",
        "--hidden-import", "PIL.ImageTk",
    ]

    # 应用图标：Windows 用 .ico；macOS 只认 .icns（传 .ico 会直接中止构建），
    # 同时把 icon.png 带进包里供窗口图标用。
    ico = os.path.join(ROOT, "assets", "icon.ico")
    icns = os.path.join(ROOT, "assets", "icon.icns")
    png = os.path.join(ROOT, "assets", "icon.png")
    if sysname == "Darwin":
        if os.path.exists(icns):
            cmd += ["--icon", icns]
        else:
            log("[!] 未找到 assets/icon.icns，macOS 包将使用系统默认图标")
    elif os.path.exists(ico):
        cmd.append("--icon")
        cmd.append(ico)
    if os.path.exists(png):
        cmd.append("--add-data")
        sep = ";" if platform.system() == "Windows" else ":"
        cmd.append(f"{png}{sep}icon.png")
    # 瘦身：源码里根本没用到，只是被 hook 误收进模块图的依赖
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")
    if not want_console:
        cmd.append("--windowed")       # GUI 程序不弹黑框

    cmd.append(os.path.join(ROOT, "main.py"))

    log("执行：" + " ".join(cmd) + "\n")
    code = run_stream(cmd)
    if code != 0:
        log("\n" + "=" * 60)
        log(f"打包失败（退出码 {code}）。完整日志已保存到：")
        log(f"    {LOG_FILE}")
        log("")
        log("常见原因：")
        log("  1) 杀毒软件拦截 —— 把本文件夹加入信任后重试")
        log("  2) 依赖不完整 —— 先装依赖再打包：")
        log(f"     {py} -m pip install pynput pillow pyinstaller")
        log("  3) 上次残留 —— 删掉 .pyinstaller 和 dist 文件夹后重试")
        log("")
        log("把「打包日志.txt」发给我，可以直接定位到具体是哪一步失败。")
        log("=" * 60)
        return 1

    ext = ".exe" if sysname == "Windows" else ""
    if sysname == "Darwin":
        target = os.path.join(ROOT, "dist", f"{APP_NAME}.app")
    elif onefile:
        target = os.path.join(ROOT, "dist", f"{APP_NAME}{ext}")
    else:
        target = os.path.join(ROOT, "dist", APP_NAME, f"{APP_NAME}{ext}")

    log("\n" + "=" * 60)
    if os.path.exists(target):
        freed = 0
        if not onefile and os.path.isdir(os.path.join(ROOT, "dist", APP_NAME)):
            freed = prune_dist(os.path.join(ROOT, "dist", APP_NAME))
        # 判断是「单文件」还是「目录/应用包」：单文件模式下产物本身就是可执行文件
        single_file = onefile and os.path.isfile(target)
        if single_file:
            size = os.path.getsize(target)
        else:
            # 目录版 / macOS .app：统计整个产物树
            size = _size_of(target if os.path.isdir(target)
                            else os.path.join(ROOT, "dist", APP_NAME))
        log("打包完成  [OK]")
        log(f"  产物：{target}")
        log(f"  大小：{size / 1024 / 1024:.1f} MB")
        if freed:
            log(f"  清理冗余：-{freed / 1024 / 1024:.1f} MB"
                f"（{'、'.join(PRUNE_DIRS)}）")
        if sysname == "Windows" and not onefile:
            log(f"\n  直接把整个「{APP_NAME}」文件夹拷给别人即可运行，")
            log(f"  可执行文件是里面的 {APP_NAME}.exe")
    else:
        log("打包进程返回成功，但未找到预期产物，请检查 dist 目录。")
        return 1
    if not onefile:
        if sysname == "Windows":
            # 把安装/卸载脚本放进程序目录：拷走文件夹即可自行安装
            copy_runtime_files(os.path.join(ROOT, "dist"))
            log("  安装脚本：已放进程序目录（双击里面的 安装按键精灵.bat 即可装机）")
            if "--installer" in sys.argv:
                inst = make_installer(os.path.join(ROOT, "dist"), py)
                if inst:
                    log(f"  附带安装器：{inst}")
            else:
                log("  对外发布：python build/make_setup.py  （生成自包含的单文件安装包）")
        else:
            log("")
            log(f"  macOS：直接把「{APP_NAME}.app」拖进 应用程序 即可使用（未签名，")
            log("  首次打开请右键 →「打开」）。生成分发用 dmg：")
            log("      python3 build/make_macos.py")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
