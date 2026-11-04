"""script_model.py —— 脚本数据模型与序列化

事件统一格式：
    { "t": 相对毫秒, "type": "...", ... }

事件类型一览
─────────────────────────────────────────────────────────
鼠标
    mousemove   移动              x, y
    mousedown   按下              x, y, button(left/right/middle)
    mouseup     释放              x, y, button
    click       单击              x, y, button
    dblclick    双击              x, y, button
    drag        拖拽              x, y → x2, y2, button
    scroll      滚轮              x, y, dx, dy
键盘
    keydown     按下单键           key
    keyup       释放单键           key
    keytype     输入一段文本        text
    hotkey      组合键            keys=["ctrl","c"]（按下逆序释放）
语义化编辑指令（edit）
    select_all      全选          Ctrl+A
    copy            复制          Ctrl+C
    cut             剪切          Ctrl+X
    paste           粘贴          Ctrl+V
    delete          删除          Delete
    backspace       退格          Backspace
    undo            撤销          Ctrl+Z
    redo            重做          Ctrl+Y
    enter           回车          Enter
    esc             取消          Esc
    tab             切换焦点       Tab
    right_click     右键（调出菜单） x, y
控制流
    delay       等待              dx=毫秒
    loop_start  循环开始           text=次数 / "data"=按数据行 / "forever"
    loop_end    循环结束
    if          如果（条件块）      cond=image|var, left, op, right
    else        否则（可省略）
    if_end      结束判断
    break       跳出循环（最内层）
    if_image    条件：屏幕存在某图  text=图片路径, dx=超时毫秒
                                  （不满足则跳过本段剩余步骤）
    comment     注释（不执行）      text
图像识别
    find_image  找图并点击          text=图片路径, dx=相似度(0-100), note=找不到时行为
                rx/ry/rw/rh=只在这个矩形里找（可省略 = 全屏）
    ocr         识别验证码并填写     rx/ry/rw/rh=验证码图的屏幕区域（必填）
                                   x/y=输入框位置（可空：输入到当前焦点）
                                   text=OCR 服务地址（空 = 127.0.0.1:1224）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

SCRIPT_VERSION = 3

# ---------------------------------------------------------------- 语义指令表
# 语义化编辑指令 → 实际按键序列（供回放器和 UI 共用）
EDIT_ACTIONS: dict[str, dict[str, Any]] = {
    "select_all": {"label": "全选",        "hotkey": ["ctrl", "a"]},
    "copy":       {"label": "复制",        "hotkey": ["ctrl", "c"]},
    "cut":        {"label": "剪切",        "hotkey": ["ctrl", "x"]},
    "paste":      {"label": "粘贴",        "hotkey": ["ctrl", "v"]},
    "undo":       {"label": "撤销",        "hotkey": ["ctrl", "z"]},
    "redo":       {"label": "重做",        "hotkey": ["ctrl", "y"]},
    "delete":     {"label": "删除",        "hotkey": ["delete"]},
    "backspace":  {"label": "退格",        "hotkey": ["backspace"]},
    "enter":      {"label": "回车",        "hotkey": ["enter"]},
    "esc":        {"label": "取消",        "hotkey": ["esc"]},
    "tab":        {"label": "切换焦点",     "hotkey": ["tab"]},
    "right_click": {"label": "右键菜单",    "hotkey": []},
}

# macOS 上 Ctrl 系列需替换为 Cmd
MAC_EDIT_MAP = {
    "select_all": ["cmd", "a"], "copy": ["cmd", "c"], "cut": ["cmd", "x"],
    "paste": ["cmd", "v"], "undo": ["cmd", "z"], "redo": ["cmd", "shift", "z"],
}


@dataclass
class Event:
    """单条脚本事件"""
    t: int                              # 相对开始的毫秒数
    type: str
    # 鼠标
    x: int | None = None
    y: int | None = None
    x2: int | None = None               # drag 目标点
    y2: int | None = None
    button: str | None = None           # left / right / middle
    pressed: bool | None = None
    dx: int | None = None               # scroll 水平量 / delay 毫秒 / 相似度
    dy: int | None = None               # scroll 垂直量
    # 键盘
    key: str | None = None
    keys: list[str] | None = None       # hotkey 组合键
    text: str | None = None             # keytype 文本 / 图片路径 / 循环次数 / 注释
    # 控制流条件（if 事件用）
    cond: str | None = None              # image | var
    left: str | None = None              # 变量表达 "{{金额}}" 或图片路径
    op: str | None = None                # eq/ne/contains/gt/lt/img_hit/img_miss
    right: str | None = None             # 比较值
    # 元信息
    note: str | None = None
    valid: bool | None = None
    enabled: bool = True                # 可临时禁用某步（不执行）
    # 找图搜索区域（物理像素，左上角 + 宽高）；None = 全屏
    rx: int | None = None
    ry: int | None = None
    rw: int | None = None
    rh: int | None = None

    def region(self) -> tuple[int, int, int, int] | None:
        """找图区域 (x, y, 宽, 高)；四项不齐/宽高非正 → None（按全屏）"""
        if None in (self.rx, self.ry, self.rw, self.rh):
            return None
        w, h = int(self.rw or 0), int(self.rh or 0)
        if w <= 0 or h <= 0:
            return None
        return int(self.rx or 0), int(self.ry or 0), w, h

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        """设置/清除找图区域"""
        if region is None:
            self.rx = self.ry = self.rw = self.rh = None
            return
        self.rx, self.ry, self.rw, self.rh = (int(v) for v in region)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        out = {k: v for k, v in d.items() if v is not None}
        if out.get("enabled") is True:
            out.pop("enabled")          # 默认值不落盘，保持文件干净
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in allowed})

    # ------------------------------------------------------------ 描述
    OP_LABELS = {
        "eq": "=", "ne": "≠", "contains": "包含", "gt": ">", "lt": "<",
        "img_hit": "存在", "img_miss": "不存在",
    }

    def cond_text(self) -> str:
        """条件摘要，供列表显示"""
        if (self.cond or "") == "image":
            name = (self.left or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            word = "不存在" if self.op == "img_miss" else "存在"
            return f"屏幕{word}[{name or '未选图'}]"
        op = self.OP_LABELS.get(self.op or "eq", self.op or "=")
        if op in ("存在", "不存在"):
            return f"{self.left or ''} {op}"
        return f"{self.left or ''} {op} {self.right or ''}"

    def detail(self) -> str:
        """动作描述（不含时间前缀），供列表/日志使用"""
        ty = self.type
        prefix = "" if self.enabled else "[已禁用] "

        if ty == "mousemove":
            return f"移动 → ({self.x}, {self.y})"
        if ty in ("mousedown", "mouseup"):
            act = "按下" if ty == "mousedown" else "释放"
            btn = {"left": "左键", "right": "右键", "middle": "中键"}.get(
                self.button or "left", self.button)
            return f"{act}{btn} @ ({self.x}, {self.y})"
        if ty == "click":
            btn = {"left": "左键", "right": "右键", "middle": "中键"}.get(
                self.button or "left", self.button)
            return f"单击{btn} @ ({self.x}, {self.y})"
        if ty == "dblclick":
            return f"双击 @ ({self.x}, {self.y})"
        if ty == "drag":
            return f"拖拽 ({self.x}, {self.y}) → ({self.x2}, {self.y2})"
        if ty == "scroll":
            return f"滚动 ({self.dx or 0}, {self.dy or 0}) @ ({self.x}, {self.y})"
        if ty == "keydown":
            return f"按键↓ {self.key}"
        if ty == "keyup":
            return f"按键↑ {self.key}"
        if ty == "keytype":
            preview = (self.text or "").replace("\n", "⏎")[:26]
            return f'输入 "{preview}"'
        if ty == "hotkey":
            combo = "+".join(k.upper() if len(k) == 1 else k.capitalize()
                             for k in (self.keys or []))
            return f"{prefix}组合键 {combo}"
        if ty == "edit":
            info = EDIT_ACTIONS.get(self.key or "", {})
            label = info.get("label", self.key)
            pos = f" @ ({self.x}, {self.y})" if self.x is not None else ""
            return f"{prefix}【{label}】{pos}"
        if ty == "delay":
            return f"等待 {self.dx or 0} ms"
        if ty == "loop_start":
            times = self.text or "forever"
            if times == "data":
                times = "按数据行"
            return f"┌ 循环开始 × {times}"
        if ty == "loop_end":
            return "└ 循环结束"
        if ty == "if":
            return f"◇ 如果 {self.cond_text()}"
        if ty == "else":
            return "◇ 否则"
        if ty == "if_end":
            return "◇ 结束判断"
        if ty == "break":
            return "✖ 跳出循环"
        if ty == "if_image":
            name = (self.text or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            return f"? 如果屏幕有 [{name}] 则继续"
        if ty == "find_image":
            name = (self.text or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            text = f"{prefix}找图点击 [{name}] 相似度{self.dx or 90}%"
            r = self.region()
            if r:
                text += f" 区域({r[0]},{r[1]},{r[2]}×{r[3]})"
            return text

        if ty == "ocr":
            r = self.region()
            text = f"{prefix}验证码识别"
            if r:
                text += f" 区域({r[0]},{r[1]},{r[2]}×{r[3]})"
            if self.x is not None and self.y is not None:
                text += f" → 填入({self.x},{self.y})"
            else:
                text += " → 输入到当前焦点"
            return text
        if ty == "comment":
            return f"# {(self.text or '')[:30]}"
        return ty

    def describe(self) -> str:
        t = f"{self.t / 1000:7.2f}s"
        return f"{t}  {self.detail()}"


@dataclass
class Script:
    """一份完整脚本"""
    name: str = "未命名脚本"
    events: list[Event] = field(default_factory=list)
    created_at: str = ""
    loop: int = 1
    speed: float = 1.0
    # 防封与容错
    jitter_ms: int = 0                  # 每步随机延时抖动上限（毫秒）
    retry: int = 0                      # 找图失败重试次数
    # 坐标校准（应对 DPI 缩放/远程桌面等环境的残余偏移）
    coord_scale: float = 1.0            # 坐标缩放系数，1.0 = 不调整
    coord_offset_x: int = 0             # 坐标固定偏移
    coord_offset_y: int = 0
    # 数据源（Excel/CSV，驱动 {{变量}} 替换）
    data_file: str = ""                 # 数据文件路径（xlsx/csv/tsv）
    data_mode: str = "off"              # off | per_loop | fixed
    data_start_row: int = 2             # 数据起始行（Excel 行号，1=表头）
    data_fixed_row: int = 2             # fixed 模式下固定取的行
    version: int = SCRIPT_VERSION

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "createdAt": self.created_at,
            "loop": self.loop,
            "speed": self.speed,
            "jitter_ms": self.jitter_ms,
            "retry": self.retry,
            "coord_scale": self.coord_scale,
            "coord_offset_x": self.coord_offset_x,
            "coord_offset_y": self.coord_offset_y,
            "data_file": self.data_file,
            "data_mode": self.data_mode,
            "data_start_row": self.data_start_row,
            "data_fixed_row": self.data_fixed_row,
            "events": [e.to_dict() for e in self.events],
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Script":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, data: dict) -> "Script":
        return cls(
            name=data.get("name", "未命名脚本"),
            events=[Event.from_dict(e) for e in data.get("events", [])],
            created_at=data.get("createdAt", ""),
            loop=int(data.get("loop", 1)),
            speed=float(data.get("speed", 1.0)),
            jitter_ms=int(data.get("jitter_ms", 0)),
            retry=int(data.get("retry", 0)),
            coord_scale=float(data.get("coord_scale", 1.0)),
            coord_offset_x=int(data.get("coord_offset_x", 0)),
            coord_offset_y=int(data.get("coord_offset_y", 0)),
            data_file=data.get("data_file", ""),
            data_mode=data.get("data_mode", "off"),
            data_start_row=int(data.get("data_start_row", 2)),
            data_fixed_row=int(data.get("data_fixed_row", 2)),
            version=int(data.get("version", SCRIPT_VERSION)),
        )

    def calibrate(self, x: int | None, y: int | None) -> tuple[int | None, int | None]:
        """把脚本记录的坐标换算成实际注入坐标

        默认原样返回（scale=1.0, offset=0），只有用户显式校准过才生效。
        换算公式：实际 = 记录值 × 缩放 + 偏移
        """
        if x is None or y is None:
            return x, y
        if self.coord_scale == 1.0 and not self.coord_offset_x and not self.coord_offset_y:
            return x, y
        nx = int(round(x * self.coord_scale)) + self.coord_offset_x
        ny = int(round(y * self.coord_scale)) + self.coord_offset_y
        return nx, ny

    @property
    def has_calibration(self) -> bool:
        return (self.coord_scale != 1.0
                or self.coord_offset_x != 0
                or self.coord_offset_y != 0)

    # -------------------------------------------------- 数据源
    def use_data(self) -> bool:
        return bool(self.data_file) and self.data_mode in ("per_loop", "fixed")

    def data_row_for_lap(self, lap: int, wrap: bool = True) -> int:
        """第 lap 轮（1 起）对应的数据行（0 起索引，供 DataTable.row_dict）。

        per_loop：从 data_start_row 起，每轮 +1。
          · wrap=True（默认）超出后回到第一行循环
          · wrap=False 超出后返回 -1（用于「按数据行数循环」，数据用完即停）
        fixed：固定 data_fixed_row 行。
        """
        from .data_source import load_table
        # Excel 行号 → 数据索引：第 2 行 = 第 1 条数据（第 1 行是表头）
        if self.data_mode == "fixed":
            return max(0, self.data_fixed_row - 2)
        n = load_table(self.data_file).n_rows
        if n <= 0:
            return 0
        first = max(2, self.data_start_row) - 2        # 0 起索引
        idx = first + lap - 1
        if idx >= n:
            return (idx % n) if wrap else -1
        return idx

    def values_for_lap(self, lap: int, table=None, wrap: bool = True) -> dict[str, str]:
        """第 lap 轮的 {列名: 值}，供 {{变量}} 替换"""
        if not self.use_data():
            return {}
        if table is None:
            from .data_source import load_table
            table = load_table(self.data_file)
        idx = self.data_row_for_lap(lap, wrap=wrap)
        if idx < 0:
            return {}                       # 数据用尽：变量留空
        vals = table.row_dict(idx)
        vals["__row__"] = str(idx + 1)
        return vals

    # -------------------------------------------------- 统计
    @property
    def duration_ms(self) -> int:
        return max((e.t for e in self.events), default=0)

    @property
    def click_count(self) -> int:
        return sum(1 for e in self.events
                   if e.type in ("click", "mouseup", "dblclick", "find_image")
                   or (e.type == "edit" and e.key == "right_click"))

    @property
    def active_events(self) -> list[Event]:
        return [e for e in self.events if e.enabled]

    # -------------------------------------------------- 编辑
    def remove(self, index: int) -> None:
        if 0 <= index < len(self.events):
            del self.events[index]

    def insert_sorted(self, ev: Event) -> int:
        """按时间插入并返回新索引"""
        self.events.append(ev)
        self.events.sort(key=lambda e: e.t)
        return self.events.index(ev)

    def retime(self) -> None:
        """保证时间轴单调不减"""
        for i in range(1, len(self.events)):
            if self.events[i].t < self.events[i - 1].t:
                self.events[i].t = self.events[i - 1].t

    def validate_structure(self) -> list[str]:
        """检查 loop / if 块配对等结构问题，返回警告列表"""
        warnings: list[str] = []
        stack: list[tuple[str, int]] = []      # (kind, 行号) kind: loop|if|else
        for i, e in enumerate(self.events):
            row = i + 1
            if e.type == "loop_start":
                stack.append(("loop", row))
            elif e.type == "loop_end":
                if stack and stack[-1][0] == "loop":
                    stack.pop()
                elif any(k == "loop" for k, _ in stack):
                    warnings.append(f"第 {row} 行「循环结束」与「如果」交叉嵌套")
                    while stack and stack[-1][0] != "loop":
                        stack.pop()
                    stack.pop()
                else:
                    warnings.append(f"第 {row} 行出现多余的「循环结束」")
            elif e.type == "if":
                stack.append(("if", row))
            elif e.type == "else":
                if not stack or stack[-1][0] != "if":
                    warnings.append(f"第 {row} 行「否则」前面没有对应的「如果」")
                else:
                    kind, start = stack.pop()
                    stack.append(("else", start))
            elif e.type == "if_end":
                if stack and stack[-1][0] in ("if", "else"):
                    stack.pop()
                else:
                    warnings.append(f"第 {row} 行「结束判断」没有对应的「如果」")
            elif e.type == "break":
                if not any(k == "loop" for k, _ in stack):
                    warnings.append(f"第 {row} 行「跳出循环」不在任何循环内")

        for kind, row in stack:
            if kind == "loop":
                warnings.append(f"第 {row} 行的「循环开始」缺少对应的「循环结束」")
            else:
                warnings.append(f"第 {row} 行的「如果」缺少对应的「结束判断」")
        return warnings


def build_hotkey(keys: list[str]) -> Event:
    """构造组合键事件的便捷函数（供 UI 指令面板使用）"""
    return Event(t=0, type="hotkey", keys=list(keys))


def build_edit(action: str, x: int | None = None, y: int | None = None,
               t: int = 0) -> Event:
    """构造语义化编辑指令事件"""
    if action not in EDIT_ACTIONS:
        raise ValueError(f"未知编辑指令: {action}")
    if action == "right_click":
        return Event(t=t, type="edit", key=action, x=x, y=y, button="right")
    return Event(t=t, type="edit", key=action)
