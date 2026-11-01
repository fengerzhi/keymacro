"""test_zip_version.py —— 升级包版本识别（完整打包 zip 也要能认出）"""
import os, sys, json, tempfile, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISPLAY", ":99")

from core import updater

PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✓' if cond else '✗'} {name}{('  ' + str(extra)) if extra else ''}")

tmp = tempfile.mkdtemp(prefix="zipver-")

def make_zip(name, files: dict) -> str:
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return p

# 1) 完整打包 zip：没有 version.txt，只有源码
src = '"""updater"""\nAPP_VERSION = "9.9.9"\nRELEASE_NOTES = "x"\n'
z_src = make_zip("按键精灵-Windows打包.zip",
                 {"main.py": "x", "core/updater.py": src, "ui/app.py": "y"})
check("从包内 core/updater.py 读出版本", updater.zip_version(z_src) == "9.9.9",
      updater.zip_version(z_src))

# 2) version.txt 优先
z_txt = make_zip("pkg.zip", {"version.txt": "2.0.0\n", "core/updater.py": src})
check("version.txt 优先于源码", updater.zip_version(z_txt) == "2.0.0",
      updater.zip_version(z_txt))

# 3) 带一层目录的包
z_nest = make_zip("nested.zip", {"按键精灵/core/updater.py": src})
check("带一层目录也能读出版本", updater.zip_version(z_nest) == "9.9.9",
      updater.zip_version(z_nest))

# 4) 只有文件名带版本号
z_name = make_zip("keymacro-3.1.4.zip", {"main.py": "x"})
check("文件名兜底", updater.zip_version(z_name) == "3.1.4", updater.zip_version(z_name))

# 5) 完全无线索
z_none = make_zip("whatever.zip", {"main.py": "x"})
check("无版本信息返回空串", updater.zip_version(z_none) == "", updater.zip_version(z_none))
m = updater.manifest_from_zip(z_none)
check("manifest 不拿本地版本冒充", m["version"] == "",
      f'{m["version"]!r} vs APP_VERSION={updater.APP_VERSION}')
check("manifest 标记 version_known=False", m["version_known"] is False, m["version_known"])

# 6) install.py 的 DEFAULT_VERSION 兜底
z_ins = make_zip("only_install.zip", {"install.py": 'DEFAULT_VERSION = "4.2.0"\n'})
check("从 install.py 读 DEFAULT_VERSION", updater.zip_version(z_ins) == "4.2.0",
      updater.zip_version(z_ins))

# 7) 升级文件夹：放一个完整打包 zip
d = os.path.join(tmp, "updir")
os.makedirs(d)
import shutil
shutil.copy(z_src, d)
mm = updater.check_update_dir(d)
check("升级文件夹识别出完整包版本", mm["version"] == "9.9.9", mm["version"])
check("识别出的版本可用于比较",
      updater.is_newer(mm["version"], "1.3.1") is True, mm["version"])

# 8) 复现用户场景：真实交付包放进升级文件夹
import glob
cands = sorted(glob.glob("/workspace/按键精灵*Windows打包.zip"))
real = cands[-1] if cands else ""
if os.path.exists(real):
    d2 = os.path.join(tmp, "real")
    os.makedirs(d2)
    shutil.copy(real, d2)
    m2 = updater.check_update_dir(d2)
    check("真实交付包能读出当前版本", m2["version"] == updater.APP_VERSION,
          f'{os.path.basename(real)} -> {m2["version"]}')
    check("真实交付包对 1.3.1 判定为可升级",
          updater.is_newer(m2["version"], "1.3.1") is True, m2["version"])

# 9) version.json 优先级仍然最高
d3 = os.path.join(tmp, "vj")
os.makedirs(d3)
shutil.copy(z_none, os.path.join(d3, "whatever.zip"))
with open(os.path.join(d3, "version.json"), "w", encoding="utf-8") as f:
    json.dump({"version": "5.0.0", "package": "whatever.zip"}, f)
m3 = updater.check_update_dir(d3)
check("version.json 优先且能解析相对包名", m3["version"] == "5.0.0"
      and m3["package"].endswith("whatever.zip"), m3)

# 10) 旧版程序的兜底：文件名带版本号就能被认出来
if cands:
    import re as _re
    stem = os.path.basename(real).rsplit(".", 1)[0]
    mm2 = _re.search(r"(\d+(?:\.\d+)+)", stem)
    check("文件名里带版本号（旧版程序靠它识别）",
          bool(mm2) and mm2.group(1) == updater.APP_VERSION,
          mm2.group(1) if mm2 else "无")

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + "、".join(FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)
