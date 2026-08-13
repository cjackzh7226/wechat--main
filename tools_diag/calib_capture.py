# -*- coding: utf-8 -*-
"""企业微信 5.0.9.6060 布局标定：截取全窗图供人工/视觉标定。"""
import sys
sys.path.insert(0, r"D:\Vibe C\客服\wechat--main")
from capture_ocr import _capture_window_bitmap
from wechat_window import ChatWindowDetector


def main():
    detector = ChatWindowDetector()
    window = detector.foreground_chat() or detector.any_chat_window()
    if window is None:
        print("未找到企业微信主窗口")
        return
    print(f"窗口 hwnd={window.hwnd} title={window.title!r} rect=({window.rect.left},{window.rect.top},{window.rect.right},{window.rect.bottom}) 尺寸={window.rect.width}x{window.rect.height}")
    img = _capture_window_bitmap(window)
    if img is None:
        print("截图失败")
        return
    out = r"D:\Vibe C\客服\wechat--main\docs\calib_full.png"
    img.save(out)
    print(f"已保存全窗图: {out} 尺寸 {img.width}x{img.height}")


if __name__ == "__main__":
    main()
