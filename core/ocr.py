"""ocr.py —— 验证码识别（对接本机 Umi-OCR 的 HTTP 服务）

Umi-OCR 打开「设置 → HTTP 服务」后默认监听 1224 端口：

    POST http://127.0.0.1:1224/api/ocr
    {"base64": "<png 的 base64>", "options": {"data.format": "text"}}
    → {"code": 100, "data": "B9DL", "time": 0.02}

不引入新依赖：截图用已有的 PIL，请求用标准库 urllib。
服务没启动时给出人话提示，而不是抛一堆 URLError。
"""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:1224/api/ocr"
DEFAULT_TIMEOUT = 8.0
UPSCALE_BELOW = 64          # 高度不足这个就放大一点，验证码小字更准

LAST_ERROR = ""             # 最近一次失败原因（UI / 日志用）


def normalize_endpoint(endpoint: str | None) -> str:
    """允许用户只填 127.0.0.1:1224，甚至整个地址都不填"""
    ep = (endpoint or "").strip()
    if not ep:
        return DEFAULT_ENDPOINT
    if "://" not in ep:
        ep = "http://" + ep
    ep = ep.rstrip("/")
    if not ep.lower().endswith("/api/ocr"):
        ep += "/api/ocr"
    return ep


def clean_code(text: str) -> str:
    """把 OCR 的原始结果收拾成能直接填进输入框的验证码

    去空白、取最长一行；优先只留字母数字（验证码基本都是这个形态），
    一个字母数字都没有时退回原文，免得把中文验证码吃光。
    """
    if not text:
        return ""
    raw = str(text).replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return ""
    line = max(lines, key=len)
    alnum = re.sub(r"[^0-9A-Za-z]", "", line)
    return alnum or re.sub(r"\s+", "", line)


def grab_region(region: tuple[int, int, int, int] | None,
                screen=None):
    """按屏幕矩形截图；region 为空 / 越界时自动收敛到屏幕内"""
    from . import image_match

    shot = screen
    if shot is None:
        shot = image_match.grab_screen()
    if shot is None:
        return None
    if not region:
        return shot
    try:
        x, y, w, h = (int(v) for v in region)
    except Exception:
        return None
    sw, sh = shot.size
    x0, y0 = max(0, min(x, sw - 1)), max(0, min(y, sh - 1))
    x1 = max(x0 + 1, min(x + max(1, w), sw))
    y1 = max(y0 + 1, min(y + max(1, h), sh))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return shot.crop((x0, y0, x1, y1))


def recognize(img, endpoint: str | None = "",
              timeout: float = DEFAULT_TIMEOUT,
              upscale: bool = True) -> tuple[str, str]:
    """识别一张图 → (文本, 错误说明)；成功时错误说明为空串"""
    global LAST_ERROR
    if img is None:
        LAST_ERROR = "没有截到图像"
        return "", LAST_ERROR

    try:
        from PIL import Image

        im = img
        if upscale and im.height and im.height < UPSCALE_BELOW:
            k = 2.0 if im.height < 32 else 1.5
            im = im.resize((max(1, int(im.width * k)),
                            max(1, int(im.height * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        payload = json.dumps({
            "base64": base64.b64encode(buf.getvalue()).decode("ascii"),
            "options": {"data.format": "text"},
        }).encode("utf-8")
    except Exception as exc:                       # noqa: BLE001
        LAST_ERROR = f"图片编码失败：{exc}"
        return "", LAST_ERROR

    url = normalize_endpoint(endpoint)
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        LAST_ERROR = (f"连不上 OCR 服务 {url}\n"
                      f"请先启动 Umi-OCR，并在「设置 → HTTP 服务」里打开服务"
                      f"（默认端口 1224）\n({getattr(exc, 'reason', exc)})")
        return "", LAST_ERROR
    except Exception as exc:                       # noqa: BLE001
        LAST_ERROR = f"请求 OCR 服务失败：{exc}"
        return "", LAST_ERROR

    try:
        data = json.loads(raw)
    except Exception:                              # noqa: BLE001
        LAST_ERROR = f"OCR 返回的不是 JSON：{raw[:120]}"
        return "", LAST_ERROR

    try:
        code = int(data.get("code", 0))
    except Exception:                              # noqa: BLE001
        code = 0
    if code != 100:
        LAST_ERROR = f"OCR 识别失败：{data.get('data') or raw[:120]}"
        return "", LAST_ERROR

    LAST_ERROR = ""
    return str(data.get("data") or ""), ""


def recognize_region(region: tuple[int, int, int, int] | None,
                     endpoint: str | None = "",
                     timeout: float = DEFAULT_TIMEOUT,
                     screen=None) -> tuple[str, str]:
    """截屏 + 识别 + 清洗，返回可直接输入的验证码"""
    img = grab_region(region, screen=screen)
    if img is None:
        LAST_ERROR = "识别范围不在屏幕内（或太小）"
        return "", LAST_ERROR
    text, err = recognize(img, endpoint=endpoint, timeout=timeout)
    if err:
        return "", err
    code_text = clean_code(text)
    if not code_text:
        LAST_ERROR = f"没识别出内容（OCR 原文：{text!r}）"
        return "", LAST_ERROR
    return code_text, ""


def available(endpoint: str | None = "", timeout: float = 2.0) -> bool:
    """OCR 服务在不在（插入指令时给个提示用）"""
    url = normalize_endpoint(endpoint)
    data = json.dumps({"base64": "", "options": {}}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except urllib.error.HTTPError:
        return True                                # 有响应就说明服务活着
    except Exception:
        return False
