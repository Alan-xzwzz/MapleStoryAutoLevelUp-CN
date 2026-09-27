# -*- coding: utf-8 -*-
"""报错不该刷屏 + 英文报错要翻译成人话（2026-09-27 加，issue #2 排查中发现）。

【为什么要它】
  用户贴来的日志里，**前几十行全是刷屏**：
      [16:08:00] ERROR: [主循环] run_once 抛异常，挂机已停止推进。完整堆栈如下：
                 Traceback (十几行)
      [16:08:01] INFO: [主循环] 已从异常中恢复（连续失败 5 帧）
      [16:08:01] ERROR: [主循环] run_once 抛异常…（又来一遍）
  真正有用的信息全被埋掉了 —— 这**不是用户的错，是我们的日志设计有问题**。

  根因（两处，都在主循环异常处理里）：
    ① 「n_loop_err == 1 就打完整堆栈」——但 n_loop_err 在**成功一帧后被清零**，
       所以"失败→成功→再失败"交替时**每次都当第一次**，每帧打一遍堆栈；
    ② 「已从异常中恢复」这条**每成功一帧就打一遍**，用户日志里出现 13 次。
  另外 `get_img_frame` 抓不到帧时只有一句英文 "Failed to capture game frame."，
  且**每帧都打**（30fps ⇒ 每秒 30 行）。

⚠️ 用例调**真实入口**（真的去跑那些分支），并用**注入假日志处理器**统计条数，
   不靠读源码找关键字凑数。

用法：
    python -m tools.verify_log_no_flood
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


class _Counter(logging.Handler):
    def __init__(self):
        super().__init__()
        self.n = 0
        self.msgs: list = []

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.n += 1
            self.msgs.append(record.getMessage())


def _logger_of(mod):
    return getattr(mod.logger, "_logger", mod.logger)


def main():
    print("=== 日志不刷屏 + 英文翻译·自检 ===")

    # 【1】A 类抓屏报错必须翻译成人话（且给行动指引）
    print("\n【1】抓屏失败：英文报错要翻译成人话")
    from src.input.GameWindowCapturor import GameWindowCapturor as G
    cases = [
        ("Failed to convert item to `GraphicsCaptureItem`", "窗口句柄失效"),
        ("The Graphics Capture API is not supported on this platform.", "系统版本太老"),
        ("Failed to find window: Failed to find a window with the name: X", "找不到窗口"),
        ("D3D11CreateDevice failed (hardware feature-level 11.0 + BGRA)", "显卡/驱动/虚拟机"),
        ("某个完全没见过的错误", "兜底"),
    ]
    for raw, label in cases:
        out = G._explain_capture_error(Exception(raw))
        check(f"{label} → 有中文说明", "抓屏" in out, out.splitlines()[0][:40])
        check(f"{label} → 给了「怎么办」", "怎么办" in out)

    # 翻译必须**保留原始技术信息**（排查时要靠它），不能只给人话
    out = G._explain_capture_error(Exception("Failed to convert item to `GraphicsCaptureItem`"))
    check("保留技术原文（排查要用）", "技术原文" in out or "Failed to convert" in out)

    # 【2】抓帧失败：必须是中文 + 说原因 + 有节流
    print("\n【2】抓不到帧：中文说明 + 不刷屏")
    eng_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "engine",
                                "MapleStoryAutoLevelUp.py"), encoding="utf-8").read()
    # ⚠️ 只检查**代码行**：注释里会引用旧文案来说明改动原因（"原来只有一句英文
    #    'Failed to capture game frame.'"），那是正常的，不该判失败。
    #    本用例第一版就因此误报了一次。
    code_lines = [ln for ln in eng_src.splitlines()
                  if not ln.strip().startswith("#")]
    code_blob = "\n".join(code_lines)
    check("已无裸英文 'Failed to capture game frame.'",
          "Failed to capture game frame." not in code_blob,
          "那句英文用户看不懂")
    check("改成中文说明", "拿不到游戏画面" in eng_src)
    check("列出了最常见原因（最小化 / 虚拟桌面）",
          "最小化" in eng_src and ("虚拟桌面" in eng_src or "Win+Tab" in eng_src))
    check("有节流（不每帧打）", "_t_last_noframe_warn" in eng_src)

    # 【3】主循环异常：不能每帧刷完整堆栈
    print("\n【3】主循环异常：降频")
    # 关键回归点：原来的判据是 n_loop_err == 1 → 交替失败时会每帧都打
    check("不再用「n_loop_err == 1」当「第一次」的判据",
          "if self.n_loop_err == 1:" not in eng_src,
          "该判据在「失败→成功→失败」交替时会每帧都判定为第一次，导致刷屏")
    check("改用时间节流（t_last_loop_err）",
          "now - getattr(self, \"t_last_loop_err\", 0.0) > 5" in eng_src)
    check("恢复日志也被降级（不再是每成功一帧就报）",
          "if self.n_loop_err > 5:" in eng_src)
    # 节流后必须**仍能**报出（别改过头变成永不报错）
    check("节流后仍保留完整堆栈用于排查",
          "traceback.format_exc()" in eng_src)
    check("堆栈里带上累计失败帧数（避免「看起来只错一次」的误导）",
          "累计失败" in eng_src)

    # 【4】抓屏参数：干扰源要显式关掉
    print("\n【4】抓屏参数：关掉光标与边框")
    cap_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "input",
                                "GameWindowCapturor.py"), encoding="utf-8").read()
    check("cursor_capture=False（光标会污染识别）", "cursor_capture=False" in cap_src)
    # ⚠️ 2026-09-27（issue #4）改判据：这里原先断言的是**无条件** `draw_border=False`，
    #    那条断言本身就是病根 —— 该开关在 build < 20348 的机器上会让库直接抛异常，
    #    用户「一开录制就崩」正是它导致的。现在要求的是**按版本条件化**。
    check("draw_border 按系统版本条件化（老系统上无条件传会崩，见 issue #4）",
          "draw_border=False if _border_off else None" in cap_src)
    check("有版本探测函数且门槛为 build 20348（IsBorderRequired 的最低要求）",
          "_DRAW_BORDER_MIN_BUILD = 20348" in cap_src
          and "RtlGetVersion" in cap_src)
    check("异常兜底：仍不支持时可退回默认设置重试",
          "_is_border_unsupported" in cap_src and "退回默认设置重试" in cap_src)
    check("border 分支排在泛化 platform 分支之前（否则提示错成「系统太老」）",
          cap_src.index("_is_border_unsupported(e)")
          < cap_src.index('"not supported on this platform" in low'))
    # 用正则取实际调用，别靠"缩进多少个空格"来匹配（那样改一下排版就误报）
    import re as _re
    _calls = _re.findall(r"WindowsCapture\((.*?)\)", cap_src, _re.S)
    _kwargs_ok = any("window_hwnd=" in c and "cursor_capture=" in c
                     and "draw_border=" in c for c in _calls)
    check("三个参数都用关键字传（第一个位置参数是 cursor_capture，易传错）",
          _kwargs_ok, f"实际调用={_calls[:1]}")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：日志不刷屏、英文报错已翻译成人话、抓屏干扰源已关掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
