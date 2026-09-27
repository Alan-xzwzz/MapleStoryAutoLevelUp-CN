'''
Execute this script:
python mapleStoryAutoLevelUp.py --map cloud_balcony --monster brown_windup_bear,pink_windup_bear
'''
# Standard import
import time
import threading

# Libarary Import
from windows_capture import WindowsCapture, Frame, InternalCaptureControl
import cv2

# local import
from src.utils.logger import logger
import win32gui

from src.utils.common import (get_game_window_title_by_token, load_image,
                              find_game_window_hwnd)


# ============================================================================
# 抓屏「黄框开关」的兼容性探测（issue #4）
# ============================================================================
# 背景：windows-capture 的 `draw_border` 参数一旦传 True/False，库就会去调
#       `GraphicsCaptureSession.IsBorderRequired`。该属性要
#       **Windows 10 build 20348（2104）/ UniversalApiContract v12** 以上；
#       低于此版本**直接抛异常**，而不是忽略。用户机器上表现为「一开录制就崩」。
#
# 注意：库没把 is_supported() 导出到 Python（见 docs/抓屏方案局限性-研究报告.md §4.1），
#       所以这里只能用系统版本号做间接判断，判断保守一点（宁可留着黄框，不可崩）。
_DRAW_BORDER_MIN_BUILD = 20348


def _windows_build():
    """取当前 Windows 的 build 号（如 26200）。取不到返回 0（按"不达标"处理）。"""
    try:
        import ctypes

        class _OsVersionInfoExW(ctypes.Structure):
            _fields_ = [("dwOSVersionInfoSize", ctypes.c_ulong),
                        ("dwMajorVersion", ctypes.c_ulong),
                        ("dwMinorVersion", ctypes.c_ulong),
                        ("dwBuildNumber", ctypes.c_ulong),
                        ("dwPlatformId", ctypes.c_ulong),
                        ("szCSDVersion", ctypes.c_wchar * 128)]

        info = _OsVersionInfoExW()
        info.dwOSVersionInfoSize = ctypes.sizeof(_OsVersionInfoExW)
        # RtlGetVersion 不走 manifest 兼容层，拿到的是**真实**版本号，
        # 而 GetVersionEx 在兼容模式下会撒谎（旧程序读到旧号）。
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
        return int(info.dwBuildNumber)
    except Exception:                                          # noqa: BLE001
        return 0


def _supports_draw_border():
    """本机能不能安全地设置「关掉抓屏黄框」。不能就返回 False（退回库默认值）。"""
    build = _windows_build()
    ok = build >= _DRAW_BORDER_MIN_BUILD
    if not ok:
        logger.info(f"[GameWindowCapturor] 系统 build {build} 低于 "
                    f"{_DRAW_BORDER_MIN_BUILD}，本机不支持关闭抓屏黄框，"
                    f"已跳过该设置（不影响抓取）。")
    return ok


def _is_border_unsupported(exc):
    """判断异常是不是「本平台不支持切换抓屏边框」。"""
    msg = str(exc).lower()
    return "border" in msg and ("not supported" in msg or "unsupported" in msg)


class GameWindowCapturor:
    '''
    GameWindowCapturor
    '''
    def __init__(self, cfg, test_image_name = None, window_title = None):
        self.cfg = cfg
        self.frame = None
        self.lock = threading.Lock()
        self.fps = 0
        self.fps_limit = cfg["system"]["fps_limit_window_capturor"]
        self.t_last_run = 0.0
        self.capture_control = None
        self.window_title = ""

        # If use test image as input, disable the whole capture thread
        if test_image_name is not None:
            self.frame = load_image(f"test/{test_image_name}.png")
            return

        # 允许调用方直接指定窗口标题（跳过按关键词查找）。
        # 用于：诊断工具量指定窗口、多窗口歧义时人工指定。
        if window_title is not None:
            self.window_title = window_title
            self.window_hwnd = win32gui.FindWindow(None, window_title)
            logger.info(f"[GameWindowCapturor] Use assigned window title: {self.window_title}")
        else:
            # 按配置关键词解析出**唯一的窗口句柄**（内部会排除资源管理器等外壳窗口）
            self.window_hwnd = find_game_window_hwnd(cfg["game_window"]["title"])
            self.window_title = (win32gui.GetWindowText(self.window_hwnd)
                                 if self.window_hwnd else None)

        if not self.window_hwnd:
            raise RuntimeError(
                f"[GameWindowCapturor] 找不到游戏窗口（关键词：{cfg['game_window']['title']}）。"
                f"请确认：游戏已打开、处于窗口模式、且没有被最小化。"
            )
        logger.info(f"[GameWindowCapturor] Found game window: hwnd={self.window_hwnd} "
                    f"title={self.window_title!r}")

        # 自动关掉 Win11 黄框（做不到就退回默认值，绝不因此崩溃）
        _border_off = _supports_draw_border()

        # Create capture handler
        # 【注意】 必须按 **hwnd** 捕获，不能用 window_name —— 2026-09-10 实测踩坑：
        #    抓帧库对 window_name 做的是**子串匹配**（其文档原文 "Name Of The Window To Capture
        #    (substring match)"），而游戏标题「冒险岛怀旧服」正好是资源管理器窗口标题
        #    「冒险岛怀旧服国服自动练级 - 文件资源管理器」的**前缀** → 会随机抓到资源管理器，
        #    而且全程不报错。实测连抓 5 帧有 2 帧是资源管理器（2546x1433）。
        #    库的 window_hwnd 参数文档写明"比 window_name 更可靠"，这里改用它。
        # ★ 2026-09-27 显式关掉两项干扰源（此前用默认值，踩了两个坑）：
        #   · cursor_capture 默认 **True** ⇒ 鼠标光标会被画进帧里。
        #     本项目靠"找白色小地图边框""按颜色找玩家黄点"做识别，
        #     光标是额外的白色/杂色像素，属**污染源**。实测同一窗口两帧
        #     差异 100 个像素就是它造成的。
        #   · draw_border 不关的话，Win11 会给被抓窗口画一圈**黄色边框**
        #     （系统行为，不是库的问题）—— 它会落在帧里，同样干扰识别。
        #   ⚠️ 必须全用关键字参数：该构造函数**第一个位置参数是 cursor_capture**
        #      （签名见库文档），误传位置参数会把语义搞反。
        #
        # 🔥 2026-09-27（issue #4）修正上一轮的**错误结论**，务必看完再改这里：
        #   上一轮据「老系统自动忽略」把 `draw_border=False` 当成零风险改进，
        #   这个前提是**错的**。实测与文档：
        #     · 库签名里 `draw_border` 默认是 **None**（不是 False），None 表示"不碰边框设置"；
        #       传 False/True 才会去调 `GraphicsCaptureSession.IsBorderRequired`。
        #     · 该属性要 **Windows 10 build 20348（2104）** 以上，即
        #       UniversalApiContract **v12**（微软文档 Device family 一栏）。
        #     · 系统低于这个版本时，库**不是忽略而是直接抛异常**，原文：
        #       "Toggling the capture border is not supported by the Graphics Capture API
        #        on this platform."（Rust 侧 BorderConfigUnsupported）
        #       ⇒ 用户机器上「一按录制就崩」，崩的就是这一行。
        #   ⇒ 现在改成：**先测系统版本，够新才传 draw_border=False；不够新就用默认 None**。
        #     黄框只是观感问题，抓不到画面是致命的，取后者。
        #     （老系统本来也没有那圈黄框，它本身就是 Win11 才有的东西。）
        self.capture = WindowsCapture(window_hwnd=self.window_hwnd,
                                      cursor_capture=False,
                                      draw_border=False if _border_off else None)
        self.capture.event(self.on_frame_arrived)
        self.capture.event(self.on_closed)

        # Start capturing thread
        # ★ 2026-09-27 加：把库抛出的**英文技术报错**翻译成人话（issue #2 排查中发现）。
        #   实测这三种是「一启动就抛异常」的类型（能在这一步捕获）：
        #     · hwnd 失效 → "Failed to convert item to `GraphicsCaptureItem`"
        #     · 系统太老 → "The Graphics Capture API is not supported on this platform"
        #     · 无硬件 D3D11 → 设备创建失败
        #   原文普通用户看不懂，而且不翻译的话只会一路冒到界面，
        #   用户看到的就是一句英文 —— 等于没提示。
        try:
            self.capture_control = self.capture.start_free_threaded()
        except Exception as e:                                # noqa: BLE001
            # 🔥 issue #4 的双保险：版本探测只是"尽量提前避开"，
            #   万一探测结果与实际不符（改过兼容性模式、系统被裁剪、库行为变动），
            #   这里还能靠**异常特征**兜住 —— 退回默认边框设置再启动一次。
            #   代价是那一圈黄框，收益是"能跑起来"。
            if _border_off and _is_border_unsupported(e):
                logger.warning("[GameWindowCapturor] 本机/本版本不支持关闭抓屏黄框，"
                               "已退回默认设置重试（画面上可能出现一圈黄色边框，不影响识别）。")
                self.capture = WindowsCapture(window_hwnd=self.window_hwnd,
                                              cursor_capture=False)
                self.capture.event(self.on_frame_arrived)
                self.capture.event(self.on_closed)
                try:
                    self.capture_control = self.capture.start_free_threaded()
                except Exception as e2:                        # noqa: BLE001
                    raise RuntimeError(self._explain_capture_error(e2)) from e2
            else:
                raise RuntimeError(self._explain_capture_error(e)) from e

        logger.info("[GameWindowCapturor] Init done")

    @staticmethod
    def _explain_capture_error(e):
        """把抓屏库的英文异常翻译成「用户看得懂 + 知道怎么办」的中文说明。"""
        msg = str(e)
        low = msg.lower()
        # ⚠️ 顺序有讲究：border 这条必须**排在**下面「not supported on this platform」之前。
        #   因为它的原文是 "...is not supported by the Graphics Capture API on this
        #   platform."，同样含 "not supported ... on this platform" 这个子串 ——
        #   放后面会被那条泛化分支截胡，用户拿到的提示就是「系统太老、要 1809」，
        #   跟真实原因（要 20348 才支持关黄框）对不上。（issue #4）
        if _is_border_unsupported(e):
            return ("[抓屏] 你的 Windows 版本不支持「关闭抓屏黄框」这个设置。\n"
                    "        程序已自动改用默认设置重试；若仍然失败，\n"
                    "        怎么办：升级到 Windows 10 2104（build 20348）以上。\n"
                    f"        （技术原文：{msg}）")
        if "failed to convert item" in low or "graphicscaptureitem" in low:
            return ("[抓屏] 拿不到这个游戏窗口的画面 —— 窗口句柄已经失效了。\n"
                    "        最常见原因：游戏刚才被关闭过，或者窗口被重建了。\n"
                    "        怎么办：重新打开游戏，再点一次开始。\n"
                    f"        （技术原文：{msg}）")
        if "not supported on this platform" in low or "universalapicontract" in low:
            return ("[抓屏] 你的 Windows 版本太老，用不了这个抓屏方式。\n"
                    "        需要 Windows 10 1809（或更新的版本）。\n"
                    "        怎么办：升级系统，或在更新的电脑上运行。\n"
                    f"        （技术原文：{msg}）")
        if "failed to find window" in low:
            return ("[抓屏] 没找到要抓的游戏窗口。\n"
                    "        怎么办：确认游戏已经打开，且设置里的「游戏窗口标题」"
                    "和实际一致。\n"
                    f"        （技术原文：{msg}）")
        if "d3d11" in low or "hardware" in low:
            return ("[抓屏] 你的显卡/驱动不支持抓屏所需的硬件加速。\n"
                    "        常见于：虚拟机、远程桌面、显卡驱动异常或太旧。\n"
                    "        怎么办：更新显卡驱动；若是虚拟机或远程桌面，"
                    "请在本地物理机前运行。\n"
                    f"        （技术原文：{msg}）")
        return (f"[抓屏] 启动抓屏失败：{msg}\n"
                "        怎么办：重新打开游戏再试一次；若反复失败，"
                "把这条日志发给作者。")

    def on_frame_arrived(self, frame: Frame,
                         capture_control: InternalCaptureControl):
        '''
        Frame arrived callback: store frame into buffer with lock.
        '''
        with self.lock:
            self.frame = frame.frame_buffer
        self.limit_fps()

    def on_closed(self):
        '''
        Capture closed callback.
        '''
        logger.warning("[GameWindowCapturor] closed.")
        cv2.destroyAllWindows()

    def get_frame(self):
        '''
        Safely get latest game window frame.
        '''
        with self.lock:
            if self.frame is None:
                return None
            return cv2.cvtColor(self.frame, cv2.COLOR_BGRA2BGR)

    def stop(self):
        '''
        Stop capturing thread
        '''
        if self.capture_control is not None:
            self.capture_control.stop()
        logger.info("[GameWindowCapturor] Terminated")

    def limit_fps(self):
        '''
        Limit FPS
        '''
        # If the loop finished early, sleep to maintain target FPS
        target_duration = 1.0 / self.fps_limit  # seconds per frame
        frame_duration = time.time() - self.t_last_run
        if frame_duration < target_duration:
            time.sleep(target_duration - frame_duration)

        # Update FPS
        self.fps = round(1.0 / (time.time() - self.t_last_run))
        self.t_last_run = time.time()
        # logger.info(f"FPS = {self.fps}")
