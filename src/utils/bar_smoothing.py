"""读数平滑：抑制血条闪烁造成的 0 值跳变。

问题（OpenCV 项目 README 里记录的坑）：
    血条在低血量时会闪烁，闪烁那一帧抠不到颜色，读数直接掉到 0，
    于是防呆逻辑反而变成"一直触发喝药"。

解决办法：
    维护一个最近 N 个**有效**读数的滑窗，对外报窗口内的**最大值**。
    - 闪烁帧被判定为无效，不进入窗口，也就不会把读数拉低
    - 连续无效帧太多（切图、读不到）则整体判为"未知"，
      此时上游不会做任何动作 —— 宁可不喝，也不乱喝

────────────────────────────────────────────────────────────────────
移植自 Alan-xzwzz 的 PR #3（MapleStoryAutoLevelUp-CN），MIT License。
本项目（冒险岛自动练级-公开版）按自身环境约束做了改造，见
docs/血蓝监控-实现交接.md。本文件逻辑与原作一致，未作语义修改；
只把 logger 引用与配置口径对齐到本项目（src.utils.logger + 扁平配置键）。
"""

from __future__ import annotations

from collections import deque

from src.utils.bars import BarResult


class ReadingSmoother:
    """单个指标的读数平滑器。"""

    def __init__(self, window: int = 3, max_invalid_frames: int = 10):
        self.window = max(1, int(window))
        self.max_invalid_frames = max(1, int(max_invalid_frames))
        self._values: deque[float] = deque(maxlen=self.window)
        self._invalid_streak = 0
        self._last_reason = ""

    def update(self, result: BarResult) -> float | None:
        """喂入一帧结果，返回平滑后的百分比；None 表示当前不可信。"""
        if result.valid and result.percent is not None:
            self._values.append(result.percent)
            self._invalid_streak = 0
            self._last_reason = ""
            return self.value()

        self._invalid_streak += 1
        self._last_reason = result.reason

        if self._invalid_streak >= self.max_invalid_frames:
            # 长时间读不到，清空历史，避免拿着过期数据做判断
            self._values.clear()
            return None

        # 短暂闪烁：沿用窗口内的历史最大值
        return self.value()

    def value(self) -> float | None:
        """当前平滑值。窗口为空返回 None。"""
        if not self._values:
            return None
        return max(self._values)

    @property
    def is_stale(self) -> bool:
        """是否处于"长时间读不到"状态。"""
        return self._invalid_streak >= self.max_invalid_frames

    @property
    def invalid_streak(self) -> int:
        return self._invalid_streak

    @property
    def last_reason(self) -> str:
        return self._last_reason

    def reset(self) -> None:
        self._values.clear()
        self._invalid_streak = 0
        self._last_reason = ""


class BarsSmoother:
    """血条 + 蓝条的平滑器组合。"""

    def __init__(self, window: int = 3, max_invalid_frames: int = 10):
        self.hp = ReadingSmoother(window, max_invalid_frames)
        self.mp = ReadingSmoother(window, max_invalid_frames)

    def update(self, hp: BarResult, mp: BarResult) -> tuple[float | None, float | None]:
        return self.hp.update(hp), self.mp.update(mp)

    def reset(self) -> None:
        self.hp.reset()
        self.mp.reset()
