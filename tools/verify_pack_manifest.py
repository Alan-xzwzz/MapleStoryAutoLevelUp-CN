# -*- coding: utf-8 -*-
"""「界面用到的子工具」必须都在打包清单里 —— 离线自检（2026-09-27 加）。

【为什么需要它】
  打包版（免安装包）里没有 Python、也没有 tools/ 源码。界面调用子工具走的是
  `exe --tool tools.xxx` 自调用（见 src/main.py 的 _run_tool），而**只有**被
  `--hidden-import` 列进打包脚本的模块才在包里。

  漏一个的后果：普通用户点那个按钮 → `No module named tools.xxx`，而他**没有
  Python、敲不了命令行**，等于功能直接废掉且无法自救。
  真实教训（2026-09-27）：issue #2 的修复提示里让用户跑
  `python -m tools.measure_window --write-config`，而 measure_window
  **既没打包、用户也没 Python** —— 提示等于把问题原样丢回给用户。

  ⇒ 把「界面引用 ⊆ 打包清单」钉成用例，谁加了新工具忘了打包就会红。

⚠️ 用例读**真实文件**（src/ui/ui.py 与两个打包脚本），不维护一份手抄清单 ——
   手抄清单一飘，这个自检就变成摆设。

用法：
    python -m tools.verify_pack_manifest
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


# 界面在自己进程里直接 import 的工具（不是子进程自调用），
# 它们**不需要** hidden-import —— PyInstaller 会顺着 import 图自动带上。
IN_PROCESS_OK = {"tools.homeRouteDrawer"}


def tools_referenced_by_ui(ui_path):
    """从 src/ui/ui.py 里找出所有以子进程方式调用的 tools.* 模块。

    只看**调用**（self._tool_argv('tools.x' / self._spawn_console(['tools.x'），
    不把注释与文档字符串里的示例算进来 —— 否则会误报。
    """
    src = open(ui_path, encoding="utf-8").read()
    found = set()
    # 逐行扫，跳过纯注释行与 docstring 里的说明
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        # self._tool_argv('tools.xxx', ...)
        for m in re.finditer(r"_tool_argv\(\s*['\"](tools\.[\w\.]+)['\"]", line):
            found.add(m.group(1))
        # self._spawn_console(['tools.xxx', ...]) 或 self._spawn_console('tools.xxx')
        for m in re.finditer(r"_spawn_console\(\s*\[\s*['\"](tools\.[\w\.]+)['\"]", line):
            found.add(m.group(1))
        for m in re.finditer(r"_spawn_console\(\s*['\"](tools\.[\w\.]+)['\"]", line):
            found.add(m.group(1))
    return found


def hidden_imports_of(bat_path):
    """从打包 bat 里取 --hidden-import= 列表。"""
    if not os.path.isfile(bat_path):
        return None
    src = open(bat_path, encoding="utf-8", errors="replace").read()
    return set(re.findall(r"--hidden-import=([\w\.]+)", src))


def main():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ui_path = os.path.join(repo, "src", "ui", "ui.py")
    print("=== 打包清单自检：界面用到的子工具必须都打进去 ===")

    if not os.path.isfile(ui_path):
        print(f"  [FAIL] 找不到 {ui_path}")
        return 1

    used = tools_referenced_by_ui(ui_path)
    print(f"\n【1】界面以子进程方式调用的工具（{len(used)} 个）")
    for t in sorted(used):
        print(f"        {t}")
    check("能扫出界面引用的工具", len(used) >= 4, f"扫到 {len(used)} 个")

    # 【2】两个打包脚本都必须覆盖这些工具
    for bat in ("打包_用户版.bat", "打包_个人版.bat"):
        p = os.path.join(repo, bat)
        print(f"\n【2】{bat}")
        hi = hidden_imports_of(p)
        if hi is None:
            check(f"{bat} 存在", False, f"找不到 {p}")
            continue
        check(f"{bat} 能解析出 hidden-import", len(hi) >= 5, f"共 {len(hi)} 个")
        missing = sorted(t for t in used if t not in hi)
        check(f"{bat} 覆盖了界面用到的全部工具", missing == [],
              f"漏了：{missing}（用户点了会 No module named，且他没有 Python 无法自救）"
              if missing else f"已覆盖 {len(used)} 个")

    # 【3】专项：measure_window 必须在列
    #     它是给「没有 Python 的打包版用户」兜底的尺寸自助修正，
    #     恰恰最不能漏（2026-09-27 的教训）。
    print("\n【3】measure_window 专项（给无 Python 用户的兜底入口）")
    for bat in ("打包_用户版.bat", "打包_个人版.bat"):
        hi = hidden_imports_of(os.path.join(repo, bat)) or set()
        check(f"{bat} 含 tools.measure_window",
              "tools.measure_window" in hi)

    # 【4】反向：打包脚本里的 hidden-import 应确实存在于 tools/ 目录
    #      （防打字错误 —— 写错一个名字，PyInstaller 不报错但功能缺失）
    print("\n【4】打包脚本里的名字必须真实存在")
    for bat in ("打包_用户版.bat", "打包_个人版.bat"):
        hi = hidden_imports_of(os.path.join(repo, bat)) or set()
        bad = []
        for m in hi:
            rel = m.replace(".", os.sep) + ".py"
            if not os.path.isfile(os.path.join(repo, rel)):
                bad.append(m)
        check(f"{bat} 的 hidden-import 都能在磁盘上找到", bad == [],
              f"不存在：{bad}" if bad else f"{len(hi)} 个全部存在")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：界面用到的子工具都已打进包，名字也都对得上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
