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
                              _all_release_tags, build_apply_bat,
                              changed_files, deps_changed)

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

    # 【4】应用补丁.bat 必须做对四件事
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
    # ★ 关键：必须从子目录取文件，否则「解压即覆盖」会让备份失去意义
    check("★ 从子目录 _补丁文件 取新文件（防解压即覆盖）",
          "_补丁文件" in bat and "%~dp0_补丁文件" in bat)
    check("★ 覆盖前先 mkdir 目标父目录（旧版可能没有 config/）",
          "mkdir" in bat)
    check("★ 子目录缺失时明确报错并中止", "没有解压完整" in bat)

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
            # ⚠️ 补丁内的文件在子目录 `_补丁文件/` 下（防止解压即覆盖，
            #    见 make_patch 里的详细说明），所以这里按 basename 判断。
            bases = [n.rsplit("/", 1)[-1] for n in names]
            check("补丁含 exe", "冒险岛自动练级.exe" in bases, f"names={names}")
            check("补丁含 patch.json", "patch.json" in bases)
            check("补丁含 应用补丁.bat", "应用补丁.bat" in bases)
            check("★ 补丁文件在子目录里（不与用户程序同级）",
                  any(n.startswith("_补丁文件/") for n in names), f"names={names}")
            check("★ 补丁根目录没有裸的 exe（防解压即覆盖）",
                  "冒险岛自动练级.exe" not in names,
                  f"根目录条目={names}")
            leaked = [n for n in names
                      if n.endswith("config/config_custom.yaml")
                      or n.endswith("config/config_data.yaml")
                      or "/log/" in n or "/minimaps/" in n
                      or "/monster/" in n or "/nametag/" in n]
            check("补丁**没有**混进用户数据", leaked == [], f"泄漏={leaked}")
            check("补丁不带 .py 源文件（业务代码在 exe 里）",
                  not any(n.endswith(".py") for n in names),
                  f"py文件={[n for n in names if n.endswith('.py')]}")
            check("中文条目带 UTF-8 标志位",
                  all(info.flag_bits & 0x0800
                      for info in zf.infolist()
                      if any(ord(c) > 127 for c in info.filename)))

    # 【6】通用补丁：一份要能覆盖所有旧版本
    #      （用户 2026-09-27 提出「1.0.1 / 1.0.0 的用户装不了」）
    print("\n【6】通用补丁 —— 一份覆盖所有旧版本")
    try:
        tags = _all_release_tags(repo)
    except RuntimeError:
        tags = []
    if not tags:
        print("        （取不到 git 标签，跳过 —— 不算失败）")
    else:
        print(f"        仓库版本标签：{tags}")
        check("能列出 v* 标签且含多个版本", len(tags) >= 2, f"tags={tags}")
        # 每个旧版本到最新版，依赖都必须没变 —— 否则通用补丁不安全
        target = tags[-1]
        older = [t for t in tags if t != target]
        baddeps = {t: deps_changed(t, target, repo) for t in older}
        leaked_deps = {t: v for t, v in baddeps.items() if v}
        check(f"所有旧版本 → {target} 依赖都没变（通用补丁的前提）",
              leaked_deps == {}, f"变了依赖的：{leaked_deps}")

    # 【7】通用补丁的 bat 必须列出适用版本，别让用户猜自己能不能装
    print("\n【7】通用补丁的 应用补丁.bat 文案")
    bat_u = build_apply_bat("v0.0", "v1.0.3", fake,
                            applies_to=["v1.0", "v1.0.1", "v1.0.2"])
    check("列出适用版本 v1.0", "v1.0" in bat_u)
    check("列出适用版本 v1.0.1", "v1.0.1" in bat_u)
    check("列出适用版本 v1.0.2", "v1.0.2" in bat_u)
    check("仍保留备份与关闭程序提示",
          "_backup_补丁前" in bat_u and "占用" in bat_u)

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过：补丁不碰用户数据、exe 必换、依赖变化能被拦住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
