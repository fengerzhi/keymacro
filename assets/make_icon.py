#!/usr/bin/env python3
"""make_icon.py —— 生成正式图标（方案 A：靶心定位）

深色 squircle 底 + 橙色靶心环 + 白色指针，强调「精准定位」。
输出 assets/icon.png / icon.ico / icon.icns
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
S = 1024
SS = 4


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def diag_gradient(size, c1, c2):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = min(1.0, x / size * 0.45 + y / size * 0.55)
            px[x, y] = lerp(c1, c2, t)
    return img


def squircle_mask(size, n=0.72):
    s = size * SS
    m = Image.new("L", (s, s), 0)
    cx = cy = s / 2
    r = s / 2
    steps = 720
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        pts.append((cx + r * math.copysign(abs(ct) ** n, ct),
                    cy + r * math.copysign(abs(st) ** n, st)))
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def big_layer(size, draw_fn):
    layer = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), size * SS)
    return layer.resize((size, size), Image.LANCZOS)


def make(size=S):
    mask = squircle_mask(size)
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = diag_gradient(size, (32, 38, 58), (18, 21, 34)).convert("RGBA")
    base.paste(grad, (0, 0), mask)

    # 顶部柔光
    g = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([-size * .35, -size * .65, size * .95, size * .42],
                              fill=(255, 255, 255, 26))
    g = g.filter(ImageFilter.GaussianBlur(radius=size * .045))
    base.alpha_composite(Image.composite(
        g, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))

    # 靶心环
    def rings(d, s):
        cx, cy = s * .5, s * .46
        d.ellipse([cx - s * .30, cy - s * .30, cx + s * .30, cy + s * .30],
                  outline=(255, 138, 61, 255), width=int(s * .028))
        d.ellipse([cx - s * .19, cy - s * .19, cx + s * .19, cy + s * .19],
                  outline=(255, 255, 255, 200), width=int(s * .022))
        r = s * .075
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 92, 61, 255))

    base.alpha_composite(big_layer(size, rings))

    # 指针
    def ptr(d, s):
        k = s / 100.0
        arrow = [(52, 44), (52, 82), (63, 71), (71, 88), (78, 84), (70, 68), (83, 65)]
        pts = [(x * k, y * k) for x, y in arrow]
        d.polygon(pts, fill=(255, 255, 255, 255))
        d.line(pts + [pts[0]], fill=(28, 32, 50, 255),
               width=max(1, int(2.0 * k)), joint="curve")

    base.alpha_composite(big_layer(size, ptr))
    return base


def save_all(master):
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
             (64, 64), (96, 96), (128, 128), (256, 256)]
    master.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    master.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"), format="ICO", sizes=sizes)
    try:
        master.resize((512, 512), Image.LANCZOS).save(
            os.path.join(OUT, "icon.icns"), "ICNS")
    except Exception as e:
        print("icns 跳过:", e)

    print("已生成：")
    for f in ("icon.png", "icon.ico", "icon.icns"):
        p = os.path.join(OUT, f)
        if os.path.exists(p):
            print(f"  {f:12s} {os.path.getsize(p):>8,} 字节")


if __name__ == "__main__":
    m = make()
    save_all(m)
    # 预览
    pv = Image.new("RGBA", (420, 200), (22, 25, 33, 255))
    x = 20
    for s in (16, 24, 32, 48, 64, 96):
        pv.alpha_composite(m.resize((s, s), Image.LANCZOS), (x, 100 - s // 2))
        x += s + 20
    pv.alpha_composite(m.resize((160, 160), Image.LANCZOS), (x + 10, 20))
    pv.save("/tmp/icon_final.png")
    print("预览：/tmp/icon_final.png")
