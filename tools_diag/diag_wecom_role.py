"""企业微信角色误判诊断：打印每个文本块的 avatar_role / visual_role / 位置角色。
用法：打开企业微信点进对话，python diag_wecom_role.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

from settings import AppSettings
from capture_ocr import (
    ChatOCR,
    _infer_role_from_avatar_proximity,
    _infer_role_from_bubble_color,
    _is_noise_text,
)
from wechat_window import ChatWindowDetector


def main() -> None:
    settings = AppSettings()
    settings.load()
    ocr = ChatOCR(settings)
    detector = ChatWindowDetector()

    wecom = None
    for hwnd in _enumerate_windows():
        w = detector.window_from_hwnd(hwnd)
        if w and w.platform.key == "wecom":
            wecom = w
            break
    if wecom is None:
        print("未找到企业微信窗口，请先打开企业微信并点进对话。")
        return

    image = ocr.capture(wecom)
    if image is None:
        print(f"截图失败: {ocr.engine_error}")
        return
    print(f"企业微信窗口尺寸: {wecom.rect.width}x{wecom.rect.height}, 聊天区截图: {image.width}x{image.height}\n")

    results = ocr._recognize(image)
    print(f"{'文本':<24} {'x':>5} {'y':>4} {'头像判':>6} {'颜色判':>6} {'位置判':>6} → 最终")
    print("-" * 72)
    for item in sorted(results, key=lambda v: (float(v["y"]), float(v["x"]))):
        text = str(item.get("text", "")).strip()
        if not text or _is_noise_text(text):
            continue
        avatar_role = _infer_role_from_avatar_proximity(item, image)
        visual_role = _infer_role_from_bubble_color(item, image)
        pos_role = _position_role(item, image.width)
        final_role = avatar_role or visual_role or pos_role
        show = text if len(text) <= 22 else text[:22] + "…"
        print(
            f"{show:<24} {float(item['x']):5.0f} {float(item['y']):4.0f} "
            f"{avatar_role or '-':>6} {visual_role or '-':>6} {pos_role:>6} → {final_role}"
        )


def _position_role(item: dict, width: int) -> str:
    left = float(item.get("left", item.get("x", 0)))
    right = float(item.get("right", item.get("x", left)))
    if width <= 0:
        return "对方"
    center = (left + right) / 2
    right_margin = max(0.0, width - right)
    if left <= width * 0.18 and right_margin >= width * 0.12:
        return "对方"
    if right_margin <= width * 0.19 and left >= width * 0.20:
        return "我"
    if center >= width * 0.58:
        return "我"
    if center <= width * 0.42:
        return "对方"
    if right_margin + width * 0.04 < left:
        return "我"
    return "对方"


def _enumerate_windows():
    import win32gui

    found = []

    def cb(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found


if __name__ == "__main__":
    main()
