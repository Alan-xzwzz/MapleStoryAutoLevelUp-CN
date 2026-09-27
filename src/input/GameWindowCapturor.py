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
        #     tools/grab_frame.py 早就用了这个参数，主程序这里漏了。
        #   ⚠️ 必须全用关键字参数：该构造函数**第一个位置参数是 cursor_capture**
        #      （签名见库文档），误传位置参数会把语义搞反。
        self.capture = WindowsCapture(window_hwnd=self.window_hwnd,
                                      cursor_capture=False,
                                      draw_border=False)
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
            raise RuntimeError(self._explain_capture_error(e)) from e

        logger.info("[GameWindowCapturor] Init done")

    @staticmethod
    def _explain_capture_error(e):
        """把抓屏库的英文异常翻译成「用户看得懂 + 知道怎么办」的中文说明。"""
        msg = str(e)
        low = msg.lower()
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
