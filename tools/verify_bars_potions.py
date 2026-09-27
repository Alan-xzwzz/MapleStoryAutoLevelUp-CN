# -*- coding: utf-8 -*-
"""血蓝监控 + 自动喝药 的离线自检（2026-09-27 加）。

【为什么需要它】
  这个功能有三个"错了也看不出来"的失败模式，全都在本次实现中被实际踩到过：

    ① **span 不写 → 读数封顶 → 血满着疯狂喝药**
       满值宽度退化成整个 ROI 宽度时，血满只算出 60% 之类，
       配合 low 档 threshold=60 就是"血满也在喝"。
    ② **validate() 不接进 from_dict → ROI 配错只静默无效**
       表现是"读数永远未知、药一次不喝"，日志干干净净，极难查。
    ③ **前台守卫缺 return → 药水键落到别的窗口上**
       默认档位键 pageup/home/end 在浏览器、编辑器里都会真的翻页。

  ⇒ 三条各钉一个用例。谁把它们改回去，这个自检就会红。

⚠️ 用例一律调**真实入口**（BarConfig / BarDetector / PotionDrinker /
   press_potion），不自己复刻一套判据 —— 复刻一遍就测不到真东西了。

用例全部离线：不需要游戏窗口、不抓帧、不发真按键（kb 用桩）。

用法：
    python -m tools.verify_bars_potions
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np                                            # noqa: E402

from src.utils.bars import BarConfig, BarDetector, BarsDetector  # noqa: E402
from src.utils.bar_smoothing import BarsSmoother               # noqa: E402
from src.utils.potions import PotionDrinker, press_potion      # noqa: E402
from src.utils.common import load_yaml                         # noqa: E402
from src.utils.env_precheck import check_bars_environment      # noqa: E402

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


# 血条红 / 蓝条蓝 的 HSV（与 config_default.yaml 一致）
RED_L, RED_U = [0, 200, 200], [5, 255, 255]
BLUE_L, BLUE_U = [100, 132, 90], [107, 255, 255]


class _FakeKB:
    """假的键盘控制器：记录发了哪些键、可设定前台状态。"""

    def __init__(self, active=True):
        self.active = active
        self.sent = []

    def is_game_window_active(self):
        return self.active

    def press_key(self, k):
        self.sent.append(k)


def solid_frame(h, w, span=[0, 0, 0, 0], color=(0, 0, 255)):
    """造一帧：在 span=[x1,y1,x2,y2] 区域涂一个纯色块。"""
    f = np.zeros((h, w, 3), dtype=np.uint8)
    x1, y1, x2, y2 = span
    if x2 > x1 and y2 > y1:
        f[y1:y2, x1:x2] = color
    return f


def main():
    print("=== 血蓝监控 + 自动喝药·自检 ===")

    # ── 【1】span=[0, roi_width] 必须合法（原严格 `<` 会抛）───────────
    # 这是"满值区正好铺满整个 ROI"的写法，精确标定后很常见。
    try:
        cfg = BarConfig.from_dict("血量", {
            "roi": [0, 0, 100, 20],
            "span": [0, 100],          # right == roi_width，合法
            "hsv_lower": RED_L, "hsv_upper": RED_U,
        })
        cfg.validate((768, 1366, 3))
        check("span 取到 roi 宽（闭区间右边界）不被误判越界", True)
    except Exception as e:                                       # noqa: BLE001
        check("span 取到 roi 宽（闭区间右边界）不被误判越界", False, str(e))

    # ── 【2】真越界的 span 必须抛 ────────────────────────────────────
    try:
        BarConfig.from_dict("魔法", {
            "roi": [0, 0, 100, 20],
            "span": [0, 101],          # 超出 roi 宽 1px
            "hsv_lower": BLUE_L, "hsv_upper": BLUE_U,
        })
        check("span 真越界时抛 ValueError", False, "居然没抛")
    except ValueError:
        check("span 真越界时抛 ValueError", True)
    except Exception as e:                                       # noqa: BLE001
        check("span 真越界时抛 ValueError", False, f"抛的是别的异常: {e}")

    # ── 【3】validate() 必须接进 from_dict（ROI 配错要在加载期就报）────
    try:
        BarConfig.from_dict("血量", {
            "roi": [5, 5, 5, 20],      # 空矩形
            "hsv_lower": RED_L, "hsv_upper": RED_U,
        })
        check("ROI 配错时 from_dict 当场抛（validate 已接入）", False, "居然没抛")
    except ValueError:
        check("ROI 配错时 from_dict 当场抛（validate 已接入）", True)
    except Exception as e:                                       # noqa: BLE001
        check("ROI 配错时 from_dict 当场抛（validate 已接入）", False, f"抛的是别的异常: {e}")

    # ── 【4】满值读数必须正好 100%（span 生效的核心证据）───────────────
    # 用 1366x768 实测值：满值宽 165
    bar_cfg = BarConfig.from_dict("血量", {
        "roi": [467, 733, 632, 767],       # 宽 165
        "span": [0, 164],                  # 满值宽 165
        "hsv_lower": RED_L, "hsv_upper": RED_U,
    })
    det = BarDetector(bar_cfg)
    frame = solid_frame(768, 1366, [467, 733, 632, 767], (0, 0, 255))
    r = det.detect(frame)
    check("满血读数正好 100%（span 生效、没封顶）",
          r.valid and r.percent == 100.0,
          f"percent={r.percent} valid={r.valid}")

    # ── 【5】半血读数必须 ≈50%（不是按 ROI 宽瞎算）───────────────────
    half = solid_frame(768, 1366, [467, 733, 467 + 82, 767], (0, 0, 255))
    r5 = det.detect(half)
    ok5 = r5.valid and r5.percent is not None and abs(r5.percent - 49.7) < 1.5
    check("半血读数 ≈50%", ok5, f"percent={r5.percent}")

    # ── 【6】读不到时必须返回 valid=False 且 percent=None（不是 0%）────
    # 这是防误喝的关键：返回 0% 会被当成"快死了"疯狂喝药。
    empty = solid_frame(768, 1366)
    r6 = det.detect(empty)
    check("读不到时返回无效帧（percent=None，不是 0%）",
          (r6.valid is False) and (r6.percent is None),
          f"valid={r6.valid} percent={r6.percent} reason={r6.reason}")

    # ── 【7】平滑层：短暂闪烁不能把读数拉低 ──────────────────────────
    sm = BarsSmoother(window=3, max_invalid_frames=10)
    from src.utils.bars import BarResult
    sm.update(BarResult(80.0, 80, 100, True), BarResult(80.0, 80, 100, True))
    v_hp, _ = sm.update(BarResult(None, 0, 100, False, "闪烁帧"),
                        BarResult(80.0, 80, 100, True))
    check("闪烁一帧不会把读数拉低（沿用窗口内最大值）", v_hp == 80.0, f"得到 {v_hp}")

    # 连续无效超过上限 → 判为未知（宁可不喝）
    for _ in range(12):
        v_hp2, _ = sm.update(BarResult(None, 0, 100, False, "blocked"),
                             BarResult(None, 0, 100, False, "blocked"))
    check("连续无效帧过多时判为未知（不拿过期数据做判断）", v_hp2 is None, f"得到 {v_hp2}")

    # ── 【8】决策：单帧低于阈值不喝、连续 confirm_frames 帧才喝 ─────────
    dr = PotionDrinker.from_dict({
        "hp": [{"name": "low", "threshold": 60, "key": "pageup"}],
        "mp": [],
        "confirm_frames": 2,
    })
    d1 = dr.evaluate(45.0, None, now=1000.0)
    d2 = dr.evaluate(45.0, None, now=1000.1)
    check("单帧低血不喝、连续 2 帧才喝",
          (not d1.should_drink) and d2.should_drink and d2.key == "pageup",
          f"第1帧={d1.should_drink} 第2帧={d2.should_drink} key={d2.key}")

    # ── 【9】冷却：刚喝过不能马上再喝 ────────────────────────────────
    d3 = dr.evaluate(45.0, None, now=1000.2)   # 距 1000.1 只过 0.1s < 1.5s
    check("冷却期内不再喝", not d3.should_drink, f"reason={d3.reason}")

    # ── 【10】读数为 None 时不喝（宁可不喝也不乱喝）───────────────────
    dr2 = PotionDrinker.from_dict({
        "hp": [{"name": "low", "threshold": 60, "key": "pageup"}],
        "mp": [], "confirm_frames": 1,
    })
    d10 = dr2.evaluate(None, None, now=2000.0)
    check("读数为 None 时不喝", not d10.should_drink, f"reason={d10.reason}")

    # ── 【11】★前台守卫必须真的拦住（缺 return 的回归用例）────────────
    # 不在游戏前台时，一个键都不能发出去。
    kb_bg = _FakeKB(active=False)
    ret_bg = press_potion("pageup", kb=kb_bg)
    check("★不在游戏窗口前台时：不发键 + 返回 False",
          (kb_bg.sent == []) and (ret_bg is False),
          f"发了={kb_bg.sent} 返回={ret_bg}")

    kb_fg = _FakeKB(active=True)
    ret_fg = press_potion("pageup", kb=kb_fg)
    check("在游戏窗口前台时：正常发键 + 返回 True",
          (kb_fg.sent == ["pageup"]) and (ret_fg is True),
          f"发了={kb_fg.sent} 返回={ret_fg}")

    # ── 【12】配置里必须有 bars / potion 段且 span 齐备 ────────────────
    # 出厂值缺 span 就是本次修的那个"血满着疯狂喝药"的坑。
    try:
        cfg_yaml = load_yaml("config/config_default.yaml")
        bars = cfg_yaml.get("bars") or {}
        potion = cfg_yaml.get("potion") or {}
        missing = []
        for key in ("hp", "mp"):
            seg = bars.get(key) or {}
            if not seg.get("roi"):
                missing.append(f"bars.{key}.roi")
            if not seg.get("span"):
                missing.append(f"bars.{key}.span")   # ★ 就是这一项
            if not seg.get("hsv_lower") or not seg.get("hsv_upper"):
                missing.append(f"bars.{key}.hsv")
        if not potion.get("hp"):
            missing.append("potion.hp")
        if not potion.get("mp"):
            missing.append("potion.mp")
        check("config_default.yaml 的 bars/potion 段齐备（含 span）",
              not missing, f"缺: {missing}")
    except Exception as e:                                       # noqa: BLE001
        check("config_default.yaml 的 bars/potion 段齐备（含 span）", False, str(e))

    # ── 【13】出厂 roi 必须与 1366x768 真机实测值一致 ──────────────────
    # ⚠️★ 2026-09-27 真机实测订正：血条与蓝条是**并排两条**（左红右蓝），
    #    不是上下叠放 —— 原来照着交接文档把两条 roi 写成同一个，
    #    真机上蓝条 ROI 里装的其实是血条，蓝色一个像素都搜不到。
    #    现在两条 roi 必须**不同**（各在自己那条上），且宽度都 = 165。
    try:
        cfg_yaml = load_yaml("config/config_default.yaml")
        bars = BarsDetector.from_dict(cfg_yaml["bars"])
        hp_roi = bars.hp.config.roi
        mp_roi = bars.mp.config.roi
        hp_w = hp_roi[2] - hp_roi[0]
        mp_w = mp_roi[2] - mp_roi[0]
        # 两条都是 165 宽；且左右位置不同（蓝条在血条右边）
        ok13 = (hp_w == 165 and mp_w == 165
                and bars.hp.config.span.width == 165
                and bars.mp.config.span.width == 165
                and mp_roi[0] > hp_roi[0])          # ★ 蓝条必须在血条右边
        check("出厂 roi：两条各 165 宽、span 齐备、蓝条在血条右侧", ok13,
              f"hp宽={hp_w}@{hp_roi[0]} mp宽={mp_w}@{mp_roi[0]} "
              f"span={bars.hp.config.span.width}/{bars.mp.config.span.width}")
    except Exception as e:                                       # noqa: BLE001
        check("出厂 roi：两条各 165 宽、span 齐备、蓝条在血条右侧", False, str(e))

    # ── 【14】环境预检：能给出人话结论（不断言具体 ok，因 CI 无游戏窗口）──
    # 只要求"能跑通并返回三元组"，且**不匹配时必须带怎么改**。
    try:
        cfg_yaml = load_yaml("config/config_default.yaml")
        ok, title, msg = check_bars_environment(cfg_yaml)
        is_triple = isinstance(ok, bool) and isinstance(title, str) and isinstance(msg, str)
        # 不匹配时提示必须包含改法关键词（否则用户无从下手）
        has_howto = ok or any(k in msg for k in ("1366", "100%", "窗口化", "缩放"))
        check("环境预检返回 (ok, 标题, 说明)，且拦下时带『怎么改』",
              is_triple and has_howto,
              f"ok={ok} title={title!r}")
    except Exception as e:                                       # noqa: BLE001
        check("环境预检返回 (ok, 标题, 说明)，且拦下时带『怎么改』", False, str(e))

    # ── 【15】预检失败时引擎必须**降级不崩**（挂机照常，只是不喝药）────
    # 用一份把 roi 改坏的配置，走真实装配入口，要求不抛异常且 bars_ok=False。
    try:
        from types import SimpleNamespace
        from src.engine.MapleStoryAutoLevelUp import MapleStoryAutoBot
        args = SimpleNamespace(disable_control=True, cfg="default", debug=False,
                               record=False, is_ui=True, disable_viz=True,
                               test_image="", init_state="")
        bot = MapleStoryAutoBot(args)
        bad = load_yaml("config/config_default.yaml")
        bad["bars"]["hp"]["roi"] = [0, 0, 5, 5]
        bad["bars"]["hp"]["span"] = [0, 999]      # 明显越界
        bot._setup_bars_and_potions(bad)          # 不应抛
        check("血蓝配置非法时：装配不抛异常、功能降级关闭",
              (bot.bars_detector is None) and (bot.bars_ok is False),
              f"detector={bot.bars_detector} bars_ok={bot.bars_ok}")
    except Exception as e:                                       # noqa: BLE001
        check("血蓝配置非法时：装配不抛异常、功能降级关闭", False, f"抛了异常: {e}")

    print()
    if FAIL:
        print(f"失败的用例（{len(FAIL)}）：{', '.join(FAIL)}")
        return 1
    print("全部通过（17 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
