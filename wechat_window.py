from __future__ import annotations

import ctypes
import re
from pathlib import Path
from dataclasses import dataclass

from settings import ChatArea


try:
    import win32api
    import win32gui
    import win32process
except Exception:  # pragma: no cover - Windows optional dependency
    win32api = None
    win32gui = None
    win32process = None


@dataclass(frozen=True)
class InputPoint:
    x: float
    y: float


@dataclass(frozen=True)
class ChatPlatform:
    key: str
    label: str
    title_keywords: tuple[str, ...]
    process_keywords: tuple[str, ...]
    class_keywords: tuple[str, ...]
    chat_area: ChatArea
    input_point: InputPoint

    @classmethod
    def profiles(cls) -> tuple["ChatPlatform", ...]:
        return (
            cls(
                key="wechat",
                label="微信",
                title_keywords=("微信", "WeChat", "Weixin"),
                process_keywords=("wechat", "weixin"),
                class_keywords=("wechat", "qt5"),
                chat_area=ChatArea(0.31, 0.98, 0.09, 0.91),
                input_point=InputPoint(0.66, 0.92),
            ),
            cls(
                key="wecom",
                label="企业微信",
                title_keywords=("企业微信", "WeCom", "WXWork"),
                process_keywords=("wxwork", "wecom"),
                class_keywords=("wxwork", "wecom", "qt5"),
                # 企业微信 4.x 实测布局：左侧导航+会话列表约 0~240px，
                # 聊天区从 ~29% 一直延伸到窗口右缘（“我”的气泡靠右显示，无右侧详情面板）。
                # 底部：最新消息气泡可到 ~72% 高度（y≈468/650），输入框从 ~76% 开始，
                # 裁剪底部取 0.72 可覆盖最新消息且避开输入框/工具栏。
                chat_area=ChatArea(0.29, 0.99, 0.10, 0.72),
                input_point=InputPoint(0.52, 0.93),
            ),
            cls(
                key="qq",
                label="QQ",
                title_keywords=("QQ",),
                process_keywords=("qq.exe", "ntqq", "qqnt", "qq"),
                class_keywords=("txguifoundation", "qq"),
                chat_area=ChatArea(0.36, 0.98, 0.12, 0.68),
                input_point=InputPoint(0.68, 0.94),
            ),
        )

    @classmethod
    def for_key(cls, key: str) -> "ChatPlatform":
        for profile in cls.profiles():
            if profile.key == key:
                return profile
        raise KeyError(key)

@dataclass
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class ChatWindow:
    hwnd: int
    title: str
    rect: WindowRect
    platform: ChatPlatform

    @property
    def display_title(self) -> str:
        return f"{self.platform.label}：{self.title}"


class ChatWindowDetector:
    def foreground_chat(self) -> ChatWindow | None:
        if win32gui is None:
            return None
        hwnd = win32gui.GetForegroundWindow()
        return self._window_from_hwnd(hwnd)

    def window_from_hwnd(self, hwnd: int) -> ChatWindow | None:
        return self._window_from_hwnd(hwnd)

    def any_chat_window(self) -> ChatWindow | None:
        if win32gui is None:
            return None
        candidates: list[ChatWindow] = []

        def collect(hwnd: int, _extra: object) -> bool:
            window = self._window_from_hwnd(hwnd)
            if window:
                candidates.append(window)
            return True

        win32gui.EnumWindows(collect, None)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.rect.width * item.rect.height, reverse=True)
        return candidates[0]

    def _window_from_hwnd(self, hwnd: int) -> ChatWindow | None:
        if win32gui is None:
            return None
        if not hwnd or not win32gui.IsWindowVisible(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return None
        if _is_non_chat_popup(title):
            return None
        platform = self._match_platform(hwnd, title)
        if platform is None:
            return None
        raw = win32gui.GetWindowRect(hwnd)
        rect = WindowRect(*raw)
        if rect.width < 420 or rect.height < 360:
            return None
        return ChatWindow(hwnd=hwnd, title=title, rect=rect, platform=platform)

    def snap_geometry(self, window: ChatWindow, width: int, height: int) -> str:
        return snap_geometry_for_work_area(
            window.rect,
            width,
            height,
            self._work_area_for_window(window.hwnd),
        )

    @staticmethod
    def _work_area_for_window(hwnd: int) -> tuple[int, int, int, int]:
        if win32api is not None:
            try:
                monitor = win32api.MonitorFromWindow(int(hwnd), 2)
                work = win32api.GetMonitorInfo(monitor).get("Work")
                if work and len(work) == 4:
                    return tuple(int(value) for value in work)
            except Exception:
                pass
        user32 = ctypes.windll.user32
        return (0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))

    def _match_platform(self, hwnd: int, title: str) -> ChatPlatform | None:
        """仅识别企业微信：本应用只针对企业微信使用，微信/QQ 窗口一律不处理。

        进程名最可靠：WXWork.exe / WeCom.exe → 企业微信；其次标题（企业微信/WeCom/WXWork）；
        最后类名（wxwork/wecom/qt5）兜底。
        """
        process_name = self._process_name(hwnd)
        class_name = self._class_name(hwnd)
        title_l = title.lower()
        process_l = process_name.lower()
        class_l = class_name.lower()

        profile = ChatPlatform.for_key("wecom")
        if _process_matches_platform(profile, process_l):
            return profile
        if _title_matches_platform(profile, title_l):
            return profile
        if any(keyword.lower() in class_l for keyword in profile.class_keywords):
            return profile
        return None

    @staticmethod
    def _class_name(hwnd: int) -> str:
        try:
            return win32gui.GetClassName(hwnd) if win32gui is not None else ""
        except Exception:
            return ""

    @staticmethod
    def _process_name(hwnd: int) -> str:
        if win32process is None:
            return ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return ""
        return ChatWindowDetector._pid_process_name(pid)

    @staticmethod
    def _pid_process_name(pid: int) -> str:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)


WeChatWindow = ChatWindow
WeChatWindowDetector = ChatWindowDetector


def snap_geometry_for_work_area(
    window_rect: WindowRect,
    width: int,
    height: int,
    work_area: tuple[int, int, int, int],
    margin: int = 8,
) -> str:
    """Place the assistant beside WeCom within the target monitor work area."""
    work_left, work_top, work_right, work_bottom = work_area
    x = window_rect.right + margin
    if x + width > work_right:
        x = window_rect.left - width - margin
    max_x = max(work_left, work_right - width)
    max_y = max(work_top, work_bottom - height - margin)
    x = max(work_left, min(x, max_x))
    y = max(work_top, min(window_rect.top, max_y))
    x_part = f"+{x}" if x >= 0 else str(x)
    y_part = f"+{y}" if y >= 0 else str(y)
    return f"{int(width)}x{int(height)}{x_part}{y_part}"


def _is_non_chat_popup(title: str) -> bool:
    """排除聊天程序弹出的非聊天窗口（转发消息详情、聊天记录、搜索等），
    避免被误识别为聊天窗口导致识别错乱。"""
    popup_keywords = (
        "转发消息详情",
        "聊天记录",
        "消息记录",
        "搜索结果",
        "选择联系人",
        "选择聊天",
        "添加成员",
        "创建群聊",
        "群聊设置",
        "聊天设置",
        "个人资料",
        "设置",
        "关于",
        "文件传输",
        "收藏",
        "截图",
        "输入密码",
        "验证码",
        "登录",
        "升级",
        "提醒",
        "日程",
        "待办",
        "通讯录",
        "工作台",
        "应用",
        "搜索",
        "扫一扫",
        "帮助",
        "意见反馈",
    )
    for keyword in popup_keywords:
        if keyword in title:
            return True
    return False


def _title_matches_platform(profile: ChatPlatform, title_l: str) -> bool:
    if profile.key == "qq":
        cleaned = title_l.strip()
        return cleaned == "qq" or cleaned.startswith("qq ")
    if profile.key == "wechat" and "企业微信" in title_l:
        # 企业微信的标题里含“微信”二字，必须排除，防止误判为微信
        return False
    if profile.key in {"wechat", "wecom"}:
        # 微信/企业微信标题匹配必须更精确，只接受独立平台名，
        # 避免浏览器网页标题中的“微信公众号”“企业微信管理后台”被误判。
        if profile.key == "wechat" and ("文件资源管理器" in title_l or "explorer" in title_l):
            return False
        if profile.key == "wechat" and _looks_like_file_path_title(title_l):
            return False
        return any(_title_contains_token(title_l, keyword.lower()) for keyword in profile.title_keywords)
    return any(keyword.lower() in title_l for keyword in profile.title_keywords)


def _title_contains_token(title_l: str, keyword: str) -> bool:
    """标题兜底匹配时只接受独立平台名，避免网页标题造成误识别。"""
    if keyword in {"wechat", "weixin", "wecom", "wxwork"}:
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", title_l) is not None
    separators = r"\s\-—–:：|_()\[\]{}【】"
    return re.search(rf"(?:^|[{separators}]){re.escape(keyword)}(?:$|[{separators}])", title_l) is not None


def _looks_like_file_path_title(title_l: str) -> bool:
    """标题形如“路径 - 文件资源管理器”或包含盘符/扩展名的，视为文件窗口标题。"""
    if "文件资源管理器" in title_l or "explorer" in title_l:
        return True
    if re.search(r"[a-z]:[\\/]", title_l):
        return True
    if re.search(r"\.\w{1,5}\s*[-—–]", title_l):
        return True
    return False


def _process_matches_platform(profile: ChatPlatform, process_l: str) -> bool:
    if not process_l:
        return False
    name = Path(process_l).name.lower()
    stem = Path(name).stem.lower()
    for keyword in profile.process_keywords:
        key = keyword.lower()
        key_name = Path(key).name.lower()
        key_stem = Path(key_name).stem.lower()
        if name == key_name or stem == key_stem:
            return True
    return False
