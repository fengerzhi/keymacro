#!/usr/bin/env python3
"""make_icon_variants.py —— 生成多款候选图标供挑选

每个方案都是独立函数，返回 1024×1024 RGBA 图。
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
os.makedirs(OUT, exist_ok=True)

S = 1024
SS = 4          # 超采样倍数


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def diag_gradient(size, c1, c2, angle=(0.45, 0.55)):
    img = Image.new("RGB", (size, size))
    px = img.load()
    ax, ay = angle
    for y in range(size):
        for x in range(size):
            t = min(1.0, x / size * ax + y / size * ay)
            px[x, y] = lerp(c1, c2, t)
    return img


def radial_gradient(size, inner, outer, cx=0.35, cy=0.28):
    img = Image.new("RGB", (size, size))
    px = img.load()
    maxd = math.hypot(size, size)
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx * size, y - cy * size) / (maxd * 0.72)
            px[x, y] = lerp(inner, outer, min(1.0, d))
    return img


def round_mask(size, radius_ratio=0.225):
    s = size * SS
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=int(s * radius_ratio), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def squircle_path(size, n=0.72, pad=0.0):
    """超椭圆（squircle）点集，比圆角矩形更饱满"""
    cx = cy = size / 2
    r = size / 2 * (1 - pad)
    pts = []
    steps = 720
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = cx + r * math.copysign(abs(ct) ** n, ct)
        y = cy + r * math.copysign(abs(st) ** n, st)
        pts.append((x, y))
    return pts


def squircle_mask(size, n=0.72, pad=0.0):
    s = size * SS
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).polygon(squircle_path(s, n, pad), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def gloss(base, size, mask, strength=34):
    g = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    gd.ellipse([-size * .35, -size * .65, size * .95, size * .42],
               fill=(255, 255, 255, strength))
    g = g.filter(ImageFilter.GaussianBlur(radius=size * 0.045))
    base.alpha_composite(Image.composite(g, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))


def paste_grad(grad, mask, base_size=S):
    base = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    m = mask if mask.size == (base_size, base_size) else mask.resize((base_size, base_size), Image.LANCZOS)
    base.paste(grad.convert("RGBA"), (0, 0), m)
    return base


def big_layer(size, draw_fn):
    """在放大画布上绘制后缩小，得到抗锯齿"""
    layer = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    draw_fn(d, size * SS)
    return layer.resize((size, size), Image.LANCZOS)


def save(img, name):
    p = os.path.join(OUT, f"variant_{name}.png")
    img.resize((256, 256), Image.LANCZOS).save(p)
    return p


# ============================================================ 方案 A：靶心 + 指针
def variant_a():
    """深色底 + 橙色靶心环，指针射入中心 —— 强调「精准定位」"""
    mask = squircle_mask(S)
    base = paste_grad(diag_gradient(S, (32, 38, 58), (18, 21, 34)), mask)
    gloss(base, S, mask, 26)

    def rings(d, s):
        cx, cy = s * 0.5, s * 0.46
        for r, w, col in ((s * .30, s * .028, (255, 138, 61, 255)),
                          (s * .19, s * .022, (255, 255, 255, 200))):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=int(w))
        r = s * .075
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 92, 61, 255))

    base.alpha_composite(big_layer(S, rings))

    def ptr(d, s):
        k = s / 100.0
        arrow = [(52, 44), (52, 82), (63, 71), (71, 88), (78, 84), (70, 68), (83, 65)]
        pts = [(x * k, y * k) for x, y in arrow]
        d.polygon(pts, fill=(255, 255, 255, 255))
        d.line(pts + [pts[0]], fill=(28, 32, 50, 255), width=int(2.0 * k), joint="curve")

    base.alpha_composite(big_layer(S, ptr))
    return save(base, "A_靶心")


# ============================================================ 方案 B：播放键 + 指针
def variant_b():
    """青绿渐变 + 白色播放三角 —— 强调「一键回放」"""
    mask = squircle_mask(S)
    base = paste_grad(diag_gradient(S, (34, 211, 173), (14, 148, 136)), mask)
    gloss(base, S, mask, 40)

    def tri(d, s):
        cx, cy, r = s * .5, s * .5, s * .27
        pts = [(cx - r * .55, cy - r), (cx - r * .55, cy + r), (cx + r * .95, cy)]
        d.polygon(pts, fill=(255, 255, 255, 255))
        d.line(pts + [pts[0]], fill=(10, 120, 110, 255), width=int(s * .012), joint="curve")

    base.alpha_composite(big_layer(S, tri))

    # 右下小指针
    def ptr(d, s):
        k = s / 100.0
        arrow = [(60, 56), (60, 88), (69, 79), (75, 92), (81, 89), (75, 75), (86, 72)]
        pts = [(x * k, y * k) for x, y in arrow]
        d.polygon(pts, fill=(255, 255, 255, 255))
        d.line(pts + [pts[0]], fill=(10, 120, 110, 255), width=int(2.2 * k), joint="curve")

    base.alpha_composite(big_layer(S, ptr))
    return save(base, "B_播放")


# ============================================================ 方案 C：键盘按键 F
def variant_c():
    """键帽造型 + 指环高光 —— 强调「按键」本身"""
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 外壳（深色键帽底）
    mask_out = round_mask(S, 0.20)
    base.paste(diag_gradient(S, (46, 52, 74), (26, 30, 46)).convert("RGBA"), (0, 0), mask_out)

    # 内层键面（略小，形成立体边）
    inner = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    im = round_mask(S, 0.17)
    inset = int(S * 0.035)
    plate = diag_gradient(S, (72, 84, 120), (52, 62, 96)).convert("RGBA")
    shrunk = Image.new("RGBA", (S - inset * 2, S - inset * 2), (0, 0, 0, 0))
    shrunk.paste(plate.resize((S - inset * 2, S - inset * 2), Image.LANCZOS), (0, 0),
                 im.resize((S - inset * 2, S - inset * 2), Image.LANCZOS))
    base.alpha_composite(shrunk, (inset, inset))

    # 大写 F 与快捷键小字
    def txt(d, s):
        try:
            f1 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(s * .40))
            f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(s * .115))
        except Exception:
            return
        d.text((s * .5, s * .455), "F", font=f1, fill=(255, 255, 255, 255), anchor="mm")
        d.text((s * .5, s * .745), "9  ·  11", font=f2,
               fill=(255, 196, 92, 235), anchor="mm")

    base.alpha_composite(big_layer(S, txt))

    # 顶部高光条（键帽质感）
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(g).rounded_rectangle(
        [S * .12, S * .075, S * .88, S * .16], radius=S * .04,
        fill=(255, 255, 255, 46))
    g = g.filter(ImageFilter.GaussianBlur(S * .012))
    base.alpha_composite(g)
    return save(base, "C_键帽")


# ============================================================ 方案 D：圆环进度 + 指针（极简）
def variant_d():
    """白色极简：单色圆环 + 指针，像系统级工具"""
    mask = squircle_mask(S)
    base = paste_grad(radial_gradient(S, (255, 255, 255), (214, 222, 240)), mask)

    def ring(d, s):
        cx, cy, r = s * .5, s * .5, s * .30
        # 缺口圆环（表示循环回放）
        d.arc([cx - r, cy - r, cx + r, cy + r], start=-58, end=250,
              fill=(79, 108, 240, 255), width=int(s * .085))
        # 箭头端点
        ang = math.radians(-58)
        ex, ey = cx + r * math.cos(ang), cy + r * math.sin(ang)
        a = s * .055
        pts = [(ex + a, ey - a * .2), (ex - a * .5, ey - a), (ex - a * .2, ey + a * .7)]
        d.polygon(pts, fill=(79, 108, 240, 255))

    base.alpha_composite(big_layer(S, ring))

    def ptr(d, s):
        k = s / 100.0
        arrow = [(38, 40), (38, 74), (48, 64), (55, 80), (61, 77), (54, 61), (66, 58)]
        pts = [(x * k, y * k) for x, y in arrow]
        d.polygon(pts, fill=(38, 46, 76, 255))

    base.alpha_composite(big_layer(S, ptr))
    return save(base, "D_极简")


# ============================================================ 方案 E：紫色魔法棒
def variant_e():
    """紫粉渐变 + 星光轨迹 —— 「自动化魔法」的直觉联想"""
    mask = squircle_mask(S)
    base = paste_grad(diag_gradient(S, (168, 85, 247), (236, 72, 153), (0.4, 0.6)), mask)
    gloss(base, S, mask, 38)

    def star(d, s):
        cx, cy = s * .52, s * .48
        # 四角星
        R, r = s * .21, s * .052
        pts = []
        for i in range(8):
            a = math.pi / 4 * i - math.pi / 2
            rad = R if i % 2 == 0 else r
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=(255, 255, 255, 255))

        # 两颗小星
        for (sx, sy, sr) in ((s * .27, s * .27, s * .055), (s * .745, s * .70, s * .042)):
            p2 = []
            for i in range(8):
                a = math.pi / 4 * i - math.pi / 2
                rad = sr if i % 2 == 0 else sr * .3
                p2.append((sx + rad * math.cos(a), sy + rad * math.sin(a)))
            d.polygon(p2, fill=(255, 255, 255, 225))

    base.alpha_composite(big_layer(S, star))
    return save(base, "E_魔法")


# ============================================================ 预览图
def build_preview(paths):
    W, H = 1180, 300
    canvas = Image.new("RGBA", (W, H), (22, 25, 33, 255))
    d = ImageDraw.Draw(canvas)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except Exception:
        f = fs = None

    names = ["A 靶心定位", "B 一键回放", "C 键帽", "D 极简", "E 魔法"]
    x = 26
    for p, nm in zip(paths, names):
        im = Image.open(p)
        big = im.resize((132, 132), Image.LANCZOS)
        canvas.alpha_composite(big, (x, 42))
        if f:
            d.text((x, 186), nm, font=f, fill=(230, 235, 245, 255))
        # 小尺寸行
        sx = x
        for s in (16, 24, 32, 48):
            canvas.alpha_composite(im.resize((s, s), Image.LANCZOS), (sx, 232))
            sx += s + 12
        x += 228
    canvas.save("/tmp/icon_variants.png")
    print("预览：/tmp/icon_variants.png")


if __name__ == "__main__":
    paths = []
    for fn in (variant_a, variant_b, variant_c, variant_d, variant_e):
        paths.append(fn())
        print("生成", paths[-1])
    build_preview(paths)
