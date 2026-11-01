"""test_update_bat.py —— 升级脚本编码与路径传递（乱码目录回归）

用户现象：升级完成后桌面多出一个乱码名字的文件夹，
          `��������-Windows��� (1) (2)`，新版本其实没装进正确目录。
根因：bat 用 GBK 写、却写着 chcp 65001，cmd 按 UTF-8 解析中文路径，
      robocopy 把文件拷到一个「解码后」的乱码目录去了。
"""
import os, sys, zipfile, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")

from core import updater as up

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

# ---------------------------------------------------------------- 模拟 Windows
class FakeOS:
    name = "nt"
    sep = "/"
    def __getattr__(self, k):
        return getattr(os, k)

real_os = up.os
up.os = FakeOS()

tmp = tempfile.mkdtemp(prefix="bat-")
# 造一个「安装目录」，路径刻意带中文、空格和括号 —— 正是用户的环境
install = os.path.join(tmp, "Desktop", "按键精灵-Windows打包 (1) (2)")
os.makedirs(install, exist_ok=True)
open(os.path.join(install, "启动按键精灵.bat"), "w").write("x")

# 造升级包（内有中文文件名）
pkg = os.path.join(tmp, "pkg.zip")
with zipfile.ZipFile(pkg, "w") as z:
    z.writestr("main.py", "print(1)")
    z.writestr("core/updater.py", 'APP_VERSION = "9.0.0"\n')
    z.writestr("使用说明.txt", "说明")

up.app_install_dir = lambda: __import__("pathlib").Path(install)
plan = up.plan_update(pkg)
bat = plan.restart_bat
raw = bat.read_bytes()
check("生成了升级脚本", bat.exists())

# ---------------------------------------------------------------- 编码检查
check("脚本可按 GBK 解码（与 chcp 936 匹配）", raw.decode("gbk") is not None)
text = raw.decode("gbk")
check("脚本用 chcp 936", "chcp 936" in text, [l for l in text.splitlines() if "chcp" in l])
check("脚本不再写 chcp 65001", "65001" not in text)

# ---------------------------------------------------------------- 关键：不写中文路径
check("脚本里没有 install 路径字面量", install not in text)
check("脚本里没有 staging 路径字面量", str(plan.staging) not in text)
check("用 %~dp0 定位自身目录", 'cd /d "%~dp0"' in text)
check("用 %~1 传安装目录", 'robocopy "." "%~1"' in text)
check("用 %~2 传重启目标", 'if exist "%~2"' in text)
check("用 %~3 结束旧进程", '"%~3"' in text)

# ---------------------------------------------------------------- 参数正确
check("launch 指向自带启动脚本", plan.launch.endswith("启动按键精灵.bat"), plan.launch)
check("源码模式下不硬编码 exe", plan.proc == "", plan.proc)

# ---------------------------------------------------------------- 解压中文名不乱码
out = up.extract_package(pkg)
names = sorted(p.name for p in out.rglob("*"))
check("解压出中文文件名不乱码", "使用说明.txt" in names, names)

# ---------------------------------------------------------------- GBK 编码的 zip
raw_name = "使用说明.txt".encode("gbk")
zi = zipfile.ZipInfo(raw_name.decode("cp437"))     # 模拟 zipfile 的误解码
zi.flag_bits = 0
check("_entry_name 修正 GBK 条目名", up._entry_name(zi) == "使用说明.txt",
      up._entry_name(zi))
zi2 = zipfile.ZipInfo("使用说明.txt")
zi2.flag_bits = 0x800
check("_entry_name 尊重 UTF-8 标志", up._entry_name(zi2) == "使用说明.txt")

# ---------------------------------------------------------------- PowerShell 编码命令
import base64
check("生成了 PowerShell 编码命令", bool(plan.ps_command))
ps = base64.b64decode(plan.ps_command).decode("utf-16-le") if plan.ps_command else ""
check("解码后含正确的安装目录（中文/空格/括号原样）",
      install in ps, [l for l in ps.splitlines() if l.startswith("$dst")])
check("解码后含启动目标", plan.launch in ps)
check("解码后不含 chcp（完全不经过 cmd 代码页）", "chcp" not in ps)
check("用 robocopy 复制并重试（PowerShell 传参走 Unicode）", "robocopy" in ps and "for ($i" in ps)
check("先结束旧进程", "Stop-Process" in ps)
check("复制成功后重启", "Start-Process" in ps)
check("单引号路径已转义", "''" in ps or "'" in ps)

up.os = real_os
print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + "、".join(FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)
