"""企业微信端到端测试：截图 → OCR → 角色判断，打印每个文本块的位置与判定。
用法：打开企业微信点进对话，python diag_wecom_e2e.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

from settings import AppSettings
from capture_ocr import ChatOCR, _infer_chat_role, _is_noise_text
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

    print(f"企业微信窗口: {wecom.title!r} 尺寸={wecom.rect.width}x{wecom.rect.height}")
    image = ocr.capture(wecom)
    if image is None:
        print(f"截图失败: {ocr.engine_error}")
        return
    print(f"聊天区截图尺寸: {image.width}x{image.height}")

    results = ocr._recognize(image)
    print(f"OCR 识别到 {len(results)} 个文本块\n")
    print(f"{'文本':<28} {'x':>5} {'left':>6} {'right':>7} {'y':>5}  角色")
    print("-" * 75)
    for item in sorted(results, key=lambda v: (float(v["y"]), float(v["x"]))):
        text = str(item.get("text", "")).strip()
        if not text or _is_noise_text(text):
            continue
        role = _infer_chat_role(item, image.width, image)
        left = float(item.get("left", item.get("x", 0)))
        right = float(item.get("right", item.get("x", left)))
        x = float(item.get("x", 0))
        y = float(item.get("y", 0))
        show = text if len(text) <= 26 else text[:26] + "…"
        print(f"{show:<28} {x:5.0f} {left:6.0f} {right:7.0f} {y:5.0f}  {role}")


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
