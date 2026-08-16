from __future__ import annotations

import time


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
    def send(self, target: object, text: str) -> tuple[bool, str]:
        text = text.strip()
        if not text:
            return False, "发送内容为空。"
        if pyautogui is None:
            return False, "缺少 pyautogui，无法自动发送。"
        if pyperclip is None:
            return False, "缺少 pyperclip，无法写入剪贴板。"
        previous_clipboard = _read_clipboard()
        copied_for_send = False
        try:
            self._activate(target)
            time.sleep(0.15)
            if not self._is_target_foreground(target):
                return False, "目标聊天窗口没有成功切到前台，已取消发送，避免发到其他应用。"
            self._click_input(target)
            pyperclip.copy(text)
            copied_for_send = True
            time.sleep(0.08)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.12)
            pyautogui.press("enter")
            return True, "已发送"
        except Exception as exc:
            return False, f"发送失败：{exc}"
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
        hwnd = _target_hwnd(target)
        if win32gui is not None and hwnd and win32gui.IsIconic(hwnd):
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
        try:
            if win32gui is not None and hwnd:
                win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.1)
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
            return True
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
