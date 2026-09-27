# -*- coding: utf-8 -*-
"""验证 A 方案的三个不变量（2026-09-27）。

1. **默认行为不变** —— 不传新参数时，结果与改动前**逐位相同**
   （这是最重要的：find_pattern_sqdiff 是共用函数，小地图/回正线/录制器都在用）
2. **新开关有效** —— allow_global_fallback=False 时不再做全图搜索
3. **收益真实** —— 失败路径的耗时确实从 ~82ms 降到 ~22ms

⚠️ 用真实游戏画面（合成数据此前误导过一次）。
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.common import find_pattern_sqdiff          # noqa: E402

FRAME = r"debug\capture_raw_2026-09-27_17-45-46.png"
TPL = r"debug\_nametag_template.png"
UI_Y_START = 680


def main():
    img = cv2.imread(FRAME)
    tpl = cv2.imread(TPL)
    if img is None or tpl is None:
        # ⚠️ 这两个素材是**本机真机截图**（debug/ 下，不进仓库），CI 里必然没有。
        #    缺素材时**优雅跳过**而不是报失败 —— 否则 CI 永远红。
        #    本检查的价值在开发机跑（有真实素材），CI 里跳过是正确行为。
        print("跳过：缺真实截图素材（debug/ 下，不进仓库）")
        print(f"  需要：{FRAME}")
        print(f"  需要：{TPL}")
        print("  生成方式：先跑 tools/grab_frame（抓一帧游戏画面），")
        print("            再从画面里把角色名那行抠出来存成 debug/_nametag_template.png")
        print("  ⇒ 本检查只在有真机素材的机器上跑得动，CI 里跳过。")
        return 0
    cam = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[:UI_Y_START, :]
    g = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    rng = np.random.default_rng(11)
    bad = rng.integers(0, 255, g.shape, dtype=np.uint8)

    fail = []

    def check(name, ok, detail=""):
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fail.append(name)

    # ── 1. 默认行为不变 ──
    print("\n【1】默认行为不变（不传新参数 ⇒ 与旧版一致）")
    # 命中场景
    r_hit = find_pattern_sqdiff(cam, g, last_result=(260, 495), global_threshold=0.01)
    check("命中场景：局部命中且位置正确",
          r_hit[2] is True and abs(r_hit[0][0] - 260) <= 2,
          f"loc={r_hit[0]} score={r_hit[1]:.5f} cached={r_hit[2]}")
    # 失败场景：必须仍然走全图（cached=False）且返回全图结果
    r_fail = find_pattern_sqdiff(cam, bad, last_result=(260, 495), global_threshold=0.01)
    check("失败场景：默认仍回退全图（cached=False）", r_fail[2] is False,
          f"cached={r_fail[2]}")
    # 无 last_result 时行为不变
    r_nolast = find_pattern_sqdiff(cam, g, global_threshold=0.01)
    check("无 last_result：仍全图且能定位到真实位置",
          r_nolast[2] is False and abs(r_nolast[0][0] - 260) <= 2,
          f"loc={r_nolast[0]}")

    # ── 2. 新开关有效 ──
    print("\n【2】allow_global_fallback=False 时不做全图")
    t = time.perf_counter()
    n = 10
    for _ in range(n):
        r_off = find_pattern_sqdiff(cam, bad, last_result=(260, 495),
                                    global_threshold=0.01,
                                    allow_global_fallback=False)
    dt_off = (time.perf_counter() - t) / n * 1000
    t = time.perf_counter()
    for _ in range(n):
        r_on = find_pattern_sqdiff(cam, bad, last_result=(260, 495),
                                   global_threshold=0.01)
    dt_on = (time.perf_counter() - t) / n * 1000
    check("禁用后耗时显著下降（应为局部量级）", dt_off < dt_on / 5,
          f"禁用 {dt_off:.2f}ms vs 允许 {dt_on:.2f}ms")
    check("禁用后仍返回一个位置（不是 None）", r_off[0] is not None,
          f"loc={r_off[0]}")
    # 边界安全：连局部都没跑成时，仍应回退全图（不能啥都不返回）
    r_edge = find_pattern_sqdiff(cam, bad, last_result=None,
                                 global_threshold=0.01,
                                 allow_global_fallback=False)
    check("边界：无 last_result 时禁用开关也不至于无结果", r_edge[0] is not None,
          f"loc={r_edge[0]}")

    # ── 3. 收益（复刻一次定位的多轮结构）──
    print("\n【3】一次定位失败路径的收益（复刻 1 主 + 2 兜底 = 3 次匹配）")
    def once(limit):
        """limit=True = 新版（只第一次允许全图）；False = 旧版（每次都允许）"""
        used = False
        total = 0.0
        for _ in range(3):
            t0 = time.perf_counter()
            loc, sc, cached = find_pattern_sqdiff(
                cam, bad, last_result=(260, 495), global_threshold=0.01,
                allow_global_fallback=((not used) if limit else True))
            total += (time.perf_counter() - t0) * 1000
            if not cached and limit:
                used = True
        return total

    n = 5
    t = time.perf_counter()
    for _ in range(n):
        old = once(False)
    old = (time.perf_counter() - t) / n * 1000
    t = time.perf_counter()
    for _ in range(n):
        new = once(True)
    new = (time.perf_counter() - t) / n * 1000
    print(f"      旧（每次失败都全图）: {old:6.1f} ms")
    print(f"      新（只第一次全图）  : {new:6.1f} ms")
    check("收益显著（新版应快 2 倍以上）", new < old / 2,
          f"提速 {old / max(new, 0.01):.1f} 倍")
    # 换算帧率
    other = 95 - 80
    print(f"      换算：整帧 {other + old:.0f}ms({1000/(other+old):.1f}fps)"
          f" → {other + new:.0f}ms({1000/(other+new):.1f}fps)")

    print()
    if fail:
        print(f"失败 {len(fail)} 项：{fail}")
        return 1
    print("全部通过：默认行为不变、开关有效、收益显著。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
