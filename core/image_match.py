"""image_match.py —— 内置模板匹配（纯 numpy，不依赖 opencv / pyautogui）

为什么自己实现：
    opencv 打包后 90 MB 上下，pyautogui 还要求用户额外装依赖才能找图，
    对「点任务栏图标」这类小模板来说太重。这里用归一化互相关（NCC）实现，
    numpy 打包只有 20 MB 左右，精度足够。

算法：
    1. 大图先按 2 倍降采样做粗搜，**只用来找"可能的位置"**，不做像素级复核
       （降采样会丢细节，按原图标准复核必然误杀，实测同一张图时好时坏）；
    2. 每个候选位置回到原分辨率，在 ±R 像素内逐点精修，并做亮度 / 对比度 /
       平均像素差复核；
    3. 精修结果按分数排序、按位置去重，得到真正"不同位置"的候选（原来只取
       分数最高的 8 个点，它们全挤在同一个目标周围，别处的竞争目标根本看不到，
       「唯一性检查」形同虚设）；
    4. 消歧：
       · 同时命中任务栏里的图标和桌面上的同款时，优先任务栏那处
         （桌面那处往往只是快捷方式，或者模板图片文件自己的缩略图）；
       · 一处与模板**逐像素相同**（MAE≈0），另一处高度相似但有渲染差异
         —— 前者几乎一定是「模板图片自己显示在屏幕上」（桌面文件图标的缩略图、
         图片查看器、编辑器里的预览图），用户要的是屏幕上的目标，跳过它。
         但**任务栏里的候选不适用**：模板本来就是从那儿截的，逐像素相同
         只能说明目标还在原位；
       · 两处都是真实渲染且分数接近 → 模板区分度不够，判为不可信、不点击。

坐标约定：全部是**物理像素**。程序已声明 DPI 感知，录制、回放、
PIL 截图三者的坐标系一致，无需按缩放比例换算。
"""
from __future__ import annotations

import os

import numpy as np

__all__ = [
    "LAST_REPORT",
    "describe_last",
    "engine_available",
    "grab_screen",
    "load_template",
    "locate_on_screen",
    "match_gray",
]

# ---- 像素级复核阈值（灰度 0-255）------------------------------------------
_MEAN_TOL = 32.0                       # 窗口均值与模板均值的最大差
_STD_RATIO = (0.65, 1.55)              # 窗口 / 模板 标准差比值允许区间
_MAE_TOL = 12.0                        # 去均值后的平均绝对差上限（形状/内容误差）

# 与模板逐像素几乎完全一致 → 屏幕上是「这张图本身」，不是它的显示效果
_SELF_MAE = 2.0
# 判定为"同一目标"的位置距离系数（模板尺寸的比例）
_SAME_TARGET = 0.6

# 最近一次 locate 的诊断信息（UI / 日志用）
LAST_REPORT: dict = {}

# 「这个坐标上是什么」的判断函数（任务栏 / 桌面 / 普通窗口）。
# 默认用 core/window.py 的 ctypes 实现；测试可替换成假数据。
_WHERE_FN = None


def _where_of(px: int, py: int) -> str:
    """该屏幕坐标属于哪里：'taskbar' / 'desktop' / ''（判断不了）"""
    fn = _WHERE_FN
    if fn is None:
        try:
            from .window import point_where as fn        # noqa: N813
        except Exception:
            return ""
    try:
        return fn(int(px), int(py)) or ""
    except Exception:
        return ""


def engine_available() -> bool:
    """找图能力是否可用（numpy + Pillow 都在就行）"""
    try:
        import numpy  # noqa: F401
        from PIL import ImageGrab  # noqa: F401
    except Exception:
        return False
    return True


def grab_screen():
    """抓主屏，返回 PIL.Image（物理像素）"""
    from PIL import ImageGrab

    try:
        return ImageGrab.grab()
    except Exception:
        return None


def load_template(path: str):
    """读模板图，转 RGB（带透明通道的截图忽略 alpha，按黑底处理）"""
    from PIL import Image

    with Image.open(path) as im:
        return im.convert("RGB")


def _to_gray(arr: np.ndarray) -> np.ndarray:
    """RGB/RGBA/灰度 → float32 灰度"""
    if arr.ndim == 2:
        return arr.astype(np.float32)
    if arr.shape[2] >= 3:
        r = arr[..., 0].astype(np.float32)
        g = arr[..., 1].astype(np.float32)
        b = arr[..., 2].astype(np.float32)
        return r * 0.299 + g * 0.587 + b * 0.114
    return arr[..., 0].astype(np.float32)


def _prefix_sum(a: np.ndarray) -> np.ndarray:
    """二维前缀和，返回 (H+1, W+1)"""
    c = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    c[1:, 1:] = np.cumsum(np.cumsum(a.astype(np.float64), axis=0), axis=1)
    return c


def _window_sums(cum: np.ndarray, h: int, w: int) -> np.ndarray:
    """由前缀和算出所有 h×w 窗口的和，形状 (H-h+1, W-w+1)"""
    return cum[h:, w:] - cum[:-h, w:] - cum[h:, :-w] + cum[:-h, :-w]


def _score_map(img: np.ndarray, tmpl: np.ndarray) -> np.ndarray | None:
    """全图归一化互相关分数矩阵，形状 (H-h+1, W-w+1)

    分数越接近 1 越像；平坦窗口（方差趋近 0，NCC 分母趋近 0 会算出天文数字）
    统一置为 -1，后续按分数取峰时自然被排除。
    """
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W:
        return None

    t = tmpl - float(tmpl.mean())
    var_t = float((t * t).sum())
    if var_t <= 1e-9:
        return None

    # 频域互相关（模板翻转后做卷积 = 互相关）
    # 必须用 float64：2560×1600 的画面用 float32 会累积误差，让互相关出现假峰。
    fi = np.fft.rfft2(img.astype(np.float64), s=(H, W))
    ft = np.fft.rfft2(t[::-1, ::-1].astype(np.float64), s=(H, W))
    corr = np.fft.irfft2(fi * ft, s=(H, W))
    num = corr[h - 1:, w - 1:]

    n = h * w
    s1 = _window_sums(_prefix_sum(img), h, w)
    s2 = _window_sums(_prefix_sum(img * img), h, w)
    var_w = s2 - s1 * s1 / n
    np.maximum(var_w, 0.0, out=var_w)

    score = num / (np.sqrt(var_w * var_t) + 1e-9)

    std_w = np.sqrt(var_w / n)
    std_t = float(np.sqrt(var_t / n))
    score[std_w < max(0.8, 0.15 * std_t)] = -1.0
    return score


def _peaks(score: np.ndarray | None, h: int, w: int,
           limit: int = 8, min_score: float = 0.45) -> list[tuple[float, int, int]]:
    """取分数图上"不同位置"的前几个峰（非极大值抑制）

    只保留彼此相距超过半个模板尺寸的点，避免同一目标周围的相邻像素
    把候选名额全部占满。
    """
    if score is None or score.size == 0:
        return []

    work = score.copy()
    rows, cols = work.shape
    dy = max(6, int(h * _SAME_TARGET))
    dx = max(6, int(w * _SAME_TARGET))
    out: list[tuple[float, int, int]] = []
    for _ in range(max(1, limit)):
        i = int(np.argmax(work))
        s = float(work.flat[i])
        if s < min_score:
            break
        y, x = divmod(i, cols)
        out.append((s, x, y))
        y0, y1 = max(0, y - dy), min(rows, y + dy + 1)
        x0, x1 = max(0, x - dx), min(cols, x + dx + 1)
        work[y0:y1, x0:x1] = -2.0
    return out


def _check(win: np.ndarray, tmpl: np.ndarray) -> float | None:
    """像素级复核（NCC 之外的二次确认），通过返回**绝对** MAE，不通过返回 None

    NCC 只比较「去均值后的形状」，两个后果实测都能复现：
      1) 图整体加亮 40 灰度，NCC 仍是 0.9999999；
      2) 屏幕上「结构相似但内容不同」的两块，NCC 会给 0.99，于是点到不相干的地方。
    所以命中后还要核亮度、对比度，以及平均绝对差。

    注意平均差要**先各自去均值再算**：窗口因为失焦变暗、处在半透明遮罩下，
    整体平移十几级灰度非常常见，直接比绝对差会把这类正常目标全部误杀
    （实测整屏偏暗 20 就一个都找不到）。亮度本身由 mean_tol 单独把关，
    这里只需要确认「形状 / 内容」一致。

    返回绝对 MAE 是为了给上层区分「屏幕上是这张图本身」（≈0）用。
    """
    if abs(float(win.mean()) - float(tmpl.mean())) > _MEAN_TOL:
        return None
    st = float(tmpl.std())
    if st > 2.0:                      # 模板本身太平淡时，比例判断不可靠，跳过
        r = float(win.std()) / st
        if not (_STD_RATIO[0] <= r <= _STD_RATIO[1]):
            return None
    shape_err = float(np.abs((win - win.mean()) - (tmpl - tmpl.mean())).mean())
    if shape_err > _MAE_TOL:
        return None
    return float(np.abs(win - tmpl).mean())


def _brightness_ok(win: np.ndarray, tmpl: np.ndarray) -> bool:
    """_check 的布尔版本（保留旧接口）"""
    return _check(win, tmpl) is not None


def _refine(img: np.ndarray, tmpl: np.ndarray, cx: int, cy: int,
            radius: int = 8) -> tuple[float, int, int, float] | None:
    """在 (cx, cy) 附近逐像素精修，返回 (相似度, 左上角 x, 左上角 y, MAE)"""
    H, W = img.shape
    h, w = tmpl.shape
    t = tmpl - float(tmpl.mean())
    var_t = float((t * t).sum())
    if var_t <= 1e-9:
        return None
    n = h * w
    min_std = max(0.8, 0.15 * float(np.sqrt(var_t / n)))

    # 把种子点夹到合法范围（降采样坐标 ×2 可能越界半格）
    cx = max(0, min(int(cx), W - w))
    cy = max(0, min(int(cy), H - h))
    x0, x1 = max(0, cx - radius), min(W - w, cx + radius)
    y0, y1 = max(0, cy - radius), min(H - h, cy + radius)

    best = None
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            win = img[y:y + h, x:x + w]
            a = win - win.mean()
            var_a = float((a * a).sum())
            if var_a <= 1e-9:
                continue
            # 纯色窗口的 NCC 分母趋近 0，分数会虚高，必须跳过
            if float(np.sqrt(var_a / n)) < min_std:
                continue
            s = float((a * t).sum()) / (float(np.sqrt(var_a * var_t)) + 1e-9)
            # 分数不如当前最优就不必做昂贵的像素级复核（复核只可能否决它）
            if best is not None and s < best[0]:
                continue
            mae = _check(win, tmpl)
            if mae is None:
                continue
            if best is None or s > best[0]:
                best = (min(s, 1.0), x, y, mae)
    return best


def _collect_candidates(img: np.ndarray, tmpl: np.ndarray,
                        coarse: bool) -> list[tuple[float, int, int, float]]:
    """产生并精修候选，返回按分数降序、彼此位置不同的列表"""
    H, W = img.shape
    h, w = tmpl.shape
    rate = 2 if coarse else 1

    if coarse:
        sub = img[::rate, ::rate]
        sub_t = tmpl[::rate, ::rate]
    else:
        sub, sub_t = img, tmpl
    if sub_t.shape[0] < 2 or sub_t.shape[1] < 2:
        return []

    score = _score_map(sub, sub_t)
    peaks = _peaks(score, sub_t.shape[0], sub_t.shape[1], limit=8, min_score=0.45)

    cands: list[tuple[float, int, int, float]] = []
    for _s, sx, sy in peaks:
        r = _refine(img, tmpl, sx * rate, sy * rate, radius=8)
        if r is None:
            continue
        # 精修后落在同一个目标上 → 合并
        if any(abs(r[1] - c[1]) < w * _SAME_TARGET
               and abs(r[2] - c[2]) < h * _SAME_TARGET for c in cands):
            continue
        cands.append(r)
    cands.sort(key=lambda c: -c[0])
    return cands


def match_gray(img: np.ndarray, tmpl: np.ndarray,
               debug: dict | None = None,
               origin: tuple[int, int] = (0, 0)) -> tuple[float, int, int] | None:
    """在灰度图里找模板，返回 (相似度, 左上角 x, 左上角 y)

    相似度是归一化互相关系数（-1 ~ 1，越接近 1 越像）。
    大图走「降采样粗搜找位置 + 原图逐点精修」，避免每次找图都花 1 秒以上。
    origin 是 img 左上角在整屏里的偏移（限定区域找图时不为 0），
    用来把候选换算成屏幕坐标，判断它落在任务栏还是桌面。
    """
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W or h < 2 or w < 2:
        return None

    # 对比度太低的模板（大片纯色、淡淡渐变）没有可区分特征：
    # 屏幕上随便一块背景都能跟它「很像」，这正是「图不对也点」的主因，
    # 所以直接判失败，让用户截一张有明确边界的图。
    if float(tmpl.std()) < 8.0:
        return None

    use_coarse = (H * W > 1_200_000 and h >= 16 and w >= 16
                  and h < H // 2 and w < W // 2)
    cands = _collect_candidates(img, tmpl, coarse=True) if use_coarse else []
    if not cands:
        cands = _collect_candidates(img, tmpl, coarse=False)
    if not cands:
        return None

    ox, oy = int(origin[0]), int(origin[1])
    if debug is not None:
        debug["candidates"] = [
            {"score": round(c[0], 4), "x": c[1] + ox, "y": c[2] + oy,
             "cx": c[1] + w // 2 + ox, "cy": c[2] + h // 2 + oy,
             "mae": round(c[3], 1)}
            for c in cands
        ]

    # ---- 消歧 0：这是任务栏里的图标，还是桌面上的同款 -------------------
    # 任务栏那处是"活"的目标（点了会切窗口/开程序）；桌面上那处往往只是
    # 同一个 app 的快捷方式，或者模板图片文件自己渲染出的缩略图。
    # 两处都命中时优先任务栏，否则就会「想点任务栏、却点了桌面图标」。
    where = [_where_of(c[1] + w // 2 + ox, c[2] + h // 2 + oy) for c in cands]
    if debug is not None and any(where):
        debug["where"] = [{"cx": c[1] + w // 2 + ox, "cy": c[2] + h // 2 + oy,
                           "at": wp} for c, wp in zip(cands, where)]

    tb_idx = [i for i, wp in enumerate(where) if wp == "taskbar"]
    if len(cands) >= 2 and tb_idx:
        ti = max(tb_idx, key=lambda k: cands[k][0])
        if cands[ti][0] >= cands[0][0] - 0.03:
            best = cands[ti]
            if debug is not None:
                debug["picked_taskbar"] = {
                    "score": round(best[0], 4),
                    "cx": best[1] + w // 2 + ox, "cy": best[2] + h // 2 + oy,
                    "ignored": [{"cx": c[1] + w // 2 + ox, "cy": c[2] + h // 2 + oy,
                                 "at": wp}
                                for k, (c, wp) in enumerate(zip(cands, where))
                                if k != ti and wp != "taskbar"],
                }
                debug["best"] = {"score": round(best[0], 4),
                                 "cx": best[1] + w // 2 + ox,
                                 "cy": best[2] + h // 2 + oy,
                                 "mae": round(best[3], 1)}
            return best[0], best[1], best[2]

    # ---- 消歧 1：屏幕上的「模板图片本身」------------------------------
    # 桌面文件图标会 1:1 渲染缩略图，与模板逐像素完全相同（MAE≈0），
    # NCC 必然是 1.0，永远盖过真正的目标。这时如果别处还有一个
    # 高度相似、但有抗锯齿/缩放差异的候选，那才是用户要找的东西。
    # 任务栏里的候选不参与这个判断：模板本来就是从那儿截的，逐像素相同
    # 只能说明"目标还在原位"，不是"屏幕上是这张图本身"。
    while len(cands) >= 2:
        self_idx = None
        for i, c in enumerate(cands):
            if where[i] == "taskbar":
                continue
            if c[0] >= 0.995 and c[3] <= _SELF_MAE:
                self_idx = i
                break
        if self_idx is None:
            break
        me = cands[self_idx]
        rest = [c for i, c in enumerate(cands) if i != self_idx]
        rest_where = [wp for i, wp in enumerate(where) if i != self_idx]
        if not rest:
            break
        # 另一处必须"几乎同样高分"但明显有渲染差异，才敢认定前者是图片本身
        if rest[0][0] >= me[0] - 0.02 and rest[0][3] >= max(6.0, me[3] + 6.0):
            if debug is not None:
                debug.setdefault("skipped_self", []).append(
                    {"score": round(me[0], 4), "cx": me[1] + w // 2 + ox,
                     "cy": me[2] + h // 2 + oy})
            cands, where = rest, rest_where
        else:
            break

    best = cands[0]
    if debug is not None:
        debug["best"] = {"score": round(best[0], 4),
                         "cx": best[1] + w // 2 + ox, "cy": best[2] + h // 2 + oy,
                         "mae": round(best[3], 1)}

    # ---- 消歧 2：多处真实目标，无法确定点哪一个 -----------------------
    # 别处还有分数几乎一样的候选 → 模板区分度不够（任务栏里几个图标长得很像），
    # 这时不管点哪一个都可能点错，宁可判为不可信，把分数压到阈值以下。
    if len(cands) >= 2 and cands[1][0] >= best[0] - 0.03:
        if debug is not None:
            debug["ambiguous"] = True
        return max(best[0] - 0.12, 0.0), best[1], best[2]

    return best[0], best[1], best[2]


def describe_last() -> str:
    """把最近一次找图的特殊情况说成一句人话（写进执行日志）"""
    rep = LAST_REPORT
    if rep.get("error"):
        if rep["error"] == "相似度不足":
            cands = rep.get("candidates") or []
            if cands:
                return f"相似度不足（最高 {cands[0]['score']:.0%}）"
            return "相似度不足"
        return str(rep["error"])
    picked = rep.get("picked_taskbar")
    if picked:
        ignored = picked.get("ignored") or []
        if ignored:
            return (f"判断为任务栏图标({picked['cx']},{picked['cy']})，"
                    f"桌面上的同款({ignored[0]['cx']},{ignored[0]['cy']})没有点")
        return f"判断为任务栏图标({picked['cx']},{picked['cy']})"
    if rep.get("ambiguous"):
        return "屏幕上有不止一处相似目标，判为不可信、不点击（把模板图从桌面移走更稳）"
    skipped = rep.get("skipped_self") or []
    if skipped:
        p = skipped[-1]
        return (f"已跳过屏幕上的「模板图片本身」（{p['cx']},{p['cy']}），"
                f"改点真正显示的那处")
    return ""


def locate_on_screen(path: str | None, confidence: float = 90,
                     screen=None,
                     region: tuple[int, int, int, int] | None = None
                     ) -> tuple[int, int] | None:
    """在屏幕（物理像素）上找图，返回中心坐标

    path       模板图片路径
    confidence 相似度阈值 0-100（默认 90；任务栏图标建议 85 左右更稳）
    screen     可选，已截好的 PIL.Image（一次操作里有多个找图步骤时复用）
    region     可选，(x, y, 宽, 高) 只在这个矩形里找；None = 全屏。
               任务栏、某个窗口内部这类「全屏会撞上同款图标」的场景，
               限定区域后桌面上的同名图标根本不会进入候选。
    """
    LAST_REPORT.clear()
    LAST_REPORT["path"] = path
    LAST_REPORT["confidence"] = confidence

    if not path or not os.path.exists(path):
        LAST_REPORT["error"] = "图片不存在"
        return None
    try:
        tmpl_img = load_template(path)
    except Exception as exc:                       # noqa: BLE001
        LAST_REPORT["error"] = f"图片读不出来：{exc}"
        return None

    shot = screen
    if shot is None:
        shot = grab_screen()
    if shot is None:
        LAST_REPORT["error"] = "抓屏失败"
        return None

    # 限定搜索区域：先裁图，找到的坐标最后再加回偏移
    off_x = off_y = 0
    if region:
        try:
            rx, ry, rw, rh = (int(v) for v in region)
            sw, sh = shot.size
            x0, y0 = max(0, min(rx, sw - 1)), max(0, min(ry, sh - 1))
            x1, y1 = max(x0 + 1, min(rx + max(1, rw), sw)), \
                max(y0 + 1, min(ry + max(1, rh), sh))
            if x1 - x0 < 4 or y1 - y0 < 4:
                LAST_REPORT["error"] = "搜索区域太小"
                return None
            shot = shot.crop((x0, y0, x1, y1))
            off_x, off_y = x0, y0
            LAST_REPORT["region"] = [x0, y0, x1 - x0, y1 - y0]
        except Exception as exc:                   # noqa: BLE001
            LAST_REPORT["error"] = f"搜索区域无效：{exc}"
            return None

    if tmpl_img.width > shot.width or tmpl_img.height > shot.height:
        LAST_REPORT["error"] = (f"模板 {tmpl_img.width}×{tmpl_img.height} 比搜索区域 "
                                f"{shot.width}×{shot.height} 还大，请把区域放大")

        return None

    try:
        img = _to_gray(np.asarray(shot))
        tmpl = _to_gray(np.asarray(tmpl_img))
    except Exception as exc:                       # noqa: BLE001
        LAST_REPORT["error"] = f"图像转换失败：{exc}"
        return None

    debug: dict = {}
    try:
        res = match_gray(img, tmpl, debug=debug, origin=(off_x, off_y))
    except Exception as exc:                       # noqa: BLE001
        LAST_REPORT["error"] = f"匹配异常：{exc}"
        return None
    LAST_REPORT.update(debug)
    if not res:
        return None

    best, x, y = res
    threshold = max(0.1, min(1.0, float(confidence or 90) / 100.0))
    LAST_REPORT["score"] = round(best, 4)
    if best < threshold:
        LAST_REPORT["error"] = "相似度不足"
        return None
    h, w = tmpl.shape
    return x + off_x + w // 2, y + off_y + h // 2
