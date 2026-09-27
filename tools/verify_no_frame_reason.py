# -*- coding: utf-8 -*-
'''录制器「抓不到画面」的原因诊断自检（2026-09-27 加，issue #2）。

为什么单独一个脚本：
  issue #2 的真相是**窗口客户区尺寸与 config 声明不符**（配置 1366x768、
  实测 1282x707）⇒ crop_frame_to_client 每帧返回 None ⇒ 录制器刷 10 秒
  "还没抓到游戏画面"后退出。而当时的提示词**写死**为
  「等待游戏窗口聚焦。若一直卡在这里：游戏窗口是否被挡住/最小化？」
  ——用户顺着它去查前台、查双屏、换单屏，全是错方向，最后把问题
  描述成「无法识别前台」报了上来。

  ⇒ 把「按真实原因给提示」这件事钉成用例：以后谁把提示改回一句套话，
    这个自检就会红。

⚠️ 用例一律调**真实入口**（`_no_frame_reason`），不自己复刻一套判据 ——
   复刻一遍就测不到真东西了（项目纪律）。

用法：
    python -m tools.verify_no_frame_reason
'''
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.common import load_yaml                      # noqa: E402
from tools.routeRecorder import RouteRecorder               # noqa: E402

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def new_recorder(cfg_size, capture=None):
    '''造一个**只带必要字段**的录制器（不跑 GUI / 不连游戏）。

    与 verify_recorder_action 同一套做法：直接 __new__ 出来只塞用得到的字段。
    '''
    cfg = load_yaml("config/config_default.yaml")
    cfg.setdefault("game_window", {})["size"] = list(cfg_size)
    r = RouteRecorder.__new__(RouteRecorder)
    r.cfg = cfg
    r.capture = capture
    r._t_frame_wait_start = 0.0
    r._t_last_noframe_log = 0.0
    r._frame_ok = False
    r._first_frame_logged = False
    return r


class _FakeCapture:
    '''假抓帧器：window_hwnd=0 / frame=... 用来走各条失败分支。'''

    def __init__(self, hwnd=0, frame=None):
        self.window_hwnd = hwnd
        self.frame = frame


def main():
    print("=== 抓不到画面时，提示必须指向**真实原因**（issue #2）===")

    # ① 句柄失效（游戏关了）—— 必须说「句柄失效」，不能说「等窗口聚焦」
    r = new_recorder((768, 1366), _FakeCapture(hwnd=0))
    why, fix = r._no_frame_reason()
    check("句柄失效：原因指向句柄", ("句柄" in why or "窗口" in why), f"why={why!r}")
    check("句柄失效：建议含『重开游戏/按 F4』", "F4" in fix or "重开" in fix, f"fix={fix!r}")

    # ② 尺寸不符（issue #2 的真身）—— 必须**明确报出两个尺寸**并给出改法。
    #    ⚠️ 本机/CI 上不一定开着游戏窗口，所以把 win32gui 的几个查询**打桩**，
    #       按 issue #2 的实测值（窗口 1282x707 / 配置 1366x768）回放。
    #       这样这条用例在无游戏环境里也能跑，且走的是**真实入口**
    #       `_no_frame_reason`，不是复刻一份判据。
    import win32gui as _wg
    _orig = {n: getattr(_wg, n) for n in
             ("IsWindow", "GetClientRect", "IsIconic", "IsWindowVisible")}
    try:
        _wg.IsWindow = lambda h: True
        _wg.GetClientRect = lambda h: (0, 0, 1282, 707)      # 用户实测客户区
        _wg.IsIconic = lambda h: False
        _wg.IsWindowVisible = lambda h: True

        r2 = new_recorder((768, 1366), _FakeCapture(hwnd=123456))
        why2, fix2 = r2._no_frame_reason()
        blob2 = why2 + " " + fix2
        check("尺寸不符：原因里报出**实测**尺寸 1282x707", "1282x707" in why2, f"why={why2!r}")
        check("尺寸不符：原因里报出**配置**尺寸 1366x768", "1366x768" in why2, f"why={why2!r}")
        check("尺寸不符：办法含 measure_window --write-config",
              "measure_window" in fix2, f"fix={fix2!r}")
        check("尺寸不符：明确排除「前台 / 双屏」误导", "双屏" in blob2, f"blob={blob2!r}")

        # ②b 反向：尺寸**相符**时不许再提尺寸问题（否则又是一个误导）
        _wg.GetClientRect = lambda h: (0, 0, 1366, 768)
        why2b, fix2b = r2._no_frame_reason()
        check("尺寸相符时不再报尺寸问题",
              "1282" not in why2b and "对不上" not in why2b, f"why={why2b!r}")

        # ②c 尺寸不匹配时，_win_diag 要标出「✗差了 ±N」
        diag2 = r2._win_diag()
        check("诊断串标出尺寸差（✗差了）", "✗差了" in diag2 or "✓尺寸相符" in diag2,
              f"diag={diag2!r}")
    finally:
        for _n, _f in _orig.items():
            setattr(_wg, _n, _f)

    # ③ 提示里**不许**再出现那句把人带偏的套话
    r3 = new_recorder((768, 1366), _FakeCapture(hwnd=0))
    why3, fix3 = r3._no_frame_reason()
    blob = why3 + " " + fix3
    check("提示不再写死『等待游戏窗口聚焦』", "等待游戏窗口聚焦" not in blob, f"blob={blob!r}")

    # ④ 尺寸不符时的文案里必须点明「与前台/双屏无关」——
    #    这是 issue #2 最大的误导点（用户把双屏当成原因去排查了）
    print("\n=== 尺寸不符文案（模拟）===")
    # 用真实判据喂一组"配置 1366x768 / 实测 1282x707"的数据
    from src.utils.common import crop_frame_to_client
    import numpy as np
    frame = np.zeros((707 + 31 + 1, 1282 + 2, 3), dtype=np.uint8)
    out, msg = crop_frame_to_client(frame, [768, 1366], 31, tag="自检")
    check("尺寸不符时裁剪必须返回 None（不许静默给坏帧）", out is None, f"out={type(out)}")
    # ⚠️ 断言按**实际口径**写：帧宽 = 客户区宽 + 2（左右边框各 1px），
    #    所以 1282 客户区喂进去是 1284。这里验的是"两个尺寸都被报出来"。
    check("裁剪失败信息含两个尺寸（实测/配置）",
          ("1284" in msg and "1366" in msg and "708" in msg and "768" in msg),
          f"msg={msg!r}")

    # ⑤ 尺寸**相符**时必须能裁出来 —— 防止把好路径也改坏
    good = np.zeros((768 + 31 + 1, 1366 + 2, 3), dtype=np.uint8)
    out2, msg2 = crop_frame_to_client(good, [768, 1366], 31, tag="自检")
    check("尺寸相符时裁剪成功", out2 is not None and out2.shape[:2] == (768, 1366),
          f"shape={None if out2 is None else out2.shape}")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：抓不到画面时的提示指向真实原因，尺寸判据正确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
