# -*- coding: utf-8 -*-
"""conversation_list 模块单测：红点检测 / 四分类 / 状态机（用合成图，不依赖真实窗口）。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conversation_list import (
    ChatIdentityTracker,
    ChatStateGuard,
    ConversationListMonitor,
    ConversationTypeDetector,
    RedDotHit,
)

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None

CAL = {
    "conversation_list_area": {"x_left": 0.08, "x_right": 0.3, "y_top": 0.12, "y_bottom": 1.0},
    "top_title_area": {"x_left": 0.3, "x_right": 1.0, "y_top": 0.0, "y_bottom": 0.10},
    "avatar_area": {"x_left": 0.30, "x_right": 0.44, "y_top": 0.01, "y_bottom": 0.09},
    "red_dot_rgb": {"r_min": 230, "g_max": 90, "b_max": 90, "min_diameter_px": 5, "max_diameter_px": 16},
    "avatar_grid": {"grid": 3, "boundary_jump_min": 18},
    "click_target": {"avatar_x_ratio": 0.18},
    "timing": {
        "self_stable_seconds": 2.0,
        "scan_interval_seconds": 2.0,
        "switch_wait_seconds": 1.2,
        "user_typing_seconds": 1.5,
        "fail_pause_seconds": 300,
        "max_consecutive_failures": 3,
        "retry_per_failure": 1,
    },
    "system_whitelist": [
        "客户联系",
        "学习园地",
        "行业资讯",
        "邮件提醒",
        "管理企业",
        "腾讯云客服",
        "企业微信团队",
        "腾讯云企业服务",
        "程明现的机器人",
    ],
    "system_keywords": ["团队", "客服", "服务", "提醒", "管理", "联系"],
}


def make_full_window(avatar=None, red_dots=()):
    """合成 1000x700 企业微信风格全窗图：左侧列表区(0~30%) + 顶部标题区(30%~100%, y0~10%)。"""
    img = Image.new("RGB", (1000, 700), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 列表区底色
    draw.rectangle([0, 0, 300, 700], fill=(245, 245, 245))
    # 红点（在列表区 y>12%）
    for y_ratio, x in red_dots:
        y = int(700 * y_ratio)
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(255, 40, 40))
    # 头像（顶部标题区左侧）
    if avatar is not None:
        img.paste(avatar, (310, 8))
    return img


def make_grid_avatar():
    """3×3 九宫格头像：每格不同纯色。"""
    img = Image.new("RGB", (120, 120))
    colors = [
        (230, 60, 60), (60, 120, 230), (60, 200, 90),
        (230, 200, 60), (160, 80, 200), (60, 200, 200),
        (230, 120, 60), (120, 120, 120), (240, 240, 240),
    ]
    d = ImageDraw.Draw(img)
    for i, color in enumerate(colors):
        r, c = divmod(i, 3)
        d.rectangle([c * 40, r * 40, c * 40 + 40, r * 40 + 40], fill=color)
    return img


def make_single_avatar():
    """单方块头像：整体蓝色渐变（连续过渡，无网格跳变）。"""
    img = Image.new("RGB", (120, 120))
    d = ImageDraw.Draw(img)
    for y in range(120):
        color = (30 + y, 60 + y // 2, 200)
        d.line([(0, y), (120, y)], fill=color)
    return img


def make_photo_avatar():
    """合影陷阱：一张多彩但连续渐变的图片（整体色彩丰富，但无子块边界跳变）。"""
    img = Image.new("RGB", (120, 120))
    d = ImageDraw.Draw(img)
    for y in range(120):
        r = 120 + int(120 * y / 120)
        g = 60 + int(120 * (120 - y) / 120)
        b = 200 - y
        d.line([(0, y), (120, y)], fill=(r, g, max(0, b)))
    for x in range(0, 120, 3):
        d.line([(x, 0), (x, 120)], fill=(200, 120, 60))
    return img


class RedDotTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ConversationListMonitor(CAL)

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_scan_finds_red_dots_sorted_by_y(self):
        img = make_full_window(red_dots=[(0.3, 100), (0.5, 150), (0.8, 250)])
        hits = self.monitor.scan(img)
        self.assertGreaterEqual(len(hits), 3)
        ys = [hit.y for hit in hits]
        self.assertEqual(ys, sorted(ys))
        # 红点都在列表区（x<300）
        for hit in hits:
            self.assertLess(hit.x_center, 300)

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_scan_ignores_no_red_dot(self):
        img = make_full_window()
        hits = self.monitor.scan(img)
        self.assertEqual(hits, [])

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_scan_ignores_large_red_area(self):
        # 大红色块（不是小圆点）不应命中
        img = make_full_window()
        d = ImageDraw.Draw(img)
        d.rectangle([10, 100, 200, 400], fill=(255, 40, 40))
        hits = self.monitor.scan(img)
        self.assertEqual(hits, [])

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_click_point_in_list_avatar_area(self):
        img = make_full_window(red_dots=[(0.5, 80)])
        hits = self.monitor.scan(img)
        self.assertTrue(hits)
        x, y = self.monitor.click_point_for(hits[0], img.size)
        # 点击点必须落在会话列表区内（x 8%~30%），不能落在最左侧工具栏（0~8%）
        self.assertGreaterEqual(x, int(1000 * 0.08))
        self.assertLess(x, int(1000 * 0.30))
        self.assertEqual(y, hits[0].y)


class TypeDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = ConversationTypeDetector(CAL)

    def test_system_whitelist_exact(self):
        ignored = (
            "客户联系",
            "学习园地",
            "行业资讯",
            "邮件提醒",
            "管理企业",
            "腾讯云客服",
            "企业微信团队",
            "腾讯云企业服务",
            "程明现的机器人",
        )
        self.assertEqual(
            [self.detector.detect(name, list_avatar_type="personal") for name in ignored],
            ["system"] * len(ignored),
        )

    def test_known_system_account_suffix_is_ignored_without_broad_prefix_matching(self):
        self.assertEqual(
            self.detector.detect("腾讯云客服@腾讯云企业", list_avatar_type="personal"),
            "system",
        )
        self.assertEqual(self.detector.detect("腾讯云客服小王", list_avatar_type="personal"), "personal")
        self.assertEqual(self.detector.detect("程明现的机器人助手", list_avatar_type="personal"), "personal")

    def test_broad_system_keywords_do_not_override_contact_avatar(self):
        self.assertEqual(self.detector.detect("某某服务", list_avatar_type="personal"), "personal")
        self.assertEqual(self.detector.detect("xx团队", list_avatar_type="personal"), "personal")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_group_grid_avatar(self):
        img = make_full_window(avatar=make_grid_avatar())
        self.assertEqual(self.detector.detect("阳光", img), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_personal_single_avatar(self):
        img = make_full_window(avatar=make_single_avatar())
        self.assertEqual(self.detector.detect("张三", img), "personal")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_explicit_external_group_subtitle_overrides_misleading_title_crop(self):
        img = make_full_window(avatar=make_single_avatar())
        title = (
            "阳光、琳、阿琳\n"
            "由企业微信用户创建的外部群，含4位外部联系人·群主："
        )

        self.assertEqual(self.detector.detect(title, img), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_photo_avatar_not_group(self):
        # 合影陷阱：整体色彩丰富但连续渐变 → 不能判成群聊
        img = make_full_window(avatar=make_photo_avatar())
        self.assertNotEqual(self.detector.detect("李四", img), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_list_row_avatar_grid_group(self):
        avatar = make_grid_avatar().resize((40, 40))
        self.assertEqual(self.detector.detect_row_avatar(avatar), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_list_row_avatar_single_personal(self):
        avatar = make_single_avatar().resize((40, 40))
        self.assertEqual(self.detector.detect_row_avatar(avatar), "personal")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_list_row_avatar_photo_not_group(self):
        avatar = make_photo_avatar().resize((40, 40))
        self.assertNotEqual(self.detector.detect_row_avatar(avatar), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_extract_row_avatar_region(self):
        img = make_full_window(red_dots=[(0.4, 150)])
        monitor = ConversationListMonitor(CAL)
        hits = monitor.scan(img)
        self.assertTrue(hits)
        avatar = monitor.extract_row_avatar(img, hits[0])
        self.assertIsNotNone(avatar)
        self.assertGreater(avatar.width, 10)
        self.assertGreater(avatar.height, 10)

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_extract_row_title_excludes_avatar_and_preview_text(self):
        img = make_full_window(red_dots=[(0.4, 150)])
        monitor = ConversationListMonitor(CAL)
        hit = monitor.scan(img)[0]

        title = monitor.extract_row_title(img, hit)

        self.assertIsNotNone(title)
        self.assertGreater(title.width, 80)
        self.assertLess(title.height, 35)

    def test_detect_with_list_avatar_type(self):
        img = make_full_window()
        self.assertEqual(self.detector.detect("阳光", img, list_avatar_type="group"), "group")
        self.assertEqual(self.detector.detect("阳光", img, list_avatar_type="personal"), "personal")
        self.assertEqual(self.detector.detect("客户联系", img, list_avatar_type="personal"), "system")

    def test_unknown_when_no_signal(self):
        self.assertEqual(self.detector.detect(""), "unknown")


class StateGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = ChatStateGuard(CAL)

    def test_requires_managed_and_self_and_stable(self):
        now = 1000.0
        # 未开托管
        self.assertFalse(self.guard.should_start_scan(False, "我", 5.0, 0, now, 0))
        # 最新消息是对方
        self.assertFalse(self.guard.should_start_scan(True, "对方", 5.0, 0, now, 0))
        # 我方但未持续 2 秒
        self.assertFalse(self.guard.should_start_scan(True, "我", 1.0, 0, now, 0))
        # 扫描间隔未到
        self.assertFalse(self.guard.should_start_scan(True, "我", 5.0, now - 0.5, now, 0))
        # 暂停期内
        self.assertFalse(self.guard.should_start_scan(True, "我", 5.0, 0, now, now + 10))
        # 全部满足
        self.assertTrue(self.guard.should_start_scan(True, "我", 5.0, now - 5.0, now, 0))

    def test_interrupted_conditions(self):
        # 对方又发消息
        self.assertTrue(self.guard.is_interrupted("对方", False, False, False, True, False))
        # 用户输入
        self.assertTrue(self.guard.is_interrupted("我", True, False, False, True, False))
        # 发送中
        self.assertTrue(self.guard.is_interrupted("我", False, True, False, True, False))
        # 处理中
        self.assertTrue(self.guard.is_interrupted("我", False, False, True, True, False))
        # 不在前台
        self.assertTrue(self.guard.is_interrupted("我", False, False, False, False, False))
        # 重试中
        self.assertTrue(self.guard.is_interrupted("我", False, False, False, True, True))
        # 无打断
        self.assertFalse(self.guard.is_interrupted("我", False, False, False, True, False))

    def test_scan_group_chat_conditions(self):
        now = 1000.0
        # 群聊：对方消息没 @ 我（group_idle=True）→ 也能触发扫描
        self.assertTrue(
            self.guard.should_start_scan(True, "对方", 5.0, now - 5.0, now, 0, chat_type="group", group_idle=True)
        )
        # 群聊：@ 我（group_idle=False）→ 不触发（要走回复流程）
        self.assertFalse(
            self.guard.should_start_scan(True, "对方", 5.0, now - 5.0, now, 0, chat_type="group", group_idle=False)
        )
        # 个人：对方消息 → 不触发
        self.assertFalse(
            self.guard.should_start_scan(True, "对方", 5.0, now - 5.0, now, 0, chat_type="personal", group_idle=True)
        )
        # 群聊：我方发出 → 触发（与个人一致）
        self.assertTrue(
            self.guard.should_start_scan(True, "我", 5.0, now - 5.0, now, 0, chat_type="group", group_idle=False)
        )


class IdentityTrackerTests(unittest.TestCase):
    def test_update_and_match(self):
        tracker = ChatIdentityTracker()
        tracker.update_from_top_title("张三")
        self.assertEqual(tracker.current_chat_key, "张三")
        self.assertTrue(tracker.matches("张三"))
        self.assertFalse(tracker.matches("李四"))
        self.assertFalse(tracker.matches(""))

    def test_empty_title_ignored(self):
        tracker = ChatIdentityTracker()
        tracker.update_from_top_title("   ")
        self.assertEqual(tracker.current_chat_key, "")


class ManagedConversationBypassTests(unittest.TestCase):
    def _app(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(
            managed_mode="full",
            chat_area=None,
            wecom_account_name="我方账号",
        )
        app.current_chat_type = "group"
        app.bypass_switch_in_progress = False
        app.bypass_self_since = 1.0
        app.bypass_last_scan_at = 0.0
        app.bypass_paused_until = 0.0
        app.bypass_retrying = False
        app.managed_send_inflight = False
        app.processing = False
        app.ocr = SimpleNamespace(read_image=lambda _image: "OCR context")
        app.group_handler = SimpleNamespace(
            latest_role_by_sender=lambda _context, _account: "我",
            detect_at_mention=lambda _context, _account: False,
            is_chat_idle=lambda _context, _account: False,
        )
        app.chat_state_guard = SimpleNamespace(
            should_start_scan=lambda *_args, **_kwargs: True,
            is_interrupted=lambda *_args, **_kwargs: False,
        )
        app._user_typing_detected = lambda: False
        return app

    def test_ready_scan_restores_verified_wecom_foreground_and_uses_fresh_bitmap(self):
        import main

        app = self._app()
        activated = []
        app.sender = SimpleNamespace(
            activate_verified_target=lambda target: activated.append(target) or True
        )
        switched = []
        app._bypass_try_switch = lambda target, bitmap: switched.append((target, bitmap))
        window = SimpleNamespace(hwnd=2468)
        old_bitmap, fresh_bitmap, chat_crop = object(), object(), object()

        with patch(
            "capture_ocr._capture_window_bitmap",
            side_effect=[old_bitmap, fresh_bitmap],
        ), patch(
            "capture_ocr._crop_detected_chat_area_with_origin",
            return_value=(chat_crop, (0, 0)),
        ), patch.object(
            main.WeChatSender,
            "_is_target_foreground",
            side_effect=[False, True],
        ), patch.object(main.time, "time", side_effect=[10.0, 10.1]):
            app._conversation_list_bypass(window)

        self.assertEqual(activated, [window])
        self.assertEqual(switched, [(window, fresh_bitmap)])

    def test_idle_group_timer_survives_other_role_until_left_list_scan_starts(self):
        import main

        app = self._app()
        app.bypass_self_since = 0.0
        app.sender = SimpleNamespace(activate_verified_target=lambda _target: True)
        app.group_handler = SimpleNamespace(
            latest_role_by_sender=lambda _context, _account: "对方",
            detect_at_mention=lambda _context, _account: False,
            is_chat_idle=lambda _context, _account: True,
        )
        app.chat_state_guard = ChatStateGuard(CAL)
        switched = []
        app._bypass_try_switch = lambda target, bitmap: switched.append((target, bitmap))
        window = SimpleNamespace(hwnd=2468)
        bitmap1, bitmap2, fresh_bitmap, chat_crop = object(), object(), object(), object()

        with patch(
            "capture_ocr._capture_window_bitmap",
            side_effect=[bitmap1, bitmap2, fresh_bitmap],
        ), patch(
            "capture_ocr._crop_detected_chat_area_with_origin",
            return_value=(chat_crop, (0, 0)),
        ), patch.object(
            main.WeChatSender,
            "_is_target_foreground",
            return_value=True,
        ), patch.object(main.time, "time", side_effect=[100.0, 103.0, 103.1]):
            app._conversation_list_bypass(window)
            app._conversation_list_bypass(window)

        self.assertEqual(switched, [(window, fresh_bitmap)])

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_switch_skips_system_unread_rows_before_clicking_actionable_chat(self):
        import main

        app = self._app()
        hits = [
            RedDotHit(y=120, x_center=270),
            RedDotHit(y=190, x_center=270),
            RedDotHit(y=260, x_center=270),
        ]
        row_titles = iter(("客户联系", "管理企业", "真实联系人"))
        scans = iter((hits, []))
        monitor = SimpleNamespace(
            scan=lambda _image: next(scans),
            extract_row_avatar=lambda _image, _hit: None,
            extract_row_title=lambda _image, _hit: next(row_titles),
            click_point_for=lambda hit, _size: (180, int(hit.y)),
        )
        app.chat_list_monitor = monitor
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.ocr = SimpleNamespace(read_chat_title=lambda value, limit=1: value)
        app.chat_identity = ChatIdentityTracker()
        app.bypass_fail_count = 0
        app.queue = SimpleNamespace(put=lambda _event: None)
        app._read_top_title = lambda image: "原会话" if image is full else "真实联系人"
        switched = []
        app._after_switch = lambda _window, title, avatar_type="": switched.append(
            (title, avatar_type)
        )
        window = SimpleNamespace(hwnd=2468, rect=SimpleNamespace(left=0, top=0))
        full = Image.new("RGB", (1000, 700), "white")
        after = Image.new("RGB", (1000, 700), "white")

        with patch("capture_ocr._capture_window_bitmap", return_value=after), patch(
            "pyautogui.click"
        ) as click, patch.object(main.time, "sleep", return_value=None):
            app._bypass_try_switch(window, full)

        click.assert_called_once_with(180, 260)
        self.assertEqual(switched, [("真实联系人", "")])
        self.assertEqual(app.bypass_fail_count, 0)

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_all_system_unread_rows_are_ignored_without_failure_pause(self):
        import main

        app = self._app()
        hits = [RedDotHit(y=120, x_center=270), RedDotHit(y=190, x_center=270)]
        row_titles = iter(("客户联系", "管理企业"))
        app.chat_list_monitor = SimpleNamespace(
            scan=lambda _image: hits,
            extract_row_avatar=lambda _image, _hit: None,
            extract_row_title=lambda _image, _hit: next(row_titles),
            click_point_for=lambda hit, _size: (180, int(hit.y)),
        )
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.ocr = SimpleNamespace(read_chat_title=lambda value, limit=1: value)
        app.chat_identity = ChatIdentityTracker()
        app.bypass_fail_count = 2
        app.queue = SimpleNamespace(put=lambda _event: None)
        app._read_top_title = lambda _image: "原会话"
        window = SimpleNamespace(hwnd=2468, rect=SimpleNamespace(left=0, top=0))
        full = Image.new("RGB", (1000, 700), "white")

        with patch("pyautogui.click") as click:
            app._bypass_try_switch(window, full)

        click.assert_not_called()
        self.assertEqual(app.bypass_fail_count, 2)

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_unreadable_row_title_fails_closed_without_click_or_failure(self):
        import main

        app = self._app()
        hit = RedDotHit(y=120, x_center=270)
        app.chat_list_monitor = SimpleNamespace(
            scan=lambda _image: [hit],
            extract_row_avatar=lambda _image, _hit: None,
            extract_row_title=lambda _image, _hit: object(),
            click_point_for=lambda _hit, _size: (180, int(_hit.y)),
        )
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.ocr = SimpleNamespace(read_chat_title=lambda _image, limit=1: "")
        app.chat_identity = ChatIdentityTracker()
        app.bypass_fail_count = 2
        app.queue = SimpleNamespace(put=lambda _event: None)
        app._read_top_title = lambda _image: "原会话"
        window = SimpleNamespace(hwnd=2468, rect=SimpleNamespace(left=0, top=0))
        full = Image.new("RGB", (1000, 700), "white")

        with patch("pyautogui.click") as click:
            app._bypass_try_switch(window, full)

        click.assert_not_called()
        self.assertEqual(app.bypass_fail_count, 2)


if __name__ == "__main__":
    unittest.main()
