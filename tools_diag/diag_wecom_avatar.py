"""企业微信头像检测误判诊断：打印每个文本块左右两侧的头像候选得分。
用法：打开企业微信点进对话，python diag_wecom_avatar.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

from settings import AppSettings
from capture_ocr import ChatOCR, _best_avatar_candidate, _is_noise_text
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
    print(f"聊天区截图: {image.width}x{image.height}\n")

    results = ocr._recognize(image)
    print(f"{'文本':<24} {'x':>5} {'y':>4} {'左头像':>14} {'右头像':>14}")
    print("-" * 70)
    for item in sorted(results, key=lambda v: (float(v["y"]), float(v["x"]))):
        text = str(item.get("text", "")).strip()
        if not text or _is_noise_text(text):
            continue
        left = float(item.get("left", item.get("x", 0)))
        right = float(item.get("right", item.get("x", left)))
        y = float(item.get("y", 0))
        lc = _best_avatar_candidate(image, 0, int(max(1, left - 10)), y)
        rc = _best_avatar_candidate(image, int(min(image.width - 1, right + 10)), image.width, y)
        show = text if len(text) <= 22 else text[:22] + "…"
        lc_s = f"{lc[0]:.2f}@{lc[1]:.0f}" if lc else "-"
        rc_s = f"{rc[0]:.2f}@{rc[1]:.0f}" if rc else "-"
        print(f"{show:<24} {float(item['x']):5.0f} {y:4.0f} {lc_s:>14} {rc_s:>14}")


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
