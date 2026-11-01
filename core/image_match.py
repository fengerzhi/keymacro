"""image_match.py —— 内置模板匹配（纯 numpy，不依赖 opencv / pyautogui）

为什么自己实现：
    opencv 打包后 90 MB 上下，pyautogui 还要求用户额外装依赖才能找图，
    对「点任务栏图标」这类小模板来说太重。这里用归一化互相关（NCC）实现，
    numpy 打包只有 20 MB 左右，精度足够。

算法：
    1. 屏幕截图与模板转灰度；
    2. 模板去均值后用 FFT 求互相关（分子）；
    3. 用积分图求每个窗口的和与平方和，得到局部方差（分母）；
    4. score = 分子 / sqrt(窗口方差 * 模板方差)，取最大值与阈值比较。

坐标约定：全部是**物理像素**。程序已声明 DPI 感知，录制、回放、
PIL 截图三者的坐标系一致，无需按缩放比例换算。
"""
from __future__ import annotations

import os

import numpy as np

__all__ = [
    "engine_available",
    "grab_screen",
    "load_template",
    "locate_on_screen",
    "match_gray",
]


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


def _ncc_full(img: np.ndarray, tmpl: np.ndarray) -> tuple[float, int, int] | None:
    """全图归一化互相关，返回 (相似度, 左上角 x, 左上角 y)"""
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W:
        return None

    t = tmpl - float(tmpl.mean())
    var_t = float((t * t).sum())
    if var_t <= 1e-9:
        return None

    # 频域互相关（模板翻转后做卷积 = 互相关）
    # 必须用 float64：2560×1600 的画面用 float32 会累积误差，
    # 让互相关出现假峰（实测会把不相干的位置判成最高分）。
    fi = np.fft.rfft2(img.astype(np.float64), s=(H, W))
    ft = np.fft.rfft2(t[::-1, ::-1].astype(np.float64), s=(H, W))
    corr = np.fft.irfft2(fi * ft, s=(H, W))
    num = corr[h - 1:, w - 1:]

    n = h * w
    s1 = _window_sums(_prefix_sum(img), h, w)
    s2 = _window_sums(_prefix_sum(img * img), h, w)
    var_w = s2 - s1 * s1 / n
    np.maximum(var_w, 0.0, out=var_w)

    denom = np.sqrt(var_w * var_t) + 1e-9
    score = num / denom

    # 平坦区域必须剔掉：窗口方差趋近 0 时 NCC 的分母也趋近 0，
    # 分数会算成天文数字（实测出现过 2.9e10），把纯色背景误判成命中。
    std_w = np.sqrt(var_w / n)
    std_t = np.sqrt(var_t / n)
    score[std_w < max(0.8, 0.15 * std_t)] = -1.0

    idx = int(np.argmax(score))
    best = float(score.reshape(-1)[idx])
    y, x = divmod(idx, score.shape[1])
    if best < -0.5:
        return None
    return min(best, 1.0), x, y


def _ncc_local(img: np.ndarray, tmpl: np.ndarray,
               cx: int, cy: int, radius: int = 6):
    """在 (cx, cy) 附近逐像素做精确比对，用于降采样后的修正"""
    H, W = img.shape
    h, w = tmpl.shape
    t = tmpl - float(tmpl.mean())
    var_t = float((t * t).sum())
    if var_t <= 1e-9:
        return None
    x0, x1 = max(0, cx - radius), min(W - w, cx + radius)
    y0, y1 = max(0, cy - radius), min(H - h, cy + radius)
    if x1 < x0 or y1 < y0:
        return None
    best = None
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            win = img[y:y + h, x:x + w]
            a = win - win.mean()
            var_a = float((a * a).sum())
            if var_a <= 1e-9:
                continue
            s = float((a * t).sum()) / (float(np.sqrt(var_a * var_t)) + 1e-9)
            if best is None or s > best[0]:
                best = (min(s, 1.0), x, y)
    return best


def match_gray(img: np.ndarray, tmpl: np.ndarray) -> tuple[float, int, int] | None:
    """在灰度图里找模板，返回 (相似度, 左上角 x, 左上角 y)

    相似度是归一化互相关系数（-1 ~ 1，越接近 1 越像）。
    大图走「降采样粗搜 + 原图精修」，避免每次找图都花 1 秒以上。
    """
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W or h < 2 or w < 2:
        return None

    # 纯色模板没有可匹配特征，直接判定失败（这种图截了也用不了）
    if float(tmpl.std()) < 1.0:
        return None

    if H * W > 1_200_000 and h >= 16 and w >= 16 and h < H // 2 and w < W // 2:
        small = _ncc_full(img[::2, ::2], tmpl[::2, ::2])
        if small is None:
            return None
        s, cx, cy = small
        refined = _ncc_local(img, tmpl, cx * 2, cy * 2, radius=6)
        if refined and refined[0] >= s - 0.02:
            return refined
        return s, cx * 2, cy * 2
    return _ncc_full(img, tmpl)


def locate_on_screen(path: str | None, confidence: float = 90,
                     screen=None) -> tuple[int, int] | None:
    """在屏幕（物理像素）上找图，返回中心坐标

    path       模板图片路径
    confidence 相似度阈值 0-100（默认 90；任务栏图标建议 85 左右更稳）
    screen     可选，已截好的 PIL.Image（一次操作里有多个找图步骤时复用）
    """
    if not path or not os.path.exists(path):
        return None
    try:
        tmpl_img = load_template(path)
    except Exception:
        return None

    shot = screen
    if shot is None:
        shot = grab_screen()
    if shot is None:
        return None

    try:
        img = _to_gray(np.asarray(shot))
        tmpl = _to_gray(np.asarray(tmpl_img))
    except Exception:
        return None

    try:
        res = match_gray(img, tmpl)
    except Exception:
        return None
    if not res:
        return None

    best, x, y = res
    threshold = max(0.1, min(1.0, float(confidence or 90) / 100.0))
    if best < threshold:
        return None
    h, w = tmpl.shape
    return x + w // 2, y + h // 2
