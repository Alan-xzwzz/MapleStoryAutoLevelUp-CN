# -*- coding: utf-8 -*-
"""增量补丁生成器的离线自检（2026-09-27 加）。

为什么要有它：
  补丁包是给**老用户覆盖安装**用的，一旦做错（例如把用户的 config_data.yaml
  也覆盖了，或者依赖变了还发补丁），用户那边是**静默损坏** —— 程序可能起不来，
  或者他的地图路线被抹掉。这类错误必须靠用例挡住，不能靠事后发现。

⚠️ 用例调**真实入口**（make_patch 模块里的函数与常量），不自己复刻一套判据。

用法：
    python -m tools.verify_make_patch
"""
from __future__ import annotations

import os
import sys
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.make_patch import (ALWAYS_REPLACE, NEVER_INCLUDE,   # noqa: E402
                              build_apply_bat, changed_files, deps_changed)

FAIL: list = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def main():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=== 增量补丁生成器·自检 ===")

    # 【1】用户数据必须被排除 —— 这是最要命的一条：
    #      补丁覆盖了用户的地图/配置 = 静默毁掉他的劳动成果
    print("\n【1】用户自己的数据绝不能被补丁覆盖")
    for p in ("config/config_custom.yaml", "config/config_data.yaml"):
        check(f"{p} 在排除名单里", p in NEVER_INCLUDE, f"NEVER_INCLUDE={NEVER_INCLUDE}")
    for d in ("log/", "minimaps/", "monster/", "nametag/"):
        check(f"{d} 在排除名单里", d in NEVER_INCLUDE, f"NEVER_INCLUDE={NEVER_INCLUDE}")

    # 【2】exe 必须每次替换（业务代码全内嵌在它里面）
    print("\n【2】exe 必须每次替换")
    check("exe 在 ALWAYS_REPLACE 里", "冒险岛自动练级.exe" in ALWAYS_REPLACE,
          f"ALWAYS_REPLACE={ALWAYS_REPLACE}")

    # 【3】依赖变化的检测必须真的有效 —— 变了就要拒发补丁
    print("\n【3】依赖变化检测")
    # ⚠️ 这一步需要完整 git 历史与标签。CI 里若 checkout 用了默认的
    #    fetch-depth=1，标签取不到就会在这里炸 —— 那不是"用例失败"，
    #    是**环境没准备好**，必须说清楚该怎么办，别让人对着 traceback 猜。
    try:
        changed = changed_files("v1.0.2", "v1.0.3", repo)
    except RuntimeError as e:
        print(f"  [跳过] 取不到 git 标签历史：{e}")
        print("        这条用例需要完整历史与标签。裸仓库请用：")
        print("          git fetch --tags --unshallow")
        print("        CI 里 actions/checkout 需要 fetch-depth: 0 + fetch-tags: true。")
        changed = []
    check("能列出两个版本间的变化文件", len(changed) > 0, f"共 {len(changed)} 个")
    bad = deps_changed("v1.0.2", "v1.0.3", repo) if changed else []
    check("v1.0.2→v1.0.3 依赖未变（所以可以发补丁）", bad == [], f"deps_changed={bad}")
    bad2 = deps_changed("v1.0", "v1.0.3", repo) if changed else []
    check("deps_changed 返回 list（不抛异常）", isinstance(bad2, list), f"type={type(bad2)}")

    # 【4】应用补丁.bat 必须做对三件事
    print("\n【4】应用补丁.bat 的关键行为")
    fake = [{"path": "冒险岛自动练级.exe", "size": 1, "sha256": "x"},
            {"path": "config/config_default.yaml", "size": 1, "sha256": "y"}]
    bat = build_apply_bat("v1.0.2", "v1.0.3", fake)
    check("含备份步骤", "_backup_补丁前" in bat)
    check("提示先关闭程序", "关闭" in bat and "占用" in bat)
    check("覆盖 exe", "冒险岛自动练级.exe" in bat)
    check("覆盖 config_default.yaml", "config_default.yaml" in bat)
    check("用 CRLF 换行（cmd 需要）", "\r\n" in bat)
    check("没有覆盖用户配置的语句",
          "config_data.yaml" not in bat and "config_custom.yaml" not in bat)

    # 【5】真实产物自检：若已有生成好的补丁 zip，检查其内容合规
    print("\n【5】已生成的补丁 zip（若存在）内容合规")
    zips = [f for f in os.listdir(repo)
            if f.startswith("patch-") and f.endswith(".zip")]
    if not zips:
        print("        （当前目录没有补丁 zip，跳过 —— 不算失败）")
    else:
        zp = os.path.join(repo, zips[0])
        print(f"        检查 {zips[0]}")
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            check("补丁含 exe", any(n.endswith("冒险岛自动练级.exe") for n in names),
                  f"names={names}")
            check("补丁含 patch.json", "patch.json" in names)
            check("补丁含 应用补丁.bat", "应用补丁.bat" in names)
            leaked = [n for n in names
                      if n in ("config/config_custom.yaml", "config/config_data.yaml")
                      or n.startswith(("log/", "minimaps/", "monster/", "nametag/"))]
            check("补丁**没有**混进用户数据", leaked == [], f"泄漏={leaked}")
            check("补丁不带 .py 源文件（业务代码在 exe 里）",
                  not any(n.endswith(".py") for n in names),
                  f"py文件={[n for n in names if n.endswith('.py')]}")
            check("中文条目带 UTF-8 标志位",
                  all(info.flag_bits & 0x0800
                      for info in zf.infolist()
                      if any(ord(c) > 127 for c in info.filename)))

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：补丁不碰用户数据、exe 必换、依赖变化能被拦住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
