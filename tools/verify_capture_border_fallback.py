#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
verifier: 抓屏「黄框开关」在老系统上不再导致录制器崩溃（issue #4）

背景
----
`windows-capture` 的 `draw_border` 参数一旦传 True/False，库就会去调
`GraphicsCaptureSession.IsBorderRequired`。该属性要 **Windows 10 build 20348 / UAC v12**；
低于此版本库**直接抛异常**（BorderConfigUnsupported），原文：

    Toggling the capture border is not supported by the Graphics Capture API on this platform.

用户机器上表现为「一按录制就崩，提示一句英文」。本脚本固化修复后的契约，
防止以后有人再把它改回无条件 `draw_border=False`。

本脚本**不依赖真实游戏窗口**，也不要求本机系统版本达标 —— 全部用桩（stub）跑，
所以在本机（build 26200）和低版本机器上都能过。

退出码 0=全过，1=有失败项。
'''

import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FAIL = []
PASS = []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}" + (f"  → {detail}" if detail else ""))


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
print("【1】源码契约：draw_border 必须是条件化的，且不能被改回写死")
# ---------------------------------------------------------------------------
cap_src = _src(os.path.join("src", "input", "GameWindowCapturor.py"))

check("主路径按版本给 draw_border（不是写死 False）",
      "draw_border=False if _border_off else None" in cap_src)
check("门槛常量 = 20348（IsBorderRequired 的最低 build）",
      "_DRAW_BORDER_MIN_BUILD = 20348" in cap_src)
check("版本探测走 RtlGetVersion（GetVersionEx 在兼容模式下会撒谎）",
      "RtlGetVersion" in cap_src)

# 库里 draw_border 的默认值必须是 None：None = 不碰边框设置；
# 传 False/True 才会调用 IsBorderRequired。这条是全部推理的前提。
_lib_init = None
try:
    import windows_capture
    _lib_init = open(windows_capture.__file__, encoding="utf-8").read()
except Exception as e:                                         # noqa: BLE001
    print(f"  [WARN] 读不到 windows_capture 源码，跳过库签名检查：{e}")
if _lib_init:
    check("库签名里 draw_border 默认是 None（传 False 才会触发 IsBorderRequired）",
          re.search(r"draw_border:\s*bool\s*\|\s*None\s*=\s*None", _lib_init) is not None)

# ---------------------------------------------------------------------------
print("\n【2】三处抓屏调用点都不能再写死 draw_border=False")
# ---------------------------------------------------------------------------
for rel in [os.path.join("src", "input", "GameWindowCapturor.py"),
            os.path.join("tools", "grab_frame.py"),
            os.path.join("tools", "template_capture.py")]:
    s = _src(rel)
    # 找 WindowsCapture(...) 调用里有没有【裸】的 draw_border=False（后面不跟 if）
    bare = re.findall(r"draw_border=False\s*[,)]", s)
    check(f"{rel}：无硬编码 draw_border=False", not bare, f"发现 {len(bare)} 处")

# ---------------------------------------------------------------------------
print("\n【3】行为：低版本系统上不传边框开关，高版本才传")
# ---------------------------------------------------------------------------
import src.input.GameWindowCapturor as G

_calls = []


class _StubCapture:
    """记录构造时收到的 draw_border。默认不抛异常（成功路径）。"""

    def __init__(self, **kw):
        _calls.append(kw)
        self._kw = kw
        self._handlers = {}

    def event(self, fn):
        return fn

    def start_free_threaded(self):
        return type("Ctl", (), {"stop": lambda self: None})()


_orig_capture = G.WindowsCapture
_orig_find = G.find_game_window_hwnd

try:
    G.WindowsCapture = _StubCapture
    G.find_game_window_hwnd = lambda token: 123456

    # --- 3a. 低版本：探测说"不支持"，就不该传 draw_border ---
    _calls.clear()
    G._supports_draw_border = lambda: False
    cap = G.GameWindowCapturor({"system": {"fps_limit_window_capturor": 60},
                                "game_window": {"title": "x"}})
    check("低版本：未向库传 draw_border（不触发 IsBorderRequired）",
          _calls and _calls[0].get("draw_border") is None,
          f"实际 kwargs={_calls[0] if _calls else None}")
    check("低版本：光标仍然被关掉（这项与边框无关，不受影响）",
          _calls and _calls[0].get("cursor_capture") is False)
    check("低版本：构造成功，不抛异常", cap is not None)

    # --- 3b. 高版本：应正常传 False ---
    _calls.clear()
    G._supports_draw_border = lambda: True
    G.GameWindowCapturor({"system": {"fps_limit_window_capturor": 60},
                          "game_window": {"title": "x"}})
    check("高版本：传 draw_border=False（关掉 Win11 黄框）",
          _calls and _calls[0].get("draw_border") is False,
          f"实际 kwargs={_calls[0] if _calls else None}")

    # --- 3c. 双保险：探测说是高版本、实际却抛 border 异常 → 应自动退回重试 ---
    _calls.clear()
    G._supports_draw_border = lambda: True

    class _FailOnceThenOk(_StubCapture):
        """首次（带 draw_border）在 start 时抛边框异常；退回后（无 draw_border）成功。

        注意异常必须从 start_free_threaded 抛，才是**真实库**的行为
        （库的构造只是存参数，会话在 start 时才建）—— 我们代码里的 try 也只包着 start。
        """

        def start_free_threaded(self):
            if self._kw.get("draw_border") is not None:
                raise Exception(
                    "Capture session threw an exception: Graphics capture error: "
                    "Toggling the capture border is not supported by the "
                    "Graphics Capture API on this platform.")
            return type("Ctl", (), {"stop": lambda self: None})()

    G.WindowsCapture = _FailOnceThenOk
    cap3 = G.GameWindowCapturor({"system": {"fps_limit_window_capturor": 60},
                                "game_window": {"title": "x"}})
    check("双保险：探测失准时也能退回默认设置启动成功（不崩）",
          cap3 is not None and len(_calls) == 2
          and _calls[0].get("draw_border") is False
          and _calls[1].get("draw_border") is None,
          f"调用序列={[(c.get('draw_border')) for c in _calls]}")

    # --- 3d. 异常翻译：必须命中 border 专属文案，且不能错误报成「系统太老要 1809」 ---
    #     直接测翻译函数（模拟"探测失准、异常还是冒上来了"这条路径）
    _border_exc = ("Capture session threw an exception: Graphics capture error: "
                   "Toggling the capture border is not supported by the "
                   "Graphics Capture API on this platform.")
    _t = G.GameWindowCapturor._explain_capture_error(Exception(_border_exc))
    check("异常翻译命中 border 专属文案（含 20348）", "20348" in _t, _t[:120])
    check("异常翻译没有误报成「需要 Windows 10 1809」", "1809" not in _t, _t[:120])

    # --- 3e. 翻译函数：其他异常的老文案不受影响 ---
    check("无关异常仍走「系统太老」分支（没被 border 分支吃掉）",
          "1809" in G.GameWindowCapturor._explain_capture_error(
              Exception("The Graphics Capture API is not supported on this platform")))
    check("hwnd 失效文案仍正常",
          "句柄已经失效" in G.GameWindowCapturor._explain_capture_error(
              Exception("Failed to convert item to `GraphicsCaptureItem`")))

finally:
    G.WindowsCapture = _orig_capture
    G.find_game_window_hwnd = _orig_find
    try:
        del G._supports_draw_border          # 还原被我们替换掉的模块级函数
    except AttributeError:
        pass
    import importlib
    importlib.reload(G)                      # 彻底还原，避免影响同进程其它用例

# ---------------------------------------------------------------------------
print("\n【4】本机真实环境：版本探测能跑通且结论自洽")
# ---------------------------------------------------------------------------
import importlib as _il
G = _il.reload(G)
_b = G._windows_build()
check("能取到本机 build 号（非 0）", _b > 0, f"build={_b}")
check("探测结论与版本号一致",
      G._supports_draw_border() == (_b >= G._DRAW_BORDER_MIN_BUILD),
      f"build={_b}, 探测={G._supports_draw_border()}")
print(f"  [信息] 本机 build={_b}（门槛 {G._DRAW_BORDER_MIN_BUILD}）"
      f"→ {'支持' if _b >= G._DRAW_BORDER_MIN_BUILD else '不支持'}关闭黄框")

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项：{FAIL}")
    sys.exit(1)
print(f"全部通过（{len(PASS)} 项）：老系统上录制器不再因黄框开关崩溃。")
sys.exit(0)
