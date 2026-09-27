"""自动吃药决策。

判定链路（每一步都是为了挡掉误判）：

    1. 读数为 None（闪烁 / 切图 / 没标定）        → 不动
    2. 读数不在 (0, 100] 内（异常值）             → 不动
    3. 距上次喝同种药不足冷却时间                 → 不动
    4. 连续 confirm_frames 帧都低于某档阈值        → 才真正按键

第 4 步是防抖的关键：血条偶尔闪一下、切图瞬间读到一个低值，
单帧判断就会误喝。要求连续 N 帧一致可以几乎完全消除这类误触。

档位选择：
    阈值表按 threshold 从小到大排。读数低于哪一档就用哪一档的键。
    key 为 null 的档位跳过（占位用），继续往上找有键的档。

移植自 maplestory-bot/src/input/potions.py，差异：
    - PotionDecision 定义在本文件里，不再从 base 包 import；
    - PotionDrinker.from_dict(potion_cfg) 接收 cfg["potion"] 子 dict，
      用扁平键（hp / mp / hp_cooldown_sec / ...）替代原版的点号路径；
    - 日志改用本项目的 src.utils.logger.logger（单参数 f-string 风格）。

────────────────────────────────────────────────────────────────────
移植自 Alan-xzwzz 的 PR #3（MapleStoryAutoLevelUp-CN），MIT License。
本项目（冒险岛自动练级-公开版）在此基础上的改造：
    1. 新增 `press_potion()` —— 原作把"按键执行"留给调用方，
       本项目把它收进本模块，并**在这里补上前台守卫的 return**（见下）。
    2. PotionDrinker 增加 enabled 总开关（可实时关掉喝药而不影响读数）。
"""

from __future__ import annotations

import time
from typing import NamedTuple

from src.utils.logger import logger


class PotionDecision(NamedTuple):
    """一次药水决策结果。"""

    action: str | None = None
    """要执行的动作：目前只有 "drink"；None 表示什么都不做。"""

    key: str | None = None
    """要按的键。"""

    tier: str | None = None
    """命中的档位名。"""

    bar: str | None = None
    """hp 还是 mp。"""

    reason: str = ""
    """为什么做/不做这个决定，用于日志和调试窗。"""

    @property
    def should_drink(self) -> bool:
        return self.action == "drink" and bool(self.key)


class PotionTier(NamedTuple):
    """一个喝水档位。"""

    name: str
    threshold: float
    key: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.key)


def parse_tiers(raw, label: str) -> list[PotionTier]:
    """把配置里的档位列表解析成 PotionTier，并按阈值升序排列。"""
    if not raw:
        return []
    tiers = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{label} 的档位配置格式错误: {item!r}")
        key = item.get("key")
        if key is not None:
            key = str(key)
        tiers.append(
            PotionTier(
                name=str(item.get("name", "?")),
                threshold=float(item["threshold"]),
                key=key,
            )
        )
    tiers.sort(key=lambda t: t.threshold)
    return tiers


class _BarState:
    """单个指标（血或蓝）的判定状态。"""

    def __init__(self, name: str, tiers: list[PotionTier], cooldown_sec: float,
                 confirm_frames: int):
        self.name = name
        self.tiers = tiers
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.confirm_frames = max(1, int(confirm_frames))

        self._last_drink_at: float | None = None
        self._candidate_tier: str | None = None
        self._candidate_streak = 0
        self.last_reason = ""

    @property
    def enabled(self) -> bool:
        return any(t.enabled for t in self.tiers)

    def _pick_tier(self, value: float) -> PotionTier | None:
        """选出该用哪一档：阈值最小的、真正低于它、且配了键的那档。

        档位按阈值升序排列，所以要从低往高逐个检查：
        读数可能低于多档阈值，取最低（最紧急）的那档。

        注意不能因为"没低于这一档"就提前退出 —— 阈值是递增的，
        读数低于 critical 就必然也低于 light，但反之不成立：
        40% 血不满足 critical(20%)，却满足 medium(50%)。
        """
        for tier in self.tiers:
            if value < tier.threshold and tier.enabled:
                return tier
        return None

    def evaluate(self, value: float | None, now: float) -> PotionDecision:
        if not self.enabled:
            self.last_reason = "未配置药水键"
            return PotionDecision(reason=self.last_reason)

        if value is None:
            self._reset_streak()
            self.last_reason = "读数不可信，跳过"
            return PotionDecision(reason=self.last_reason)

        # 异常值防呆：0 或负数或超过 100 都当作读错
        if not (0 < value <= 100):
            self._reset_streak()
            self.last_reason = f"读数异常({value})，跳过"
            logger.debug(f"{self.name} 读数异常: {value}")
            return PotionDecision(reason=self.last_reason)

        tier = self._pick_tier(value)
        if tier is None:
            self._reset_streak()
            self.last_reason = f"{value:.1f}% 高于所有阈值"
            return PotionDecision(reason=self.last_reason)

        # 连续帧确认
        if self._candidate_tier == tier.name:
            self._candidate_streak += 1
        else:
            self._candidate_tier = tier.name
            self._candidate_streak = 1

        if self._candidate_streak < self.confirm_frames:
            self.last_reason = (
                f"{value:.1f}% < {tier.threshold:.0f}% ({tier.name}) "
                f"确认中 {self._candidate_streak}/{self.confirm_frames}"
            )
            return PotionDecision(reason=self.last_reason)

        # 冷却检查放在确认之后：这样"确认期间"的耗时也计入冷却
        if self._last_drink_at is not None:
            elapsed = now - self._last_drink_at
            if elapsed < self.cooldown_sec:
                self.last_reason = (
                    f"冷却中 {elapsed:.1f}/{self.cooldown_sec:.1f}s"
                )
                return PotionDecision(reason=self.last_reason)

        self._last_drink_at = now
        self._reset_streak()
        self.last_reason = f"{value:.1f}% < {tier.threshold:.0f}% → 喝{tier.name}"
        logger.info(
            f"{self.name} 喝水：读数 {value:.1f}% 低于 {tier.name} 档"
            f"({tier.threshold:.0f}%)，按键 {tier.key}"
        )
        return PotionDecision(
            action="drink",
            key=tier.key,
            tier=tier.name,
            bar=self.name,
            reason=self.last_reason,
        )

    def _reset_streak(self) -> None:
        self._candidate_tier = None
        self._candidate_streak = 0

    def note_external_drink(self, now: float) -> None:
        """外部（比如手动）喝过药时同步冷却，避免脚本紧接着再喝一次。"""
        self._last_drink_at = now

    def cooldown_remaining(self, now: float) -> float:
        if self._last_drink_at is None:
            return 0.0
        return max(0.0, self.cooldown_sec - (now - self._last_drink_at))


class PotionDrinker:
    """血蓝药决策器。

    无副作用：evaluate() 只返回决定，不真的按键。
    按键由调用方（主循环）执行 —— 这样逻辑可测、可回放。
    """

    def __init__(
        self,
        hp_tiers: list[PotionTier],
        mp_tiers: list[PotionTier],
        hp_cooldown_sec: float = 1.5,
        mp_cooldown_sec: float = 1.5,
        confirm_frames: int = 2,
    ):
        self.hp = _BarState("血量", hp_tiers, hp_cooldown_sec, confirm_frames)
        self.mp = _BarState("魔法", mp_tiers, mp_cooldown_sec, confirm_frames)
        self.last_decision = PotionDecision(reason="尚未开始")
        # 总开关：可实时关掉喝药，不影响血量读数和预览
        self.enabled = True

    @classmethod
    def from_dict(cls, potion_cfg) -> "PotionDrinker":
        """从 cfg["potion"] 子 dict 构造。

        与原版 from_config 的区别：原版读点号路径 cfg.get("potion.hp")，
        本项目传入的已是 potion 子 dict，故改用扁平键 potion_cfg.get("hp")。
        """
        return cls(
            hp_tiers=parse_tiers(potion_cfg.get("hp"), "hp"),
            mp_tiers=parse_tiers(potion_cfg.get("mp"), "mp"),
            hp_cooldown_sec=float(potion_cfg.get("hp_cooldown_sec", 1.5)),
            mp_cooldown_sec=float(potion_cfg.get("mp_cooldown_sec", 1.5)),
            confirm_frames=int(potion_cfg.get("confirm_frames", 2)),
        )

    def evaluate(
        self,
        hp: float | None,
        mp: float | None,
        now: float | None = None,
    ) -> PotionDecision:
        """给出这一拍该不该喝药。

        血量优先于蓝量：血量低更危险。
        两者都不需要时返回 should_drink=False 的决策。
        """
        now = time.monotonic() if now is None else now

        # 总开关关闭 → 不喝药
        if not self.enabled:
            self.last_decision = PotionDecision(reason="喝药已关闭")
            return self.last_decision

        hp_decision = self.hp.evaluate(hp, now)
        if hp_decision.should_drink:
            self.last_decision = hp_decision
            return hp_decision

        mp_decision = self.mp.evaluate(mp, now)
        if mp_decision.should_drink:
            self.last_decision = mp_decision
            return mp_decision

        # 都不喝：把两条的原因拼起来，方便调试窗显示
        self.last_decision = PotionDecision(
            reason=f"血:{hp_decision.reason} | 蓝:{mp_decision.reason}"
        )
        return self.last_decision

    def validate_keys(self, key_checker) -> None:
        """校验所有配置的药水键名合法。key_checker 抛异常即视为非法。"""
        for bar in (self.hp, self.mp):
            for tier in bar.tiers:
                if tier.key:
                    key_checker(tier.key)


# ============================================================
# 按键执行（本项目新增 —— 原 PR 把这一步留给调用方）
# ============================================================

def press_potion(key: str, kb=None, logger_=None) -> bool:
    """真的把药水键发出去。返回是否**发出**（不是是否生效）。

    ⚠️★ 2026-09-27 本项目改造点，请勿简化 —— 前台守卫分支**必须 return**：
        原作对应的实现里，守卫是这么写的：

            if not self.is_game_window_active():
                logger.warning("游戏窗口不在前台，药水键可能发到别的程序上")

        它**只有一句警告、没有 return** —— 警告完照常执行 press_key(key)。
        作者在注释里的理由是「药水键误发到桌面危害小」，但默认档位键
        （pageup / home / end）在浏览器、资源管理器、编辑器里**都有实际作用**：
        翻页、跳到文首/文末，足以把用户正在编辑的文档搞乱。
        ⇒ 这里补上 return：**不在前台就一个字都不发**，
          与引擎主循环里「前台不对就不发键」的行为保持一致。

    Args:
        key:   要按的键名（来自配置的档位）
        kb:    可选的键盘控制器；给了就用它的 press_key
        logger_:可选的 logger（默认用本模块的），便于单测注入

    Returns:
        True = 键已发出；False = 被前台守卫拦下 / 键为空
    """
    log = logger_ or logger

    if not key:
        return False

    # ── 前台守卫 ──────────────────────────────────────────────
    # 只有"游戏窗口就是当前前台窗口"时才允许发键。
    # kb 为 None（离线自检、无真实键盘）时跳过守卫 —— 那种场景下
    # 根本不会真的按键，属测试路径。
    if kb is not None:
        try:
            active = kb.is_game_window_active()
        except Exception as e:                                   # noqa: BLE001
            # 判不出来 = 不敢发（宁可漏喝一瓶，也不要把键发到别人窗口上）
            log.warning(f"[喝药] 判断游戏窗口是否在前台时出错，本次不发键：{e}")
            return False

        if not active:
            # ★ 就是这里，原文缺 return。缺了它警告会变成一句空话。
            log.warning(
                "[喝药] 游戏窗口不在前台，本次不发药水键"
                "（避免翻页键落到浏览器/编辑器上）。"
                "把游戏窗口点回最前面即可恢复正常。")
            return False

    try:
        if kb is not None:
            kb.press_key(key)
        else:
            from src.input.KeyBoardController import press_key
            press_key(key)
    except Exception as e:                                       # noqa: BLE001
        # 发键失败不能把主循环带崩（挂机场景下"少喝一瓶"远好于"整个停摆"）
        log.warning(f"[喝药] 发送药水键 {key!r} 失败：{e}")
        return False

    return True
