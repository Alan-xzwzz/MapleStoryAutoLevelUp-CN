# -*- coding: utf-8 -*-
"""血蓝监控的运行环境预检（2026-09-27 加）。

【一句话说明】
    血条/蓝条的位置是**按 1366x768 窗口化量死的**，环境一变位置就偏，
    读数就错。所以在开跑前先量一下真实窗口，不对就**拦住并说清怎么改**，
    而不是让它默默读错、默默不喝药（那种"看起来在跑其实没用"最难查）。

【为什么必须按「实测客户区」判断，不能用游戏里显示的分辨率】
    issue #2 的真因就是这个（2026-09-27 实测）：
        用户日志 rect=(-1821,201,-439,1008) ⇒ 客户区实际 1282x707，
        而 config 写的是 1366x768 ⇒ 抓帧裁不出一帧 ⇒ 引擎刷 10 秒后退出。
    1282x707 不是用户选的分辨率，是**多屏且各屏 DPI 缩放不同**时，
    窗口被系统缩放后的实际物理像素。所以只能以**实测**为准。

【为什么不在缺少确定信息时猜】
    这里只做"能确定的检查"：
      · 客户区尺寸 —— 能直接量出来，必查；
      · 是否最大化 —— 能量，必查（最大化后客户区通常不是 1366x768）；
      · 系统缩放比例 —— 能量，但**只在客户区尺寸不对时**才一并报出来，
        因为它多半是那个"为什么尺寸会不对"的答案。

设计原则（与项目里 issue #2 的收尾一致）：
    预检**绝不静默失败**，也**绝不只说"错了"** —— 每条报错都要带
    「怎么改」，并且改法要能让**免安装包用户**照做（不给命令行）。
"""

from __future__ import annotations

# 本项目锁死的运行环境（与 config_default.yaml 的 game_window.size 一致）
# [高, 宽]，注意顺序与项目其他地方一致（高在前）
REQUIRED_CLIENT_SIZE = (768, 1366)

# 读取窗口标题时用的配置键
_CFG_SECTION = "game_window"

# README 里「环境要求」那一节的关键词（报错时指路用；用户不会主动翻文档）
README_HINT = (
    "详细说明见 README 的「运行环境要求」一节"
    "（游戏里选 1366x768 + 窗口化 + Windows 显示缩放 100%）。")


def _human_size(size):
    """(高, 宽) → '宽x高' 这种人话（用户习惯说宽 x 高）。"""
    if not size or len(size) < 2:
        return "<未知>"
    h, w = int(size[0]), int(size[1])
    return f"{w} x {h}"


def _system_scaling_percent():
    """当前主显示器的 Windows 缩放比例（%）；取不到返回 None。

    ⚠️ 只用于**解释**"为什么客户区尺寸不对"，不参与判定。
       取不到时不报错、不拦路 —— 预检宁可少说一句，也不能因为
       取不到一个辅助信息就把工具堵死。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Win10 1703+ 的 per-monitor DPI 接口；老系统上拿不到就算了
        try:
            user32.SetProcessDpiAwarenessContext(-4)   # PER_MONITOR_AWARE_V2
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        hdc = user32.GetDC(0)
        if hdc:
            LOGPIXELSX = 88
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            user32.ReleaseDC(0, hdc)
            if dpi:
                return int(round(dpi / 96.0 * 100))
    except Exception:
        return None
    return None


def _is_maximized(hwnd):
    """窗口是否处于最大化状态；判不了返回 None。"""
    try:
        import win32gui
        import win32con
        place = win32gui.GetWindowPlacement(hwnd)
        return place[1] == win32con.SW_SHOWMAXIMIZED
    except Exception:
        return None


def check_bars_environment(cfg, window_title=None):
    """检查血蓝监控的运行环境是否匹配。

    Args:
        cfg: 完整配置（至少要有 game_window 段）
        window_title: 游戏窗口标题；不给就用 cfg 里的

    Returns:
        (ok, title, message) 三元组：
            ok      True = 环境对得上，可以开跑
            title   一句话标题（给弹窗/日志当抬头）
            message 多行说明；ok=True 时是简短确认语，
                    ok=False 时**必须**包含「怎么改」
    """
    gw = (cfg or {}).get(_CFG_SECTION, {}) or {}
    title = window_title or gw.get("title", "")
    want = tuple(gw.get("size") or REQUIRED_CLIENT_SIZE)
    want_h, want_w = int(want[0]), int(want[1])

    # ── 1. 找窗口 ────────────────────────────────────────────────
    hwnd = None
    try:
        from src.utils.common import find_game_window_hwnd
        hwnd = find_game_window_hwnd(title)
    except Exception:
        hwnd = None

    if not hwnd:
        return (False, "没找到游戏窗口",
                f"没有找到标题含「{title}」的游戏窗口。\n\n"
                f"请先：\n"
                f"　1. 打开游戏并进到角色所在的画面\n"
                f"　2. 别最小化（挡住没关系，最小化不行）\n"
                f"　3. 确认游戏窗口最上面那条标题栏里有「{title}」这几个字\n\n"
                f"{README_HINT}")

    # ── 2. 量真实客户区（这才是"游戏画面"的实际大小）──────────────
    client = None
    try:
        from src.utils.common import get_window_client_size
        client = get_window_client_size(title)
    except Exception:
        client = None

    if client is None:
        return (False, "量不到游戏窗口大小",
                "找到了游戏窗口，但量不出它的画面区域大小。\n\n"
                "常见原因：窗口正被最小化、或刚打开还在加载。\n"
                "请把游戏窗口还原到屏幕上，再试一次。\n\n"
                f"{README_HINT}")

    cw, ch = int(client[0]), int(client[1])

    # ── 3. 尺寸对不对 ────────────────────────────────────────────
    if (cw, ch) != (want_w, want_h):
        lines = [
            "自动喝药要盯着血条和蓝条看，而它们的位置是按「1366x768 窗口化」"
            "一个像素一个像素量出来的 —— 窗口大小一变，位置就偏，读出来的血量就是错的。",
            "",
            f"现在游戏画面区域的大小是：　{cw} x {ch}（宽 x 高）",
            f"要求的大小是：　　　　　　{want_w} x {want_h}",
            "",
            "请按顺序检查这三条（三条都要满足）：",
            f"　① 游戏里把分辨率设成 {want_w} x {want_h}，并且用「窗口化」运行"
            f"（全屏、最大化都不行）",
            f"　② Windows 的「显示设置 → 缩放」设成 100%",
            f"　③ 如果你有两个屏幕、且两个屏幕的缩放不一样，"
            f"把游戏窗口拖到主屏幕上再试",
        ]
        scale = _system_scaling_percent()
        if scale is not None and scale != 100:
            lines += [
                "",
                f"（顺带量到：你现在的系统缩放是 {scale}%，"
                f"这多半就是画面尺寸对不上的原因 —— 把它调回 100% 试试。）",
            ]
        if _is_maximized(hwnd) is True:
            lines += [
                "",
                "（另外：游戏窗口现在是**最大化**状态。"
                "请把它还原成普通窗口，再手动拖成 "
                f"{want_w}x{want_h} 的画面区域。）",
            ]
        lines += ["", README_HINT]
        return (False, "游戏画面大小不对", "\n".join(lines))

    # ── 4. 尺寸对了，但最大化/缩放仍可能让坐标整体偏移 ──────────────
    # 最大化时客户区恰好等于 1366x768 的概率极低，但**真出现**时，
    # 坐标系仍是对的（裁出来就是 1366x768）—— 所以这里只提醒、不拦。
    extra = []
    scale = _system_scaling_percent()
    if scale is not None and scale != 100:
        extra.append(f"（注意：系统缩放现在是 {scale}%，不是 100%。"
                     f"这次窗口大小刚好对得上，但下次重开游戏可能就偏了，"
                     f"建议调回 100%。）")

    msg = f"游戏画面 {cw} x {ch}，与要求的 {want_w} x {want_h} 一致，血蓝监控可用。"
    if extra:
        msg += "\n" + "\n".join(extra)
    return (True, "环境检查通过", msg)


def format_for_log(ok, title, message):
    """把预检结果格式化成日志行（缩进对齐全项目日志风格）。"""
    head = "[血蓝预检] 【OK】" if ok else "[血蓝预检] 【拦下】"
    body = "\n".join("        " + ln if ln else "" for ln in message.splitlines())
    return f"{head} {title}\n{body}"
