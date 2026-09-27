# -*- coding: utf-8 -*-
"""发版前 CI 门禁的**离线**自检（2026-09-27 加）。

【为什么需要它】
  `verify_ci_green` 是"发版前拦住你"的那道闸。如果这道闸自己坏了 ——
  比如把 failure 判成 success —— 那它会**给出假绿**，
  比没有这道闸更糟（你会以为检查过了）。

  所以：**得判据本身**要有用例。用例不联网，用假的 run 数据喂判据函数。

⚠️ 用例调**真实入口**（`tools.verify_ci_green.judge`），不自己复刻一套判定 ——
   复刻一遍就测不到真东西了（项目纪律）。

用法：
    python -m tools.verify_ci_green_check
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.verify_ci_green import judge                       # noqa: E402

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def main():
    print("=== 发版前 CI 门禁·自检 ===")

    # ── 【1】★最重要：failure 绝不能被判成放行（假绿 = 比没闸更糟）──────
    ok, verdict, _ = judge({"status": "completed", "conclusion": "failure",
                            "databaseId": 1, "url": "u"})
    check("★failure 必须拦住（不许给假绿）", ok is False, f"ok={ok} verdict={verdict}")

    # ── 【2】success 才放行 ───────────────────────────────────────────
    ok2, v2, _ = judge({"status": "completed", "conclusion": "success",
                        "databaseId": 2, "url": "u"})
    check("success 放行", ok2 is True, f"ok={ok2} verdict={v2}")

    # ── 【3】还在跑的时候不能放行（否则等于没等它跑完就发）──────────────
    for st in ("in_progress", "queued", "requested", "waiting"):
        ok3, v3, _ = judge({"status": st, "conclusion": "", "databaseId": 3})
        if ok3:
            check(f"status={st} 时不许放行", False, f"竟然放行了：{v3}")
            break
    else:
        check("CI 还在跑时不许放行（等它跑完）", True)

    # ── 【4】其它非 success 结论一律拦住 ─────────────────────────────
    bad_ones = ["cancelled", "timed_out", "startup_failure", "stale", "neutral", "skipped"]
    leaked = []
    for c in bad_ones:
        okc, _, _ = judge({"status": "completed", "conclusion": c, "databaseId": 4})
        if okc:
            leaked.append(c)
    check("其它非 success 结论一律拦住", not leaked, f"漏放的：{leaked}")

    # ── 【5】查不到 / 出错时必须拦住，不能默认放行 ─────────────────────
    # 「查不到就放行」是最危险的默认值 —— 断网了也照样让你发版。
    ok5, _, _ = judge(None)
    ok6, _, _ = judge({"_error": "网络不通"})
    check("没有对应 run 时拦住（不默认放行）", ok5 is False, f"ok={ok5}")
    check("查询出错时拦住（不默认放行）", ok6 is False, f"ok={ok6}")

    # ── 【6】拦下时的说明必须给出"下一步怎么做"（用户不会自己猜）────────
    _, _, advice = judge({"status": "completed", "conclusion": "failure",
                          "databaseId": 98765})
    has_howto = ("gh run view" in advice) and ("98765" in advice)
    check("拦下时给出具体排查命令（含 run id）", has_howto, advice[:80])

    # ── 【7】按 commit 查而不是"最近一次" —— 防止拿别人的绿灯放行 ───────
    # 这是设计约束，靠源码断言钉住（改成"最近一次"就会红）。
    src_path = os.path.join(os.path.dirname(__file__), "verify_ci_green.py")
    src = open(src_path, encoding="utf-8").read()
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    uses_commit = '"--commit", sha' in code
    check("查询按 --commit <sha> 过滤（不拿别人的绿灯放行）", uses_commit,
          "源码里应有 --commit sha 过滤")

    # ── 【8】它自己**不该**进 CI 的自检列表 ───────────────────────────
    # 理由：它检查的正是"当前 commit 的 CI"，而 CI 自己跑时那个 CI 正在进行中，
    # 放进去必然自相矛盾（永远 pending → 永远红）。
    ci_path = os.path.join(os.path.dirname(__file__), "..",
                           ".github", "workflows", "ci.yml")
    ci = open(ci_path, encoding="utf-8").read()
    in_ci_list = "'verify_ci_green'" in ci
    check("它不在 CI 自检列表里（避免自相矛盾）", not in_ci_list,
          "ci.yml 里不应把 verify_ci_green 列为自检项")

    print()
    if FAIL:
        print(f"失败的用例（{len(FAIL)}）：{', '.join(FAIL)}")
        return 1
    print("全部通过（8 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
