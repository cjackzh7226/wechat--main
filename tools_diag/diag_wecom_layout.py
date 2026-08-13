"""企业微信布局诊断：截图 + 分析气泡位置/颜色，验证角色判断逻辑。
用法：先打开企业微信并点进对话，然后 python diag_wecom_layout.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

import win32gui
from PIL import Image

from wechat_window import ChatWindowDetector, ChatPlatform
from capture_ocr import _detected_chat_area_box, _infer_chat_role

OUT = r"D:\Vibe C\客服\wechat--main\wecom_debug_shot.png"


def main() -> None:
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

    # 1) 截图整个窗口
    image = _screenshot_window(wecom.hwnd, wecom.rect)
    if image is None:
        print("截图失败！")
        return
    full_path = OUT.replace(".png", "_full.png")
    image.save(full_path)
    print(f"全窗口截图已保存: {full_path} ({image.width}x{image.height})")

    # 2) 用程序自身的裁剪逻辑裁剪聊天区
    platform = wecom.platform
    area = _detected_chat_area_box(image, platform.key, platform.chat_area)
    cropped = image.crop(area)
    print(f"聊天区裁剪区域: {area}  实际尺寸={cropped.width}x{cropped.height}")
    crop_path = OUT.replace(".png", "_crop.png")
    cropped.save(crop_path)
    print(f"聊天区截图已保存: {crop_path}")

    # 3) 分析裁剪图里的水平像素分布：找气泡左右边界和颜色
    _analyze_layout(cropped, area, image.width)

    # 4) 模拟角色判断：在裁剪图上放几行"假OCR文本"看判断结果
    print("\n--- 角色判断逻辑自测（基于布局） ---")
    width = cropped.width
    _test_role(width, area)


def _enumerate_windows():
    found = []

    def cb(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found


def _screenshot_window(hwnd, rect):
    import ctypes

    try:
        import win32ui
        import win32con

        width, height = rect.width, rect.height
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        if not ok:
            ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        if not ok:
            return None
        bmp_info = bitmap.GetInfo()
        bmp_bytes = bitmap.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]), bmp_bytes, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img.copy()
    except Exception as exc:
        print(f"PrintWindow 截图失败: {exc}")
        return None


def _analyze_layout(cropped, area, full_width):
    width, height = cropped.size
    print(f"\n--- 聊天区布局分析 (宽={width}, 高={height}) ---")
    # 找每行是否有"绿色气泡"像素，记录绿色区域分布
    green_rows = []
    gray_rows = []
    for y in range(int(height * 0.10), int(height * 0.95), 6):
        g_left, g_right, g_count = _scan_color(cropped, y, _is_green)
        gray_left, gray_right, gray_count = _scan_color(cropped, y, _is_gray)
        if g_count > 3:
            green_rows.append((y, g_left, g_right, g_count))
        if gray_count > 3:
            gray_rows.append((y, gray_left, gray_right, gray_count))

    print(f"绿色气泡行数: {len(green_rows)}  灰色气泡行数: {len(gray_rows)}")
    if green_rows:
        sample = green_rows[len(green_rows) // 2]
        print(f"绿色气泡示例: y={sample[0]} x范围=[{sample[1]},{sample[2]}] (宽度占比 {sample[1]/width:.2f}~{sample[2]/width:.2f})")
    if gray_rows:
        sample = gray_rows[len(gray_rows) // 2]
        print(f"灰色气泡示例: y={sample[0]} x范围=[{sample[1]},{sample[2]}] (宽度占比 {sample[1]/width:.2f}~{sample[2]/width:.2f})")

    # 取中间一行采样颜色，展示聊天区左右边界
    mid_y = int(height * 0.5)
    colors = [cropped.getpixel((x, mid_y))[:3] for x in range(0, width, 40)]
    print(f"y={mid_y} 行的颜色采样(每40px):")
    print("  " + " ".join(f"{x}:{c}" for x, c in enumerate(colors)))


def _scan_color(image, y, predicate):
    width = image.size[0]
    left, right = None, None
    count = 0
    for x in range(0, width, 3):
        if predicate(image.getpixel((x, y))[:3]):
            count += 1
            if left is None:
                left = x
            right = x
    return (left if left is not None else -1, right if right is not None else -1, count)


def _is_green(p):
    r, g, b = p
    return 120 <= r <= 180 and 210 <= g <= 250 and 80 <= b <= 150


def _is_gray(p):
    r, g, b = p
    return 220 <= r <= 248 and 220 <= g <= 248 and 220 <= b <= 248 and abs(r - g) <= 8 and abs(g - b) <= 8


def _test_role(width, area):
    # 用当前阈值测试几个虚拟文本位置的角色判断
    left_bound = int(width * 0.18)
    tests = [
        ("对方消息(左侧25%)", width * 0.25, width * 0.25 + 80),
        ("我方消息(右侧75%)", width * 0.75, width * 0.75 + 80),
        ("中间(50%)", width * 0.50, width * 0.50 + 80),
    ]
    for name, left, right in tests:
        item = {"left": left, "right": right, "x": (left + right) / 2}
        role = _infer_chat_role(item, width, None)
        print(f"  {name}: left={left:.0f}(占比{left/width:.2f}) → 角色={role}")


if __name__ == "__main__":
    main()
