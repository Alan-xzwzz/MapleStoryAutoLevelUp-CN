# -*- coding: utf-8 -*-
"""发版前的 CI 门禁：最近一次 CI 必须是绿的，否则拦住不让发（2026-09-27 加）。

【为什么要有它 —— 一次真实的翻车】
  2026-09-27 发 v1.1.0-beta.1 时：本地 13 项自检全绿 → 直接打包发版。
  发完之后 CI 才跑完，**红了**：
      verify_bars_potions 的用例 15 挂了 —— 它依赖 config/config_data.yaml，
      而那是用户数据（录路线才生成、被 .gitignore 挡住），
      CI 全新检出没有它 → FileNotFoundError。

  也就是说：**本地绿 ≠ CI 绿**。本地有用户数据文件，天然会掩盖这类问题。
  而当时的发版流程里**根本没有"看 CI"这一步** —— 就算那次失败的是产品代码，
  也一样会发出去。

  ⇒ 把「发版前 CI 必须绿」做成**机器检查**，不再靠人记得去看 Actions 页面。
    这是本项目一贯的做法：能自动扣扳机的，不靠自觉
    （同类：tools/verify_pack_manifest.py 挡住"界面用了但没打包"）。

【它怎么判】
  用 `gh run list` 取**当前 HEAD** 对应的最近一次 CI 结论：
    · success                     → 放行
    · in_progress / queued        → 拦住（还没跑完，等它跑完再看）
    · failure / cancelled / ...   → 拦住，并把失败的那次 run 指出来
    · 没有对应 run（刚提交还没触发）→ 拦住，提示稍等

  ⚠️ 按 **commit sha** 匹配而不是"最近一次 run"：
     否则你本地提交了 A、还没推，却拿远端 B 的绿灯放行 —— 那是假绿。

【怎么用】
    python -m tools.verify_ci_green            # 检查 HEAD
    python -m tools.verify_ci_green --sha abc  # 检查指定 commit
    python -m tools.verify_ci_green --warn-only  # 只警告不失败（CI 里用不上）

  ⚠️ 它**不进 CI 的自检列表**（.github/workflows/ci.yml）：
     它是"发版前"的检查，而 CI 自己跑的时候当前 commit 的 CI 正是进行中，
     放进去必然自相矛盾。它是给**人**在发版前跑的一步。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 默认仓库（可用 --repo 覆盖）
DEFAULT_REPO = "Aynamuk/MapleStoryAutoLevelUp-CN"

# 视为"可以发版"的结论
GREEN = {"success"}
# 视为"还没跑完"的结论（要等）
PENDING = {"in_progress", "queued", "requested", "waiting", "pending", "action_required"}


def _run(cmd):
    """跑一条命令，返回 (returncode, stdout, stderr)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"命令不存在：{cmd[0]}（gh CLI 装了吗？）"
    except Exception as e:                                       # noqa: BLE001
        return 1, "", str(e)


def head_sha():
    """当前 HEAD 的完整 sha；取不到返回 None。"""
    rc, out, _ = _run(["git", "rev-parse", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def gh_ready():
    """gh 是否可用且已登录。返回 (ok, 说明)。"""
    rc, out, err = _run(["gh", "auth", "status"])
    if rc == 127:
        return False, "没找到 gh 命令（发版依赖它，请先安装）"
    if rc != 0:
        return False, f"gh 未登录或不可用：{(err or out).strip()[:200]}"
    return True, "gh 已登录"


def latest_run_for_sha(repo, sha):
    """取指定 commit 的最近一次 CI 运行。返回 dict 或 None。

    为什么按 sha 查而不是取"最近一次 run"：见模块头部的说明 ——
    本地提交了还没推时，"最近一次 run" 是别人的绿灯，会给出**假绿**。
    """
    rc, out, err = _run([
        "gh", "run", "list", "--repo", repo,
        "--commit", sha, "--limit", "1",
        "--json", "databaseId,status,conclusion,headSha,displayTitle,url,workflowName",
    ])
    if rc != 0:
        return {"_error": (err or out).strip()[:300]}
    try:
        runs = json.loads(out or "[]")
    except Exception as e:                                       # noqa: BLE001
        return {"_error": f"解析 gh 输出失败：{e}"}
    return runs[0] if runs else None


def judge(run):
    """判定一次 run。返回 (ok, 一句话结论, 建议动作)。

    ok=True 仅当结论是 success。其余一律 False —— 宁可拦住让人确认，
    也不要放行一个"不确定是不是绿的"版本（发版是不可逆动作）。
    """
    if run is None:
        return False, "当前 commit 还没有对应的 CI 运行", \
            "可能刚提交、还没触发。等 30 秒后重试；或先 push 再看。"
    if "_error" in run:
        return False, f"查不到 CI 状态：{run['_error']}", \
            "检查网络 / gh 登录；确认 --repo 是否正确。"
    status = (run.get("status") or "").lower()
    conclusion = (run.get("conclusion") or "").lower()

    if status in PENDING or (not conclusion and status != "completed"):
        return False, f"CI 还在跑（status={status or '未知'}）", \
            "等它跑完再检查：gh run watch 或用 gh run list 看一下。"
    if conclusion in GREEN:
        return True, "最近一次 CI 是绿的（success）", "可以发版。"
    return False, f"CI 结论是 **{conclusion or status or '未知'}**，不是 success", \
        ("先修好再发版。看失败原因：\n"
         "        gh run view {id} --log-failed\n"
         "        ⚠️ 本地绿不代表 CI 绿 —— 本地可能有 CI 环境没有的文件"
         "（典型：config/config_data.yaml 是用户数据，不进仓库）。").format(
            id=run.get("databaseId"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="发版前 CI 门禁：最近一次 CI 必须绿")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库 owner/name")
    ap.add_argument("--sha", default=None, help="要检查的 commit（缺省=当前 HEAD）")
    ap.add_argument("--warn-only", action="store_true",
                    help="只警告不失败（退出码始终 0）")
    args = ap.parse_args(argv)

    print("=== 发版前 CI 门禁 ===")

    ok_gh, gh_msg = gh_ready()
    print(f"  gh 状态: {gh_msg}")
    if not ok_gh:
        print("\n【拦下】发版前检查没能完成 —— 不给结论视为不许发版。")
        print("        按上面的提示修好 gh 后重试。")
        return 0 if args.warn_only else 1

    sha = args.sha or head_sha()
    if not sha:
        print("\n【拦下】取不到当前 commit（不在 git 仓库里？）")
        return 0 if args.warn_only else 1
    print(f"  检查 commit: {sha[:12]}")

    run = latest_run_for_sha(args.repo, sha)
    ok, verdict, advice = judge(run)

    if run and "_error" not in run:
        print(f"  CI 运行: #{run.get('databaseId')}  "
              f"{run.get('workflowName', '')}  {run.get('url', '')}")
        title = (run.get("displayTitle") or "").strip()
        if title:
            print(f"  标题   : {title}")

    print()
    if ok:
        print(f"  [OK] {verdict}")
        print(f"       {advice}")
    else:
        print(f"  [FAIL] {verdict}")
        print(f"         {advice}")

    print()
    print("判据：只有本次 commit 的 CI 结论是 success 才放行。")
    print("      （按 commit sha 匹配 —— 避免拿别人的绿灯放行自己的版本）")

    if ok:
        return 0
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
