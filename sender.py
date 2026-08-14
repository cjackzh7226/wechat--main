from __future__ import annotations

import time

from managed_runtime import SendAttempt


try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None

try:
    import win32gui
    import win32con
except Exception:  # pragma: no cover
    win32gui = None
    win32con = None


class WeChatSender:
    def activate_verified_target(self, target: object) -> bool:
        """Activate one visible, non-minimized HWND and confirm it owns focus."""
        hwnd = _target_hwnd(target)
        if win32gui is None or not hwnd:
            return False
        try:
            if (
                not win32gui.IsWindow(hwnd)
                or not win32gui.IsWindowVisible(hwnd)
                or win32gui.IsIconic(hwnd)
            ):
                return False
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.15)
            return self._is_target_foreground(target)
        except Exception:
            return False

    def send(
        self,
        target: object,
        text: str,
        *,
        allow_activate: bool = False,
    ) -> SendAttempt:
        if type(text) is not str or not text.strip():
            return SendAttempt(False, "发送内容为空。")
        if pyautogui is None:
            return SendAttempt(False, "缺少 pyautogui，无法自动发送。")
        if pyperclip is None:
            return SendAttempt(False, "缺少 pyperclip，无法写入剪贴板。")
        if type(allow_activate) is not bool:
            return SendAttempt(False, "allow_activate 参数无效。")
        previous_clipboard = _read_clipboard()
        copied_for_send = False
        try:
            if allow_activate:
                self._activate(target)
                time.sleep(0.15)
            if not self._is_target_foreground(target):
                return SendAttempt(False, "目标聊天窗口不在前台，已取消发送。", retry_safe=True)
            self._click_input(target)
            if not self._is_target_foreground(target):
                return SendAttempt(False, "点击输入框后前台窗口已变化，已取消发送。", retry_safe=True)
            pyperclip.copy(text)
            copied_for_send = True
            time.sleep(0.08)
            if not self._is_target_foreground(target):
                return SendAttempt(False, "粘贴前前台窗口已变化，已取消发送。", retry_safe=True)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.12)
            if not self._is_target_foreground(target):
                return SendAttempt(False, "focus_lost_after_paste")
            pyautogui.press("enter")
            return SendAttempt(True, "已按下发送键，等待气泡验证")
        except Exception as exc:
            return SendAttempt(False, f"发送失败：{type(exc).__name__}")
        finally:
            if copied_for_send and previous_clipboard is not None:
                try:
                    pyperclip.copy(previous_clipboard)
                except Exception:
                    pass

    @staticmethod
    def _activate(target: object) -> None:
        hwnd = _target_hwnd(target)
        if win32gui is None or not hwnd:
            return
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def scroll_chat_to_bottom(target: object) -> None:
        """滚动聊天窗口到底部，确保截图能看到最新消息。
        企业微信/微信聊天窗口若停在旧位置，新消息在可视区域外，OCR 将识别不到。
        注意：不能点击消息区域（会误触“转发”等弹窗），只激活窗口后按 End 键。"""
        if pyautogui is None:
            return
        if not WeChatSender._is_target_foreground(target):
            return
        try:
            # 仅按 End 键滚动到底部，不点击任何位置，避免误触消息气泡弹出转发/菜单
            pyautogui.press("end")
            time.sleep(0.15)
        except Exception:
            pass

    @staticmethod
    def _click_input(target: object) -> None:
        if pyautogui is None:
            return
        rect = getattr(target, "rect", None)
        platform = getattr(target, "platform", None)
        input_point = getattr(platform, "input_point", None)
        if rect is not None and input_point is not None:
            x = rect.left + int(rect.width * input_point.x)
            y = rect.top + int(rect.height * input_point.y)
            pyautogui.click(x, y)
            return
        hwnd = _target_hwnd(target)
        if win32gui is None or not hwnd:
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        pyautogui.click(left + width // 2, bottom - 58)

    @staticmethod
    def _is_target_foreground(target: object) -> bool:
        hwnd = _target_hwnd(target)
        if win32gui is None or not hwnd:
            return False
        try:
            return int(win32gui.GetForegroundWindow() or 0) == hwnd
        except Exception:
            return False


def _target_hwnd(target: object) -> int:
    try:
        return int(getattr(target, "hwnd", target) or 0)
    except Exception:
        return 0


def _read_clipboard() -> str | None:
    try:
        return str(pyperclip.paste()) if pyperclip is not None and hasattr(pyperclip, "paste") else None
    except Exception:
        return None
