# -*- coding: utf-8 -*-
"""「我这边有、用户那边可能没有」的发布前体检（2026-09-27 加）。

【为什么需要它】
  本项目已经因为这类差异连踩三次（都在 issue #2）：
    ① 窗口尺寸：我这边 1366x768 能跑，用户那边窗口更小 → 一帧都抓不到；
    ② 按键驱动：我这边装了 Interception，用户没装 → 角色一动不动；
    ③ 打包遗漏：我改了源码却忘了重新打包 → 用户拿到的还是旧程序。

  这类问题的共同点是「**开发者环境成立、用户环境不成立**」，
  而且在开发机上**永远不会复现** —— 只能靠发布前主动对照。

  ⇒ 把对照做成可执行脚本：拿**打包产物**当"用户环境"，逐条查
     「程序引用到的东西，产物里到底有没有」。

用法：
    python -m tools.verify_user_env_gap
    python -m tools.verify_user_env_gap --dist dist/冒险岛自动练级
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

FAIL: list = []
WARN: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def warn(name, detail=""):
    print(f"  [WARN] {name}{(' — ' + detail) if detail else ''}")
    WARN.append(name)


def _pyz_modules(exe_path):
    """从打包 exe 内嵌的 PYZ 取出模块名集合；失败返回 None。"""
    try:
        import tempfile
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
        arc = CArchiveReader(exe_path)
        tmp = os.path.join(tempfile.gettempdir(), "_gap_probe.pyz")
        with open(tmp, "wb") as f:
            f.write(arc.extract("PYZ.pyz"))
        z = ZlibArchiveReader(tmp)
        return set(z.toc.keys())
    except Exception:                                         # noqa: BLE001
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist/冒险岛自动练级",
                    help="打包产物目录（默认 dist/冒险岛自动练级）")
    args = ap.parse_args(argv)

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dist = os.path.join(repo, args.dist)
    print("=== 发布前体检：用户环境 vs 我的环境 ===")
    print(f"产物目录：{dist}")

    if not os.path.isdir(dist):
        print(f"\n[FAIL] 产物目录不存在：{dist}\n"
              f"       先按流程打包（打包_用户版.bat 或 PyInstaller 命令）再跑本检查。")
        return 1

    exe = None
    for f in os.listdir(dist):
        if f.endswith(".exe"):
            exe = os.path.join(dist, f)
            break
    if exe is None:
        print("\n[FAIL] 产物目录里没有 exe")
        return 1

    pyz = _pyz_modules(exe)
    internal = os.path.join(dist, "_internal")
    have_dirs = set(os.listdir(internal)) if os.path.isdir(internal) else set()
    # _internal 下还可能有子目录（如 pywin32 的模块落在 _internal/win32/ 里），
    # 一并收进来，否则会误报"缺失"（2026-09-27 实测踩过：win32gui 在
    # _internal/win32/win32gui.pyd，被按顶层目录名找不到）。
    nested = set()
    for d in have_dirs:
        sub = os.path.join(internal, d)
        if os.path.isdir(sub):
            try:
                nested.update(os.listdir(sub))
            except OSError:
                pass

    def _in_dist(mod):
        """产物里有没有这个模块（PYZ / 顶层 / 子目录 三种形态都算）。"""
        if pyz is not None and any(n == mod or n.startswith(mod + ".") for n in pyz):
            return True
        for name in (mod, mod + ".py", mod + ".pyd", mod + ".dll"):
            if name in have_dirs or name in nested:
                return True
        # 模块名可能带包前缀（win32gui 属于 win32 包）
        return any(n.startswith(mod) for n in have_dirs | nested)

    # 【1】运行时依赖：打包产物里有没有
    print("\n【1】运行时要用的库，产物里都在吗")
    # 纯 Python 包在 PYZ 里；带二进制扩展的以目录/文件形式落在 _internal
    deps = [
        ("PySide6", "目录", "界面框架"),
        ("cv2", "目录", "图像识别（OpenCV）"),
        ("numpy", "目录", "数值运算"),
        ("windows_capture", "目录", "抓帧"),
        ("PIL", "两者", "中文绘制"),
        ("yaml", "PYZ", "配置读写"),
        ("ruamel", "PYZ", "带注释的 yaml"),
        ("pynput", "PYZ", "按键监听"),
        ("pygetwindow", "PYZ", "前台窗口判断"),
        ("win32gui", "目录", "Win32 API"),
        ("interception", "PYZ", "按键注入"),
    ]
    for mod, where, why in deps:
        ok = _in_dist(mod)
        check(f"{mod:18}（{why}）", ok,
              "" if ok else f"产物里既不在 PYZ 也不在 _internal —— 用户会 ModuleNotFoundError")

    # 【2】出厂资源：程序启动/首次使用就要用的
    print("\n【2】出厂资源齐不齐")
    need = {
        "config/config_default.yaml": "基准配置（缺了直接起不来）",
        "config/config_custom.yaml": "用户配置回退值",
        "config/config_data.yaml": "地图/怪物登记表",
        "media/icon.png": "界面图标",
    }
    for rel, why in need.items():
        p = os.path.join(dist, rel.replace("/", os.sep))
        check(f"{rel:30}（{why}）", os.path.isfile(p),
              "" if os.path.isfile(p) else "缺少这个文件")

    # 【3】开发用工具**不该**混进用户包（省体积 + 避免误导）
    print("\n【3】开发用工具不该进用户包")
    if pyz is not None:
        dev_tools = ["tools.mob_maker", "tools.grab_frame", "tools.probe_live",
                     "tools.render_ui_preview", "tools.test_hotkey"]
        leaked = [t for t in dev_tools if t in pyz]
        # 这些工具用了开发期才有的东西（requests 等），进包反而会让用户点了报错
        for t in dev_tools:
            check(f"{t} 未进包（开发用）", t not in pyz,
                  "" if t not in pyz else "它在包里，用户点了可能报缺依赖")
        if leaked:
            warn("有开发工具进了包", str(leaked))

    # 【4】界面用到的子工具**必须**进包（漏一个用户点了就报错）
    print("\n【4】界面自调用的子工具都在吗")
    if pyz is not None:
        for t in ("tools.routeRecorder", "tools.calibrate_nametag",
                  "tools.template_capture", "tools.diagnose",
                  "tools.mob_template_qa", "tools.measure_window",
                  "tools.homeRouteDrawer"):
            check(f"{t} 已进包", t in pyz,
                  "" if t in pyz else "用户点对应按钮会 No module named 且无法自救")

    # 【5】配置里写死的「我的机器」痕迹
    print("\n【5】出厂配置没有我的机器痕迹")
    cfg = os.path.join(dist, "config", "config_default.yaml")
    if os.path.isfile(cfg):
        text = open(cfg, encoding="utf-8", errors="replace").read()
        for bad, why in ((r"F:\\", "我的盘符"), (r"G:\\", "我的盘符"),
                         ("Administrator", "我的用户名")):
            check(f"不含「{why}」", bad not in text)
    else:
        check("config_default.yaml 存在", False)

    # 【6】需要额外系统组件的东西 —— 必须"在文档里说过"
    print("\n【6】需要用户额外准备的东西，文档里说了吗")
    readme_user = os.path.join(repo, "README_用户版.md")
    text = open(readme_user, encoding="utf-8").read() if os.path.isfile(readme_user) else ""
    for kw, why in (
            ("Interception", "按键驱动要单独装"),
            ("驱动", "同上"),
            ("管理员", "必须管理员运行"),
            ("窗口模式", "全屏抓不到画面"),
    ):
        check(f"用户版文档提到「{kw}」", kw in text, why)

    # 【7】本机跑得通、但不代表用户环境 —— 只提醒，不算失败
    print("\n【7】只在开发机成立、需人工确认的项")
    warn("游戏窗口尺寸 / 标题栏高度是实测值",
         "config 的 game_window.size / title_bar_height 来自开发机，"
         "用户缩放不同就对不上 —— 已提供「一键按当前窗口大小改配置」与 measure_window，"
         "发布说明里应提醒")
    warn("Interception 驱动本机已装",
         "开发机装了驱动，用户不一定 —— 已做启动前检查与安装指引（v1.0.6+）")

    print()
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print(f"✅ 全部通过（另有 {len(WARN)} 项需人工确认，见上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
