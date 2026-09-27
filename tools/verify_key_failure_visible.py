# -*- coding: utf-8 -*-
"""「按键送不出去」必须留下可查的线索 —— 离线自检（2026-09-27 加）。

【为什么需要它】
  用户报过这样的症状（issue #2，2026-09-27）：
      引擎在发指令（`left none none`）、角色纹丝不动、位置 10 秒没变、附近怪 0。
      —— 工具在努力按左键，但游戏收不到。

  而当时 `key_down` / `key_up` / `press_key` 把异常**一律记 logger.debug**，
  日志默认级别是 INFO ⇒ **日志里一条线索都没有**。用户能拿到的只有"角色卡住了"，
  根本无从判断是权限、驱动还是别的问题，只能来回猜。

  ⇒ 改成「每类失败只大声报一次 WARNING，并列出最常见原因」，
     把这件事钉成用例：谁把它改回 debug，这个自检就红。

⚠️ 用例调**真实入口**（InterceptionController.key_down 等），用假模块模拟失败，
   不自己复刻判据。

用法：
    python -m tools.verify_key_failure_visible
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.input import InterceptionController as ic      # noqa: E402

FAIL: list = []

# 真实 interception（main 里保存，收尾还原）
_REAL_INTERCEPTION = None


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


class _Counter(logging.Handler):
    """精确统计各等级的日志条数（不受项目 logger 自身级别影响）。"""

    def __init__(self):
        super().__init__()
        self.warnings = 0
        self.debugs = 0
        self.texts = []

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.warnings += 1
            self.texts.append(record.getMessage())
        else:
            self.debugs += 1


def _attach():
    base = getattr(ic.logger, "_logger", ic.logger)
    c = _Counter()
    base.addHandler(c)
    return base, c


def main():
    print("=== 按键失败必须可见·自检 ===")
    base = getattr(ic.logger, "_logger", ic.logger)
    # 记下真实值，收尾时还原（本用例会把 interception 临时置 None）
    global _REAL_INTERCEPTION
    _REAL_INTERCEPTION = ic.interception

    # 【1】拦截失败时，key_down 要报 WARNING（不是 debug）
    print("\n【1】key_down 失败要报 WARNING")
    ic._KEY_FAIL_LOGGED.clear()
    ic.interception = None          # 模拟"驱动/库不可用"
    b, c = _attach()
    try:
        ic.key_down("left")
    finally:
        b.removeHandler(c)
    check("key_down 失败产生 WARNING", c.warnings >= 1,
          f"warnings={c.warnings}")
    check("WARNING 里说明了后果（角色/原地不动）",
          any("角色" in t or "原地不动" in t for t in c.texts),
          f"texts={c.texts[:1]}")
    check("WARNING 里给了最常见原因（管理员/驱动/前台）",
          any(("管理员" in t and "驱动" in t) for t in c.texts),
          f"texts={c.texts[:1]}")

    # 【2】同类失败**只报一次** —— 否则按 fps 刷屏，日志被淹没
    print("\n【2】同类失败只报一次（不刷屏）")
    ic._KEY_FAIL_LOGGED.clear()
    ic.interception = None
    b, c = _attach()
    try:
        for _ in range(20):
            ic.key_down("left")
    finally:
        b.removeHandler(c)
    check("连按 20 次只报 1 条 WARNING", c.warnings == 1, f"warnings={c.warnings}")
    # ⚠️ 这里**不能**断言「后续是 debug 且计数为 N」：日志默认级别是 INFO，
    #    debug 记录在到达 handler 之前就被 logger 过滤掉了，计数天然是 0。
    #    而那正是"不刷屏"想要的效果 —— 后 19 次在默认级别下**完全不可见**。
    #    所以断言的是「后续没有再产生可见输出」。
    check("后续 19 次不再产生可见输出（默认 INFO 级下）",
          c.warnings == 1, f"警告数={c.warnings}（应为 1，多出来就是刷屏）")

    # 【3】key_up / press_key 走同一条报告路径
    print("\n【3】key_up 与 press_key 也要可见")
    for fn_name in ("key_up", "press_key"):
        ic._KEY_FAIL_LOGGED.clear()
        ic.interception = None
        b, c = _attach()
        try:
            getattr(ic, fn_name)("left")
        finally:
            b.removeHandler(c)
        check(f"{fn_name} 失败产生 WARNING", c.warnings >= 1,
              f"warnings={c.warnings}")

    # 【4】不同类别的失败各自报一次（判重按「API+异常类型」，不是一刀切）
    print("\n【4】判重粒度：不同 API 各自报一次")
    ic._KEY_FAIL_LOGGED.clear()
    ic.interception = None
    b, c = _attach()
    try:
        ic.key_down("left")
        ic.key_up("left")
    finally:
        b.removeHandler(c)
    check("两个不同 API 各报一次", c.warnings == 2, f"warnings={c.warnings}")

    # 【5】收尾：恢复现场，别把全局状态留给别的用例
    print("\n【5】收尾")
    check("_KEY_FAIL_LOGGED 可清空（不是只增不减的全局脏状态）",
          isinstance(ic._KEY_FAIL_LOGGED, set))
    ic._KEY_FAIL_LOGGED.clear()
    ic.interception = _REAL_INTERCEPTION
    check("已恢复 interception 模块（不污染同进程后续用例）",
          ic.interception is _REAL_INTERCEPTION)

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：按键送不出去时会留下可查的 WARNING，且不会刷屏。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
