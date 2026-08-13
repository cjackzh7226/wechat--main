"""企业微信深度布局诊断 v2：像素级分析气泡位置与颜色，验证角色判断。
用法：先打开企业微信并点进对话，然后 python diag_wecom_layout2.py"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")

import win32gui
from PIL import Image

from wechat_window import ChatWindowDetector
from capture_ocr import _detected_chat_area_box


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

    image = _screenshot_window(wecom.hwnd, wecom.rect)
    if image is None:
        print("截图失败！")
        return

    # 全图颜色分布统计
    print("\n===== 全窗口像素统计 =====")
    _count_colors(image, "全窗口")

    # 裁剪聊天区
    platform = wecom.platform
    area = _detected_chat_area_box(image, platform.key, platform.chat_area)
    cropped = image.crop(area)
    print(f"\n聊天区裁剪区域: {area}  实际尺寸={cropped.width}x{cropped.height}")

    # 聊天区颜色统计 + 每行分析
    print("\n===== 聊天区像素统计 =====")
    _count_colors(cropped, "聊天区")

    print("\n===== 逐行扫描：绿色/灰色/白色 色块分布 =====")
    _scan_rows(cropped)


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


def _count_colors(image, label):
    """统计全图各类颜色像素占比"""
    width, height = image.size
    total = 0
    green = gray = white = dark = other = 0
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            p = image.getpixel((x, y))[:3]
            total += 1
            if _is_green(p):
                green += 1
            elif _is_gray(p):
                gray += 1
            elif p[0] >= 248 and p[1] >= 248 and p[2] >= 248:
                white += 1
            elif sum(p) < 420:
                dark += 1
            else:
                other += 1
    print(f"{label} ({width}x{height}): 绿色={green/total*100:.1f}% 灰色={gray/total*100:.1f}% "
          f"纯白={white/total*100:.1f}% 深色={dark/total*100:.1f}% 其他={other/total*100:.1f}%")


def _scan_rows(cropped):
    width, height = cropped.size
    print(f"行数={height}, 宽={width}")
    for y in range(40, height, 28):
        # 找该行绿色色块
        green_blocks = _find_blocks(cropped, y, _is_green)
        gray_blocks = _find_blocks(cropped, y, _is_gray)
        white_blocks = _find_blocks(cropped, y, _is_white)
        # 该行中间点颜色
        mid_p = cropped.getpixel((width // 2, y))[:3]
        parts = [f"y={y}"]
        if green_blocks:
            parts.append(f"绿色块={green_blocks}")
        if gray_blocks:
            parts.append(f"灰色块={gray_blocks}")
        if white_blocks:
            parts.append(f"白色块={white_blocks}")
        parts.append(f"中点色={mid_p}")
        # 只输出有内容或明显的行
        if green_blocks or (gray_blocks and y > 60) or y % 84 == 40:
            print("  " + " | ".join(parts))


def _find_blocks(image, y, predicate):
    width = image.size[0]
    blocks = []
    in_block = False
    start = 0
    count = 0
    for x in range(0, width, 2):
        hit = predicate(image.getpixel((x, y))[:3])
        if hit and not in_block:
            in_block = True
            start = x
            count = 1
        elif hit and in_block:
            count += 1
        elif not hit and in_block:
            if count >= 6:
                blocks.append(f"[{start},{x}]")
            in_block = False
            count = 0
    if in_block and count >= 6:
        blocks.append(f"[{start},{width}]")
    return blocks


def _is_green(p):
    r, g, b = p
    return 120 <= r <= 180 and 210 <= g <= 250 and 80 <= b <= 150


def _is_gray(p):
    r, g, b = p
    return 200 <= r <= 250 and 200 <= g <= 250 and 200 <= b <= 250 and abs(r - g) <= 10 and abs(g - b) <= 10 and sum(p) < 740


def _is_white(p):
    return p[0] >= 248 and p[1] >= 248 and p[2] >= 248


if __name__ == "__main__":
    main()
