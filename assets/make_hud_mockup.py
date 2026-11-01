"""生成录制迷你控制条（HUD）的设计效果图 —— 纯 PIL 绘制，不涉及实现代码。

输出：
  hud_concept.png    三套方案总览（顶部细条 / 底部胶囊 / 侧边竖条）
  hud_plan_a.png     方案A 详图（各状态 + 悬停展开）
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 配色
BG_CARD = (22, 26, 38)          # 卡片外的展示底
C_BAR = (28, 33, 48)            # HUD 主体
C_BAR_HI = (38, 45, 64)         # HUD 高光
C_TEXT = (238, 242, 250)
C_SUB = (140, 152, 175)
C_REC = (255, 74, 74)           # 录制红
C_PLAY = (74, 160, 255)         # 回放蓝
C_IDLE = (120, 132, 155)
C_ACCENT = (255, 138, 61)       # 品牌橙
C_OK = (66, 208, 138)

SS = 3                          # 超采样倍数


def mix(fg, alpha, bg):
    """把 fg 以 alpha(0~1) 混到 bg 上，返回不透明色（规避 PIL 直接替换 alpha 的问题）"""
    return tuple(int(round(f * alpha + b * (1 - alpha))) for f, b in zip(fg, bg))


# ---------------------------------------------------------------- 字体
def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in cands:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def cn_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """中文字体：优先思源/Noto，退回文泉驿"""
    cands = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return load_font(size, bold)


# ---------------------------------------------------------------- 基础件
def rounded_card(w, h, radius, color, alpha=255, border=None, border_w=1):
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=radius * SS,
                        fill=color + (alpha,),
                        outline=border + (255,) if border else None,
                        width=border_w * SS)
    return img.resize((w, h), Image.LANCZOS)


def soft_shadow(size, radius, blur=18, spread=0, color=(0, 0, 0), alpha=110):
    w, h = size
    pad = blur * 2 + spread + 4
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        [pad - spread, pad - spread, pad + w + spread, pad + h + spread],
        radius=radius + spread, fill=color + (alpha,))
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    return canvas, pad


def paste_shadowed(base, layer, xy, radius, blur=16, spread=0, alpha=100):
    shadow, pad = soft_shadow(layer.size, radius, blur, spread, alpha=alpha)
    base.alpha_composite(shadow, (xy[0] - pad, xy[1] - pad + 4))
    base.alpha_composite(layer, xy)


def pill_button(draw, x, y, w, h, fill, text, font, text_color=C_TEXT,
                border=None, icon=None):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                           fill=fill, outline=border)
    cx = x + w / 2
    if icon:
        # 简单几何图标
        r = h * 0.22
        if icon == "stop":
            draw.rounded_rectangle([cx - r, y + h / 2 - r, cx + r, y + h / 2 + r],
                                   radius=max(1, int(r * 0.35)), fill=text_color)
        elif icon == "pause":
            bw = max(2, int(r * 0.55))
            draw.rounded_rectangle([cx - r * 1.1, y + h / 2 - r,
                                    cx - r * 1.1 + bw, y + h / 2 + r],
                                   radius=1, fill=text_color)
            draw.rounded_rectangle([cx + r * 1.1 - bw, y + h / 2 - r,
                                    cx + r * 1.1, y + h / 2 + r],
                                   radius=1, fill=text_color)
        elif icon == "record":
            draw.ellipse([cx - r, y + h / 2 - r, cx + r, y + h / 2 + r],
                         fill=text_color)
        elif icon == "play":
            draw.polygon([(cx - r * 0.7, y + h / 2 - r),
                          (cx - r * 0.7, y + h / 2 + r),
                          (cx + r * 1.0, y + h / 2)], fill=text_color)
        return
    if text:
        tw = draw.textlength(text, font=font)
        draw.text((cx - tw / 2, y + h / 2), text, font=font, fill=text_color,
                  anchor="lm")


def rec_dot(draw, cx, cy, r, color=C_REC, glow=True):
    if glow:
        for i, a in ((3.0, 30), (2.2, 45), (1.5, 70)):
            draw.ellipse([cx - r * i, cy - r * i, cx + r * i, cy + r * i],
                         fill=color + (a,))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


# ================================================================ 方案 A
def bar_top(w=430, h=48, state="recording", hover=False, scale=SS):
    """方案 A：顶部居中细长条，始终置顶"""
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    f_num = cn_font(int(13 * scale), bold=True)
    f_lbl = cn_font(int(11 * scale))

    # 底：85% 不透明深色胶囊 + 1px 亮边
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2,
                        fill=C_BAR + (245,), outline=(255, 255, 255, 26), width=max(1, scale))

    # 左侧品牌色拖拽握把
    d.rounded_rectangle([0, 0, 10 * scale, H - 1], radius=H // 2,
                        fill=C_ACCENT + (210,))
    for i in range(3):
        yy = H / 2 + (i - 1) * 5 * scale
        d.ellipse([3.4 * scale, yy - 1.1 * scale, 5.6 * scale, yy + 1.1 * scale],
                  fill=(255, 255, 255, 150))

    x = 22 * scale
    if state == "recording":
        rec_dot(d, x + 6 * scale, H / 2, 6 * scale)
        x += 20 * scale
        d.text((x, H / 2 - 1 * scale), "REC", font=f_lbl, fill=C_REC, anchor="lm")
        x += 30 * scale
        d.text((x, H / 2), "00:12", font=f_num, fill=C_TEXT, anchor="lm")
        x += 46 * scale
        d.line([(x, H / 2 - 9 * scale), (x, H / 2 + 9 * scale)],
               fill=(255, 255, 255, 26), width=max(1, scale))
        x += 10 * scale
        d.text((x, H / 2 - 7 * scale), "事件 37", font=f_lbl, fill=C_TEXT, anchor="lm")
        d.text((x, H / 2 + 7 * scale), "点击 8", font=f_lbl, fill=C_SUB, anchor="lm")
    elif state == "playback":
        rec_dot(d, x + 6 * scale, H / 2, 6 * scale, C_PLAY)
        x += 20 * scale
        d.text((x, H / 2 - 1 * scale), "PLAY", font=f_lbl, fill=C_PLAY, anchor="lm")
        x += 36 * scale
        d.text((x, H / 2), "第 2/5 轮", font=f_num, fill=C_TEXT, anchor="lm")
        x += 62 * scale
        # 进度条
        bw = 56 * scale
        d.rounded_rectangle([x, H / 2 - 2.5 * scale, x + bw, H / 2 + 2.5 * scale],
                            radius=3 * scale, fill=(255, 255, 255, 30))
        d.rounded_rectangle([x, H / 2 - 2.5 * scale, x + bw * 0.42, H / 2 + 2.5 * scale],
                            radius=3 * scale, fill=C_PLAY)
        x += bw + 10 * scale
        d.text((x, H / 2), "步骤 18/43", font=f_lbl, fill=C_SUB, anchor="lm")
    else:
        rec_dot(d, x + 6 * scale, H / 2, 6 * scale, C_IDLE, glow=False)
        x += 20 * scale
        d.text((x, H / 2 - 7 * scale), "空闲", font=f_lbl, fill=C_SUB, anchor="lm")
        d.text((x, H / 2 + 7 * scale), "F11 开始", font=f_lbl,
               fill=(120, 132, 155, 190), anchor="lm")

    # 右侧按钮组
    bx = W - 18 * scale
    if state == "recording":
        # 停止(红)
        d.rounded_rectangle([bx - 30 * scale, H / 2 - 14 * scale, bx, H / 2 + 14 * scale],
                            radius=14 * scale, fill=C_REC + (235,))
        pill_button(d, bx - 30 * scale, H / 2 - 14 * scale, 30 * scale, 28 * scale,
                    (0, 0, 0, 0), "", f_lbl, icon="stop")
        bx -= 40 * scale
        # 暂停(灰描边)
        d.rounded_rectangle([bx - 30 * scale, H / 2 - 14 * scale, bx, H / 2 + 14 * scale],
                            radius=14 * scale, fill=(255, 255, 255, 18),
                            outline=(255, 255, 255, 40), width=max(1, scale))
        pill_button(d, bx - 30 * scale, H / 2 - 14 * scale, 30 * scale, 28 * scale,
                    (0, 0, 0, 0), "", f_lbl, icon="pause")
        bx -= 44 * scale
    elif state == "playback":
        d.rounded_rectangle([bx - 30 * scale, H / 2 - 14 * scale, bx, H / 2 + 14 * scale],
                            radius=14 * scale, fill=C_REC + (235,))
        pill_button(d, bx - 30 * scale, H / 2 - 14 * scale, 30 * scale, 28 * scale,
                    (0, 0, 0, 0), "", f_lbl, icon="stop")
        bx -= 44 * scale
    else:
        d.rounded_rectangle([bx - 30 * scale, H / 2 - 14 * scale, bx, H / 2 + 14 * scale],
                            radius=14 * scale, fill=C_ACCENT + (225,))
        pill_button(d, bx - 30 * scale, H / 2 - 14 * scale, 30 * scale, 28 * scale,
                    (0, 0, 0, 0), "", f_lbl, icon="record", text_color=(30, 20, 10))
        bx -= 40 * scale
        d.rounded_rectangle([bx - 30 * scale, H / 2 - 14 * scale, bx, H / 2 + 14 * scale],
                            radius=14 * scale, fill=(255, 255, 255, 18),
                            outline=(255, 255, 255, 40), width=max(1, scale))
        pill_button(d, bx - 30 * scale, H / 2 - 14 * scale, 30 * scale, 28 * scale,
                    (0, 0, 0, 0), "", f_lbl, icon="play")
        bx -= 44 * scale

    # 展开按钮（悬停时出现）
    if hover:
        d.rounded_rectangle([bx - 26 * scale, H / 2 - 12 * scale, bx, H / 2 + 12 * scale],
                            radius=12 * scale, fill=(255, 255, 255, 26))
        for i in (-1, 0, 1):
            d.ellipse([bx - 13 * scale - 1.4 * scale, H / 2 + i * 4.4 * scale - 1.4 * scale,
                       bx - 13 * scale + 1.4 * scale, H / 2 + i * 4.4 * scale + 1.4 * scale],
                      fill=(255, 255, 255, 200))

    return img


# ================================================================ 方案 B
def bar_bottom(w=300, h=64, state="recording", scale=SS):
    """方案 B：底部悬浮胶囊，单排大按钮，信息更少更直观"""
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2,
                        fill=C_BAR + (248,), outline=(255, 255, 255, 28), width=max(1, scale))

    f_num = cn_font(int(15 * scale), bold=True)
    f_lbl = cn_font(int(10 * scale))

    # 左：状态圆环 + 计时
    cx, cy = 32 * scale, H / 2
    d.ellipse([cx - 18 * scale, cy - 18 * scale, cx + 18 * scale, cy + 18 * scale],
              outline=C_REC + (70,), width=max(2, int(2.2 * scale)))
    rec_dot(d, cx, cy, 8 * scale, C_REC if state == "recording" else
            (C_PLAY if state == "playback" else C_IDLE),
            glow=(state != "idle"))
    d.text((62 * scale, cy - 8 * scale), "00:12", font=f_num, fill=C_TEXT, anchor="lm")
    d.text((62 * scale, cy + 10 * scale),
           "37 事件 · 8 点击" if state == "recording" else "第 2/5 轮", font=f_lbl,
           fill=C_SUB, anchor="lm")

    # 右：三个大圆钮
    r = 19 * scale
    gap = 12 * scale
    bx = W - 24 * scale - r
    # 更多
    d.ellipse([bx - r, cy - r, bx + r, cy + r], fill=(255, 255, 255, 20))
    for i in (-1, 0, 1):
        d.ellipse([bx - 1.5 * scale, cy + i * 5 * scale - 1.5 * scale,
                   bx + 1.5 * scale, cy + i * 5 * scale + 1.5 * scale],
                  fill=(255, 255, 255, 190))
    bx -= 2 * r + gap
    # 停止
    d.ellipse([bx - r, cy - r, bx + r, cy + r], fill=C_REC + (240,))
    pill_button(d, bx - r, cy - r, 2 * r, 2 * r, (0, 0, 0, 0), "", f_lbl,
                icon="stop")
    bx -= 2 * r + gap
    # 暂停
    d.ellipse([bx - r, cy - r, bx + r, cy + r], fill=(255, 255, 255, 24),
              outline=(255, 255, 255, 46), width=max(1, scale))
    pill_button(d, bx - r, cy - r, 2 * r, 2 * r, (0, 0, 0, 0), "", f_lbl,
                icon="pause")
    return img


# ================================================================ 方案 C
def bar_side(w=64, h=252, state="recording", scale=SS):
    """方案 C：右侧竖条，占地最小，竖屏/超宽屏友好"""
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=W // 2,
                        fill=C_BAR + (246,), outline=(255, 255, 255, 26), width=max(1, scale))

    f_num = cn_font(int(12 * scale), bold=True)
    f_lbl = cn_font(int(9 * scale))

    cy = 22 * scale
    rec_dot(d, W / 2, cy, 6 * scale)
    cy += 20 * scale
    d.text((W / 2, cy), "00:12", font=f_num, fill=C_TEXT, anchor="mm")
    cy += 16 * scale
    d.text((W / 2, cy), "37", font=f_num, fill=C_ACCENT, anchor="mm")
    d.text((W / 2, cy + 11 * scale), "事件", font=f_lbl, fill=C_SUB, anchor="mm")

    # 竖排按钮
    r = 16 * scale
    by = H - 24 * scale - r
    for label, color, icon in (
        ("更多", (255, 255, 255, 20), "dots"),
        ("停止", C_REC + (238,), "stop"),
        ("暂停", (255, 255, 255, 24), "pause"),
    ):
        d.ellipse([W / 2 - r, by - r, W / 2 + r, by + r], fill=color)
        if icon == "dots":
            for i in (-1, 0, 1):
                d.ellipse([W / 2 - 1.4 * scale, by + i * 4.5 * scale - 1.4 * scale,
                           W / 2 + 1.4 * scale, by + i * 4.5 * scale + 1.4 * scale],
                          fill=(255, 255, 255, 190))
        else:
            pill_button(d, W / 2 - r, by - r, 2 * r, 2 * r, (0, 0, 0, 0), "",
                        f_lbl, icon=icon)
        by -= 2 * r + 10 * scale
    return img


# ================================================================ 展示
def label(d, xy, text, font, fill=C_TEXT, anchor="lm"):
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


def make_concept() -> str:
    W, H = 1440, 980
    img = Image.new("RGBA", (W, H), BG_CARD + (255,))
    d = ImageDraw.Draw(img)

    # 柔和背景光斑
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-200, -260, 620, 380], fill=(255, 138, 61, 26))
    gd.ellipse([900, 420, 1650, 1100], fill=(74, 160, 255, 22))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

    f_title = cn_font(34, bold=True)
    f_h2 = cn_font(22, bold=True)
    f_body = cn_font(15)
    f_small = cn_font(13)
    f_tiny = cn_font(12)

    # 标题
    d.text((64, 54), "录制迷你控制条", font=f_title, fill=C_TEXT, anchor="lm")
    d.text((64, 92), "录制 / 回放时主窗口最小化，屏幕上只留这一小条，随时看状态、随时喊停",
           font=f_body, fill=C_SUB, anchor="lm")

    # 屏幕示意框
    sx, sy, sw, sh = 64, 130, 1312, 470
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=14,
                        fill=(15, 18, 28, 255), outline=(255, 255, 255, 22), width=2)
    # 屏幕内容暗示（目标窗口里的文档线条）
    d2 = ImageDraw.Draw(img)
    for i in range(11):
        yy = sy + 54 + i * 34
        ww = sw - 260 - (i % 4) * 90
        d2.rounded_rectangle([sx + 90, yy, sx + 90 + ww, yy + 9], radius=4,
                             fill=mix((255,255,255), 0.07, (15,18,28)))
    d.text((sx + sw / 2, sy + 26), "目标窗口（你正在操作的软件）", font=f_tiny,
           fill=mix((140,152,175), 0.85, (15,18,28)), anchor="mm")

    # —— 方案A：顶部居中（渲染后缩小到 60% 再贴，避免过大）
    barA = bar_top(state="recording")
    barA = barA.resize((int(barA.width * 0.6), int(barA.height * 0.6)), Image.LANCZOS)
    paste_shadowed(img, barA, (sx + sw // 2 - barA.width // 2, sy + 18),
                   barA.height // 2, blur=18, spread=1, alpha=120)
    d.text((sx + 22, sy + 30), "A", font=f_h2, fill=C_ACCENT, anchor="lm")

    # —— 方案B：底部居中
    barB = bar_bottom(state="recording")
    barB = barB.resize((int(barB.width * 0.6), int(barB.height * 0.6)), Image.LANCZOS)
    paste_shadowed(img, barB, (sx + sw // 2 - barB.width // 2, sy + sh - barB.height - 22),
                   barB.height // 2, blur=18, spread=1, alpha=120)
    d.text((sx + 22, sy + sh - 54), "B", font=f_h2, fill=C_ACCENT, anchor="lm")

    # —— 方案C：右侧竖条
    barC = bar_side(state="recording")
    barC = barC.resize((int(barC.width * 0.6), int(barC.height * 0.6)), Image.LANCZOS)
    cy_c = sy + sh // 2 - barC.height // 2
    paste_shadowed(img, barC, (sx + sw - barC.width - 16, cy_c),
                   barC.width // 2, blur=18, spread=1, alpha=120)
    d.text((sx + sw - barC.width - 44, sy + sh // 2), "C", font=f_h2,
           fill=C_ACCENT, anchor="rm")

    # 三栏说明
    cols = [
        ("A　顶部居中细条　430 × 48", "信息最全：计时 / 事件数 / 点击次数 / 停止 / 暂停。\n"
                                  "贴顶不挡内容，宽度小；适合长时间录制。", "★ 推荐"),
        ("B　底部悬浮胶囊　300 × 64", "按钮最大最好点，信息精简为「时长 + 事件数」。\n"
                                  "适合快速记录简单操作，误点概率最低。", "轻量"),
        ("C　右侧竖条　64 × 252", "占地最窄，横向几乎不占空间；信息竖排。\n"
                                "适合超宽屏或右侧有空白区的场景。", "省地方"),
    ]
    cw = (1312 - 2 * 24) // 3
    for i, (title, desc, tag) in enumerate(cols):
        cx = 64 + i * (cw + 24)
        cy = 628
        d.rounded_rectangle([cx, cy, cx + cw, cy + 150], radius=12,
                            fill=mix((255,255,255), 0.06, BG_CARD),
                            outline=mix((255,255,255), 0.12, BG_CARD), width=1)
        d.text((cx + 20, cy + 26), title, font=cn_font(17, bold=True),
               fill=(255, 255, 255, 255), anchor="lm")
        # tag
        tw = d.textlength(tag, font=f_tiny)
        d.rounded_rectangle([cx + cw - 20 - tw - 16, cy + 14, cx + cw - 20, cy + 38],
                            radius=12, fill=mix(C_ACCENT, 0.25, mix((255,255,255), 0.06, BG_CARD)))
        d.text((cx + cw - 28, cy + 26), tag, font=f_tiny, fill=C_ACCENT, anchor="rm")
        for k, line in enumerate(desc.split("\n")):
            d.text((cx + 20, cy + 62 + k * 24), line, font=f_small, fill=C_SUB, anchor="lm")

    # 底部共同特性
    d.text((64, 812), "共同点", font=cn_font(17, bold=True), fill=C_TEXT, anchor="lm")
    feats = [
        ("始终置顶", "盖在任何窗口上，不被目标软件遮住"),
        ("可拖拽", "按住左侧橙色握把随意挪位置，位置会记住"),
        ("点击穿透不抢焦点", "点按钮不会让目标窗口失焦，录制不中断"),
        ("从录制区排除", "这一条自己的区域自动排除，不会录进脚本"),
        ("状态一眼可见", "红=录制　蓝=回放　灰=空闲，颜色区分"),
    ]
    fx = 64
    for i, (k, v) in enumerate(feats):
        col = i % 3
        row = i // 3
        cx = 64 + col * 448
        cy = 848 + row * 46
        d.ellipse([cx, cy - 3, cx + 6, cy + 3], fill=C_OK)
        d.text((cx + 16, cy), k, font=cn_font(14, bold=True), fill=C_TEXT, anchor="lm")
        d.text((cx + 16 + d.textlength(k, font=cn_font(14, bold=True)) + 10, cy),
               v, font=f_small, fill=C_SUB, anchor="lm")

    d.text((64, 946), "先看效果，确认方案后我再改代码接入录制流程。",
           font=f_small, fill=mix(C_SUB, 0.9, BG_CARD), anchor="lm")

    path = os.path.join(OUT, "hud_concept.png")
    img.convert("RGB").save(path, quality=95)
    return path


def make_plan_a() -> str:
    """方案 A 详图：三种状态 + 悬停展开"""
    W, H = 1240, 760
    img = Image.new("RGBA", (W, H), BG_CARD + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-160, -200, 560, 320], fill=(255, 138, 61, 24))
    gd.ellipse([760, 480, 1400, 1060], fill=(74, 160, 255, 20))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

    d = ImageDraw.Draw(img)
    f_title = cn_font(30, bold=True)
    f_h2 = cn_font(18, bold=True)
    f_body = cn_font(14)
    f_small = cn_font(13)

    d.text((56, 46), "方案 A · 状态与交互详图", font=f_title, fill=C_TEXT, anchor="lm")
    d.text((56, 82), "一条控件覆盖录制、回放、空闲三种状态，悬停展开更多操作",
           font=f_body, fill=C_SUB, anchor="lm")

    rows = [
        ("录制中", bar_top(state="recording"),
         "红点呼吸闪烁 + 计时 + 事件/点击计数；右侧「暂停」「停止(红)」。"),
        ("录制中 · 悬停", bar_top(state="recording", hover=True),
         "鼠标移上去：出现「⋯」更多按钮（最近脚本 / 标记 / 设置）。"),
        ("回放中", bar_top(state="playback"),
         "蓝点 + 轮次 + 进度条 + 步骤数；右侧只有「停止」。"),
        ("空闲", bar_top(state="idle"),
         "灰点 + F11 提示；右侧「回放」「录制」两个入口。"),
    ]
    y = 128
    for name, bar, desc in rows:
        bar = bar.resize((int(bar.width * 0.44), int(bar.height * 0.44)), Image.LANCZOS)
        d.text((56, y), name, font=f_h2, fill=C_TEXT, anchor="lm")
        paste_shadowed(img, bar, (56, y + 22), bar.height // 2, blur=18, spread=1, alpha=110)
        d.text((56, y + 22 + bar.height + 22), desc, font=f_small, fill=C_SUB, anchor="lm")
        y += 22 + bar.height + 62

    # 右侧交互说明卡片
    cx = 640
    d.rounded_rectangle([cx, 128, cx + 544, 686], radius=14,
                        fill=mix((255,255,255), 0.05, BG_CARD),
                        outline=mix((255,255,255), 0.11, BG_CARD), width=1)
    d.text((cx + 26, 158), "交互细节", font=cn_font(17, bold=True), fill=(255,255,255), anchor="lm")

    items = [
        ("拖拽移动", "按住左侧橙色握把（三点纹）拖动；松手即记住坐标，\n下次录制仍在原位置。"),
        ("双击握把", "从迷你条直接恢复主窗口（等价于停止录制并回到主界面）。"),
        ("滚轮悬停", "在条上滚动滚轮 = 调整录制时的「保留移动点」数量（0~10）。"),
        ("不抢焦点", "条上的按钮点击不会激活本程序，目标窗口保持输入焦点。"),
        ("自动避让", "若与目标窗口当前位置重叠，会自动滑到屏幕顶部/底部空白处。"),
        ("退出全屏游戏", "检测到全屏独占（如游戏）时可切换为「仅热键」模式，不显示条。"),
    ]
    yy = 200
    for k, v in items:
        d.ellipse([cx + 26, yy - 4, cx + 34, yy + 4], fill=C_ACCENT)
        d.text((cx + 44, yy - 12), k, font=cn_font(14, bold=True), fill=(255,255,255), anchor="lm")
        for i, line in enumerate(v.split("\n")):
            d.text((cx + 44, yy + 8 + i * 20), line, font=f_small, fill=C_SUB, anchor="lm")
        yy += 42 + (len(v.split("\n")) - 1) * 20 + 22

    path = os.path.join(OUT, "hud_plan_a.png")
    img.convert("RGB").save(path, quality=95)
    return path


if __name__ == "__main__":
    p1 = make_concept()
    p2 = make_plan_a()
    print(p1)
    print(p2)
