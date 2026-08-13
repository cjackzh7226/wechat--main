"""诊断脚本：检查企业微信/微信窗口为什么识别不到。用法：python diag_wecom_window.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

import win32gui
import win32process
from pathlib import Path

from wechat_window import ChatWindowDetector, ChatPlatform


def pid_process_name(pid: int) -> str:
    import ctypes

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


def main() -> None:
    detector = ChatWindowDetector()
    print("=" * 100)
    print("当前前台窗口:")
    fg = win32gui.GetForegroundWindow()
    if fg:
        _dump(fg, detector, mark="<-- 前台")
    print("=" * 100)
    print("所有可见窗口（按尺寸排序，前 25 个）：")

    rows: list[tuple[int, int, int]] = []

    def collect(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w < 50 or h < 30:
            return True
        rows.append((w * h, w, hwnd))
        return True

    win32gui.EnumWindows(collect, None)
    rows.sort(reverse=True)
    for area, w, hwnd in rows[:25]:
        _dump(hwnd, detector)

    print("=" * 100)
    print("当前进程列表（含微信/企业微信关键字的）：")
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_ulong),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snapshot:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                name = entry.szExeFile.decode("gbk", "ignore").lower()
                if "wxwork" in name or "wecom" in name or "wechat" in name or "weixin" in name or "wework" in name:
                    print(f"  PID {entry.th32ProcessID:>7}  {entry.szExeFile.decode('gbk','ignore')}")
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        kernel32.CloseHandle(snapshot)


def _dump(hwnd: int, detector: ChatWindowDetector, mark: str = "") -> None:
    title = win32gui.GetWindowText(hwnd).strip()
    cls = win32gui.GetClassName(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    proc = Path(pid_process_name(pid)).name
    rect = win32gui.GetWindowRect(hwnd)
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    matched = detector.window_from_hwnd(hwnd)
    match_info = ""
    if matched is not None:
        match_info = f"  >>> 已识别为【{matched.platform.label}】"
    elif title or "wxwork" in proc.lower():
        match_info = "  >>> 未被识别 ← 原因见下"
    print(f"  [{w}x{h}] 标题={title!r} | 类名={cls!r} | 进程={proc} | PID={pid} {mark}")
    if match_info:
        print(f"      {match_info}")


if __name__ == "__main__":
    main()
