"""test_image_match.py —— 找图匹配的自检脚本（纯 numpy，不需要显示器）

覆盖几个真实踩过的坑：
  · 桌面上的模板图片会 1:1 渲染缩略图，NCC=1.0 盖过真正的目标 → 必须跳过它
  · 屏幕上有两处真实相似目标时不许瞎点（分数压到阈值以下）
  · 窗口失焦变暗导致整体灰度平移十几级 → 仍应命中
  · 结构相似但内容不同的干扰块 → 必须拒绝
  · 降采样粗搜不能因为"降采样后不像"就整体判失败

运行：python test_image_match.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import image_match as im

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'OK ' if cond else 'NG '} {name}{('  ' + str(extra)) if extra else ''}")


def make_template():
    """造一个有明显结构的灰度模板（渐变圆 + 斜边），等价于一张图标"""
    h, w = 39, 32
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    body = np.clip(220 - r * 7.0, 40, 220)
    body[(xx > w * 0.55) & (yy > h * 0.45)] *= 0.55
    body = np.clip(body, 0, 255).astype(np.float32)
    return body


def scene(seed=7, h=600, w=900):
    rng = np.random.default_rng(seed)
    return rng.normal(120, 10, (h, w)).astype(np.float32)


def put(img, x, y, patch):
    img[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return img


T = make_template()
TH, TW = T.shape

# 前几节与屏幕归属无关：打桩成「判断不了」，结果不随真实桌面/窗口变化
im._WHERE_FN = lambda x, y: ""


def run(img):
    dbg = {}
    return im.match_gray(img, T, debug=dbg), dbg


def centred(res, x, y, tol=2):
    return res is not None and abs(res[1] - x) <= tol and abs(res[2] - y) <= tol


print("=" * 62)
print("一、基础命中与坐标")
print("=" * 62)
img = put(scene(), 400, 300, T)
res, dbg = run(img)
check("单处目标能命中", centred(res, 400, 300), res)
check("相似度接近 1", res is not None and res[0] > 0.99, res)

pos = im.locate_on_screen  # 存在性检查（需要屏幕，这里不实际调用）
check("locate_on_screen 仍导出", callable(pos))

print()
print("=" * 62)
print("二、屏幕上的「模板图片本身」（桌面缩略图 / 预览图）")
print("=" * 62)
img = scene()
put(img, 60, 60, T)                                  # 1:1 渲染 → MAE=0
put(img, 600, 400, np.clip(T + 12, 0, 255))          # 真实目标，有渲染差异
res, dbg = run(img)
check("跳过逐像素相同的缩略图", centred(res, 600, 400), res)
check("报告里记录了跳过", "skipped_self" in dbg, dbg.get("skipped_self"))

img = scene()
put(img, 60, 60, np.clip(T + 10, 0, 255))
put(img, 600, 400, np.clip(T + 12, 0, 255))
res, dbg = run(img)
check("两处真实目标 → 判不可信", dbg.get("ambiguous") is True and res[0] <= 0.9, res)

print()
print("=" * 62)
print("三、亮度变化与干扰块")
print("=" * 62)
res, _ = run(put(scene(), 300, 200, np.clip(T - 20, 0, 255)))
check("整体偏暗 20 仍命中", centred(res, 300, 200), res)

res, _ = run(put(scene(), 300, 200, np.clip(T + 25, 0, 255)))
check("整体偏亮 25 仍命中", centred(res, 300, 200), res)

rng = np.random.default_rng(3)
noisy = np.clip(T + rng.normal(0, 25, T.shape), 0, 255).astype(np.float32)
res, _ = run(put(scene(), 300, 200, noisy))
check("结构相似但内容不同的噪声块被拒绝", res is None or not centred(res, 300, 200), res)

res, _ = run(scene())
check("目标不存在时不命中", res is None, res)

print()
print("=" * 62)
print("四、无法使用的模板")
print("=" * 62)
flat = np.full((39, 32), 200.0, dtype=np.float32)
res, _ = run(put(scene(), 300, 200, flat))
check("大片纯色的模板被拒绝", res is None, res)

check("过小的模板被拒绝", im.match_gray(scene(), np.zeros((1, 1), np.float32)) is None)
check("模板比画面大时被拒绝", im.match_gray(np.zeros((10, 10), np.float32), T) is None)

print()
print("=" * 62)
print("五、限定搜索区域（点任务栏却点到桌面图标）")
print("=" * 62)
import tempfile

from PIL import Image

tpath = os.path.join(tempfile.mkdtemp(), "tmpl.png")
Image.fromarray(T.astype(np.uint8)).save(tpath)

# 桌面上一处同款（模板图片文件本身的缩略图）、任务栏里一处同款
img = scene()
put(img, 60, 60, T)
put(img, 600, 500, T)
shot = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))

pos = im.locate_on_screen(tpath, 90, screen=shot, region=(0, 480, 900, 120))
check("限定区域后区域外的同款不干扰",
      pos == (600 + TW // 2, 500 + TH // 2), pos)

pos_all = im.locate_on_screen(tpath, 90, screen=shot)
check("区域外的同款会干扰全屏结果（未限定时判不可信）", pos_all is None, pos_all)

pos_small = im.locate_on_screen(tpath, 90, screen=shot, region=(0, 0, 10, 600))
check("模板比区域大时给出明确原因",
      pos_small is None and "区域" in (im.LAST_REPORT.get("error") or ""),
      im.LAST_REPORT.get("error"))

print()
print("=" * 62)
print("六、自动判断任务栏图标 / 桌面图标")
print("=" * 62)
# 打桩：y >= 480 视为任务栏，上方视为桌面（真实判断在 core/window.point_where）
im._WHERE_FN = lambda x, y: "taskbar" if y >= 480 else "desktop"

img = scene()
put(img, 60, 60, T)                                  # 桌面上的同款
put(img, 600, 500, np.clip(T + 10, 0, 255))          # 任务栏里的图标
shot = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
pos = im.locate_on_screen(tpath, 90, screen=shot)
check("桌面与任务栏各一处 → 点任务栏那处", pos == (600 + TW // 2, 500 + TH // 2), pos)
check("日志说明写清了判断结果", "任务栏" in im.describe_last(), im.describe_last())

img = scene()
put(img, 600, 500, T)                                # 任务栏那处逐像素相同
put(img, 60, 60, np.clip(T + 10, 0, 255))            # 桌面上是缩放渲染
shot = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
pos = im.locate_on_screen(tpath, 90, screen=shot)
check("任务栏里的同款不会被当成「模板图片本身」跳过",
      pos == (600 + TW // 2, 500 + TH // 2), pos)

im._WHERE_FN = lambda x, y: "desktop" if y < 480 else ""
img = scene()
put(img, 60, 60, T)                                  # 桌面缩略图（1:1）
put(img, 600, 500, np.clip(T + 12, 0, 255))          # 窗口里的真实目标
shot = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
pos = im.locate_on_screen(tpath, 90, screen=shot)
check("没有任务栏候选时，仍跳过桌面上的模板图片本身",
      pos == (600 + TW // 2, 500 + TH // 2), pos)
im._WHERE_FN = None

print()
print("=" * 62)
if FAIL:
    print(f"{len(FAIL)} 项失败 / 共 {len(PASS) + len(FAIL)} 项")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print(f"全部通过：{len(PASS)} 项")
