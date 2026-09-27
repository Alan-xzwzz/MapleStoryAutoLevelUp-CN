# -*- coding: utf-8 -*-
"""按键驱动缺失时必须在**启动前**拦住并给出装法 —— 离线自检（2026-09-27 加）。

【为什么需要它】
  实测（issue #2，2026-09-27）用户报「点到 F1，角色完全不操作」。日志里是：
      ERROR: [KeyBoardController] Interception 初始化失败:
             Interception driver was not found or is not installed.
      WARNING: 请确认已安装 Interception 驱动并重启过电脑

  真因是 **Interception 内核驱动没装** —— 而它**不是 pip 装包就有的**，
  要单独下载安装并重启电脑。用户下载的是免安装包，既不知道要装驱动，
  也没有 Python 去 pip install，就这么卡住了。

  而项目原先**没有任何前置检查**：驱动缺失时程序照样启动，用户只能
  对着"角色一动不动"和一句英文报错干猜。

  ⇒ 现在：start_bot 在启动引擎**之前**检查驱动，缺了直接返回 -2，
     界面弹窗给出「装什么、怎么装、装完要重启」。
     把这条钉成用例，谁把它改回"静默放行"就会红。

⚠️ 用例调**真实入口**（driver_status / how_to_install_driver / start_bot 的
   返回值约定），用打桩模拟"驱动未装"，不自己复刻判据。

用法：
    python -m tools.verify_driver_precheck
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.input import InterceptionController as ic      # noqa: E402

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


class _FakeModule:
    """假的 interception 模块：调用就抛「驱动未安装」。"""

    @staticmethod
    def auto_capture_devices(**kw):
        raise Exception(
            "Interception driver was not found or is not installed.\n"
            "Please confirm that it has been installed properly and is added to PATH.")


def main():
    print("=== 按键驱动前置检查·自检 ===")
    real_interception = ic.interception
    real_inited = ic._interception_initialized

    # 【1】驱动未装时，driver_status 要识别出来并给**中文人话**
    print("\n【1】driver_status 识别「驱动未安装」")
    try:
        ic.interception = _FakeModule()
        ic._interception_initialized = False
        ok, detail = ic.driver_status()
        check("返回 ok=False", ok is False, f"ok={ok}")
        check("原因里点明是「内核驱动没装」", "内核驱动" in detail or "驱动" in detail,
              f"detail={detail!r}")
        check("说明「程序装了≠驱动装了」这层区别",
              "单独" in detail or "两回事" in detail, f"detail={detail!r}")
    finally:
        ic.interception = real_interception
        ic._interception_initialized = real_inited

    # 【2】driver_status **不许抛异常**（检查本身失败不能把启动流程带崩）
    print("\n【2】driver_status 不抛异常")
    try:
        ic.interception = _FakeModule()
        ic._interception_initialized = False
        raised = None
        try:
            ic.driver_status()
        except Exception as e:                                # noqa: BLE001
            raised = e
        check("驱动缺失时不抛异常（只返回 False）", raised is None, f"raised={raised}")
    finally:
        ic.interception = real_interception
        ic._interception_initialized = real_inited

    # 【3】安装指引必须**可直接照做**：有下载地址、有命令、有重启提醒
    print("\n【3】安装指引可操作")
    guide = ic.how_to_install_driver()
    check("含官方下载地址", "github.com/oblitum/Interception" in guide)
    check("含安装命令", "install-interception" in guide)
    check("提醒要重启电脑", "重启" in guide)
    check("提醒要管理员运行", "管理员" in guide)
    # 出问题时怎么救 —— 实测有用户装完键鼠异常，必须提前给退路
    check("给了出问题时的恢复办法", "安全模式" in guide or "drivers" in guide,
          "有用户装完键鼠异常过，安装指引必须带这条退路")

    # 【4】调用方约定：start_bot 用 -2 表示"驱动缺失"，且与 -1 区分开
    print("\n【4】start_bot 的返回值约定")
    ctrl_src = open(
        os.path.join(os.path.dirname(__file__), "..", "src", "ui",
                     "AutoBotController.py"), encoding="utf-8").read()
    check("start_bot 里有驱动检查", "driver_status" in ctrl_src)
    check("驱动缺失返回 -2（不与配置失败 -1 混用）", "return -2" in ctrl_src)
    check("驱动检查在 start() 之前（先拦后启）",
          ctrl_src.index("driver_status") < ctrl_src.index("self.auto_bot.start()"),
          "顺序反了就变成「先启动再报错」")
    check("驱动不可用时记 ERROR（日志里也留痕）",
          "按键驱动不可用" in ctrl_src)

    # 【5】界面要处理 -2（否则用户看不到任何提示）
    print("\n【5】界面处理 -2")
    ui_src = open(
        os.path.join(os.path.dirname(__file__), "..", "src", "ui", "ui.py"),
        encoding="utf-8").read()
    check("界面有 ret == -2 分支", "ret == -2" in ui_src)
    check("该分支弹窗展示安装指引", "how_to_install_driver" in ui_src)

    # 【6】★ 视觉链路**不需要**驱动 —— 别让用户白折腾
    #      （2026-09-27 实测：windows-capture 走 Windows 自带的 Graphics Capture
    #       API，opencv 是纯算法库；本机无任何额外抓屏驱动也能成功抓到帧。）
    #      这里做静态核对：抓帧用的是 windows_capture，**没有**任何需要装驱动的
    #      抓屏库（如拦截驱动 / 虚拟显示器之类）。
    print("\n【6】视觉链路不引入需装驱动的依赖")
    cap_src = open(
        os.path.join(os.path.dirname(__file__), "..", "src", "input",
                     "GameWindowCapturor.py"), encoding="utf-8").read()
    check("抓帧用 windows_capture（Windows 自带 Graphics Capture API）",
          "windows_capture" in cap_src)
    # 这些库会引入额外系统组件/驱动，一旦有人换过去，用户就得多装东西
    risky = [lib for lib in ("dxcam", "mss", "pyautogui", "OBS", "virtual_display")
             if lib in cap_src]
    check("没有换成需要额外系统组件的抓屏方案", risky == [], f"发现：{risky}")

    # opencv 只做算法，不该出现在抓屏模块里当"驱动"用
    check("opencv 没有被当成抓屏手段（它只管算法）",
          "cv2" not in cap_src or "VideoCapture" not in cap_src,
          "抓屏模块里出现 cv2.VideoCapture 说明路子变了")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：驱动缺失会在启动前拦住，并给出可照做的安装指引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
