"""状态条（血条 / 蓝条）识别。

原理：条状 UI 是纯色填充，用 HSV 阈值把目标颜色的像素抠出来，
再数"有多少列存在目标像素"，除以满值宽度就是百分比。

为什么按列（axis=0）而不是直接数像素：
    直接数像素会被条本身的高度变化、上下描边、抗锯齿边缘干扰；
    按列取"这一列有没有目标色"相当于把条压成一条一维线，
    只要列上有任意一个像素命中就算这一列是满的，
    对高度抖动和细描边鲁棒得多。

满值宽度怎么来：
    ROI 框选的是"整条槽"（含空槽），而满血时只有一部分被填充。
    所以需要一个 span = [left, right] 表示填充区在 ROI 内的横向范围。
    没标定时退化为"整个 ROI 宽度"。

本文件从 maplestory-bot 移植而来，改造点：
    1. 不依赖 maplestory-bot 的 Config 类，改用普通 dict 工厂方法 from_dict；
    2. BarSpan / BarResult 直接定义在本文件里，避免引入外部包结构；
    3. 日志用本项目的 src.utils.logger，不用 logging.getLogger。

────────────────────────────────────────────────────────────────────
移植自 Alan-xzwzz 的 PR #3（MapleStoryAutoLevelUp-CN），MIT License。
本项目（冒险岛自动练级-公开版）在此基础上做了如下改造：
    1. validate() 的 span 边界由严格 `<` 改为 `<=` ——
       满血时填充区可以正好铺满整个 ROI（span=[0, roi_width]），
       原判据会把这个**合法**写法判成越界抛 ValueError。
    2. validate() 接进 from_dict —— 原实现里它没有任何调用方，
       ROI 配错只会静默返回无效帧（表现是"读数一直不可信"），
       接进来之后配错会在加载期就报出来。
    3. span 由 config 显式提供（1366x768 实测满值宽 165px），
       不再依赖标定工具写入 —— 详见 config_default.yaml 的 bars 段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import cv2
import numpy as np

from src.utils.logger import logger


# ============================================================
# 数据类型（从 maplestory-bot src/base/types.py 抄过来，本文件自包含）
# ============================================================

class BarSpan(NamedTuple):
    """满值填充区在 ROI 内的横向范围（相对 ROI 左边，闭区间 [left, right]）。"""

    left: int
    right: int

    @property
    def width(self) -> int:
        return self.right - self.left + 1


class BarResult(NamedTuple):
    """一次检测的原始结果。"""

    percent: float | None
    """百分比 0~100；None 表示这一帧无效（闪烁 / 读不到）。"""

    filled_px: int
    """命中的列数。"""

    full_px: int
    """满值对应的列数。"""

    valid: bool
    """这一帧是否可信。"""

    reason: str = ""
    """无效原因，便于调试。"""


# ============================================================
# 配置 & 检测器
# ============================================================

@dataclass
class BarConfig:
    """单条状态条的配置。"""

    name: str
    roi: tuple[int, int, int, int] | None
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    span: BarSpan | None = None
    min_pixels: int = 4

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "BarConfig":
        """从普通 dict 构建配置。

        d 对应配置里 cfg["bars"]["hp"] / cfg["bars"]["mp"] 这个子 dict。
        roi / span 为 None 时合法（未标定），不抛异常；
        hsv_lower / hsv_upper 缺失才抛 ValueError。

        ⚠️★ 2026-09-27 改（本项目改造点 2）：构造完立刻调 `validate()`。
            原实现里 `validate()` **一个调用方都没有** —— 于是 ROI 配错
            （越界 / 空矩形 / span 超宽）时什么都不报，detect() 只会
            "静默地一直返回不可信"，表现是「血蓝读数永远是未知、药一次不喝」，
            而且日志里干干净净，极难查。
            现在把它接在这里：配错在**加载配置那一刻**就抛出来，
            配合引擎层的 try 直接变成一条看得懂的报错。
        """
        roi_raw = d.get("roi")
        roi = tuple(int(v) for v in roi_raw) if roi_raw else None

        span_raw = d.get("span")
        span = None
        if span_raw and len(span_raw) == 2:
            span = BarSpan(int(span_raw[0]), int(span_raw[1]))

        hsv_lower = d.get("hsv_lower")
        hsv_upper = d.get("hsv_upper")
        if hsv_lower is None or hsv_upper is None:
            raise ValueError(
                f"{name}缺少 hsv_lower / hsv_upper，无法构建颜色掩码。"
            )

        cfg = cls(
            name=name,
            roi=roi,
            hsv_lower=tuple(int(v) for v in hsv_lower),
            hsv_upper=tuple(int(v) for v in hsv_upper),
            span=span,
            min_pixels=int(d.get("min_pixels", 4)),
        )
        # 只做"不需要画面尺寸"的那部分校验（span 自洽）；画面范围留到 validate(frame_shape)
        cfg.validate()
        return cfg

    def validate(self, frame_shape: tuple[int, int, int] | None = None) -> None:
        """检查 ROI / span 是否合法。不合法抛 ValueError。

        frame_shape 为 None 时只做「不需要画面尺寸」的检查（即 span 自洽性）——
        这样 from_dict 在建配置的那一刻就能把 span 配错拦下来，不必等到有帧。
        """
        if self.roi is None:
            raise ValueError(
                f"{self.name}还没有标定 ROI。请检查 config_default.yaml 的 "
                f"bars.{self.name} 段（本项目出厂已配好 1366x768 的值）。"
            )
        x1, y1, x2, y2 = self.roi
        if not (0 < x2 - x1 and 0 < y2 - y1):
            raise ValueError(
                f"{self.name}的 ROI {self.roi} 是空矩形（右下角必须大于左上角）。"
            )
        if frame_shape is not None:
            height, width = frame_shape[:2]
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError(
                    f"{self.name}的 ROI {self.roi} 超出画面范围 "
                    f"{width}x{height}。游戏分辨率或窗口大小变了，需要重新标定。"
                )
        if self.span is not None:
            roi_width = x2 - x1
            # ⚠️★ 2026-09-27 改（本项目改造点 1）：右边是**闭区间**，必须用 `<=`。
            #    原文写的是 `self.span.right < roi_width`（严格小于），它会拒绝
            #    span=[0, roi_width] 这个**完全合法**的写法 —— 而"填充区正好铺满
            #    整个 ROI"恰恰是精确标定后的常见结果（1366x768 实测 span=[0,164]，
            #    roi_width=165，right=164 < 165 侥幸能过；但只要把 ROI 框得刚好
            #    贴住填充区，right 就等于 roi_width，立刻抛异常）。
            #    BarSpan.width 的定义是 right - left + 1（闭区间），语义上 right
            #    取到 roi_width 就是"最后一列也算"，没有任何越界。
            if not (0 <= self.span.left <= self.span.right <= roi_width):
                raise ValueError(
                    f"{self.name}的 span {tuple(self.span)} 超出 ROI 宽度 {roi_width}。"
                )
            if self.span.width <= 0:
                raise ValueError(
                    f"{self.name}的 span {tuple(self.span)} 是空区间（left > right）。"
                )


class BarDetector:
    """单条状态条的检测器。

    无状态：同一个实例可以反复调用 detect()，也可以跨条复用。
    """

    def __init__(self, config: BarConfig):
        self.config = config
        self._lower = np.array(config.hsv_lower, dtype=np.uint8)
        self._upper = np.array(config.hsv_upper, dtype=np.uint8)

    def validate(self, frame_shape: tuple[int, int, int] | None = None) -> None:
        """检查 ROI / span 是否合法。不合法抛 ValueError。

        转发到 BarConfig，这样单个条和组合检测器的调用方式保持一致。
        frame_shape 可省略（只做 span 自洽性检查）。
        """
        self.config.validate(frame_shape)

    # ---------- 主流程 ----------

    def detect(self, frame_bgr: np.ndarray) -> BarResult:
        cfg = self.config
        if cfg.roi is None:
            return BarResult(None, 0, 0, False, "未标定 ROI")

        x1, y1, x2, y2 = cfg.roi
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return BarResult(None, 0, 0, False, "ROI 为空")

        mask = self.color_mask(crop)
        return self._measure(mask)

    def color_mask(self, crop_bgr: np.ndarray) -> np.ndarray:
        """把 BGR 裁剪图转成目标颜色的二值掩码。"""
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, self._lower, self._upper)

    # ---------- 测量 ----------

    def _measure(self, mask: np.ndarray) -> BarResult:
        cfg = self.config
        roi_width = mask.shape[1]

        # 把 span 之外的列直接裁掉（切片是 view，零拷贝），
        # 只统计真正的填充区。比 zeros_like + 赋值省一次全图复制。
        if cfg.span is not None:
            lo = max(0, cfg.span.left)
            hi = min(roi_width, cfg.span.right + 1)
            mask = mask[:, lo:hi]

        # axis=0：只要某一列上有任意一个目标像素，这一列就算"被填充"
        column_hit = np.any(mask > 0, axis=0)
        filled = int(np.count_nonzero(column_hit))

        full = cfg.span.width if cfg.span is not None else roi_width
        if full <= 0:
            return BarResult(None, filled, 0, False, "满值宽度为 0")

        if filled < cfg.min_pixels:
            # 血条低血量时会闪烁，闪烁帧读到 0。
            # 这里不直接返回 0%，而是标记为无效交给平滑层处理 ——
            # 否则会被误判成"快死了"，疯狂喝药。
            return BarResult(
                None, filled, full, False,
                f"命中像素过少({filled}<{cfg.min_pixels})，可能是闪烁帧"
            )

        percent = round(filled / full * 100.0, 1)
        # 数值合理性夹紧：条不可能超过 100%
        percent = max(0.0, min(100.0, percent))
        return BarResult(percent, filled, full, True)

    # ---------- 调试 ----------

    def debug_crop(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        """返回 ROI 裁剪图，供标定 / 调试显示。"""
        if self.config.roi is None:
            return None
        x1, y1, x2, y2 = self.config.roi
        crop = frame_bgr[y1:y2, x1:x2]
        return crop if crop.size else None

    def debug_mask(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        crop = self.debug_crop(frame_bgr)
        if crop is None:
            return None
        return self.color_mask(crop)


class BarsDetector:
    """血条 + 蓝条的组合检测器。"""

    def __init__(self, hp: BarDetector, mp: BarDetector):
        self.hp = hp
        self.mp = mp

    @classmethod
    def from_dict(cls, bars_cfg: dict) -> "BarsDetector":
        """从 cfg["bars"] 这个 dict 构建 hp + mp 两个 BarDetector。"""
        return cls(
            BarDetector(BarConfig.from_dict("血量", bars_cfg["hp"])),
            BarDetector(BarConfig.from_dict("魔法", bars_cfg["mp"])),
        )

    def validate(self, frame_shape: tuple[int, int, int] | None = None) -> None:
        self.hp.config.validate(frame_shape)
        self.mp.config.validate(frame_shape)

    def detect(self, frame_bgr: np.ndarray) -> tuple[BarResult, BarResult]:
        """返回 (hp 结果, mp 结果)。"""
        return self.hp.detect(frame_bgr), self.mp.detect(frame_bgr)
