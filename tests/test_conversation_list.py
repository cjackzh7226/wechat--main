# -*- coding: utf-8 -*-
"""conversation_list 模块单测：红点检测 / 四分类 / 状态机（用合成图，不依赖真实窗口）。"""

import sys
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        "self_stable_seconds": 1.2,
        "scan_interval_seconds": 0.8,
        "switch_wait_seconds": 1.2,
        "user_typing_seconds": 1.5,
        "fail_pause_seconds": 300,
        "max_consecutive_failures": 3,
        "retry_per_failure": 1,
    },
    "system_whitelist": [
        "客户联系", "学习园地", "行业资讯", "邮件提醒", "管理企业",
        "腾讯云客服", "企业微信团队", "腾讯云企业服务", "程明现的机器人",
        "可能的商务伙伴", "一周小结", "接收微信中的工作消息", "客户咨询", "微信客服",
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

    def test_keeps_calibration_for_title_crop(self):
        self.assertEqual(self.detector.cal["top_title_area"], CAL["top_title_area"])

    def test_system_whitelist_exact(self):
        self.assertEqual(self.detector.detect("客户联系"), "system")
        self.assertEqual(self.detector.detect("邮件提醒"), "system")
        self.assertEqual(self.detector.detect("管理企业"), "system")

    def test_six_new_non_chat_entries_are_system(self):
        for title in (
            "可能的商务伙伴",
            "一周小结",
            "行业资讯",
            "接收微信中的工作消息",
            "客户咨询",
            "微信客服",
        ):
            self.assertEqual(self.detector.detect_title(title), "system", title)

    def test_dynamic_customer_consultation_suffix_is_system(self):
        self.assertEqual(self.detector.detect_title("客户咨询·海峡非营利组织"), "system")

    def test_common_customer_contact_ocr_confusion_is_system(self):
        self.assertEqual(self.detector.detect_title("客户联糸"), "system")

    def test_system_prefix_match(self):
        self.assertEqual(self.detector.detect("腾讯云客服@abc"), "system")

    def test_system_detection_does_not_use_broad_keyword_fallback(self):
        self.assertNotEqual(self.detector.detect("某某服务"), "system")
        self.assertNotEqual(self.detector.detect("客户服务项目交流群"), "system")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_group_grid_avatar(self):
        img = make_full_window(avatar=make_grid_avatar())
        self.assertEqual(self.detector.detect("阳光", img), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_personal_single_avatar(self):
        img = make_full_window(avatar=make_single_avatar())
        self.assertEqual(self.detector.detect("张三", img), "personal")

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

    def test_detect_with_list_avatar_type(self):
        img = make_full_window()
        self.assertEqual(self.detector.detect("阳光", img, list_avatar_type="group"), "group")
        self.assertEqual(self.detector.detect("阳光", img, list_avatar_type="personal"), "personal")
        self.assertEqual(self.detector.detect("客户联系", img, list_avatar_type="personal"), "system")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_wechat_contact_marker_overrides_colorful_avatar_false_group(self):
        img = make_full_window(avatar=make_grid_avatar())
        self.assertEqual(self.detector.detect("- @微信", img, list_avatar_type="group"), "personal")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_multiple_member_names_override_avatar_false_personal(self):
        img = make_full_window(avatar=make_single_avatar())
        self.assertEqual(self.detector.detect("张三、李四、王五", img, list_avatar_type="personal"), "group")

    @unittest.skipIf(Image is None, "PIL 不可用")
    def test_empty_title_never_promotes_avatar_guess_to_group(self):
        img = make_full_window(avatar=make_grid_avatar())
        self.assertEqual(self.detector.detect("", img, list_avatar_type="group"), "unknown")

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
        # 我方但未持续 1.2 秒
        self.assertFalse(self.guard.should_start_scan(True, "我", 1.0, 0, now, 0))
        # 扫描间隔未到
        self.assertFalse(self.guard.should_start_scan(True, "我", 5.0, now - 0.5, now, 0))
        # 暂停期内
        self.assertFalse(self.guard.should_start_scan(True, "我", 5.0, 0, now, now + 10))
        # 全部满足
        self.assertTrue(self.guard.should_start_scan(True, "我", 5.0, now - 5.0, now, 0))

    def test_stable_chat_starts_red_dot_scan_after_one_point_two_seconds(self):
        now = 1000.0
        self.assertEqual(self.guard.self_stable_seconds, 1.2)
        self.assertFalse(self.guard.should_start_scan(True, "我", 1.19, 0, now, 0))
        self.assertTrue(self.guard.should_start_scan(True, "我", 1.20, 0, now, 0))

    def test_red_dot_scan_interval_is_eight_hundred_milliseconds(self):
        now = 1000.0
        self.assertEqual(self.guard.scan_interval_seconds, 0.8)
        self.assertFalse(self.guard.should_start_scan(True, "我", 5.0, now - 0.79, now, 0))
        self.assertTrue(self.guard.should_start_scan(True, "我", 5.0, now - 0.80, now, 0))

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

    def test_recall_system_event_can_enter_scan_gate(self):
        now = 1000.0
        self.assertTrue(
            self.guard.should_start_scan(True, "系统", 2.0, now - 5.0, now, 0, chat_type="personal")
        )
        self.assertFalse(self.guard.is_interrupted("系统", False, False, False, True, False))


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


@unittest.skipIf(Image is None, "PIL 不可用")
class MainConversationRoutingTests(unittest.TestCase):
    def test_group_model_generation_does_not_block_monitor_loop(self):
        import main
        from group_chat_handler import GroupChatHandler

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(wecom_account_name="程正恒", chat_area=SimpleNamespace())
        app.queue = Queue()
        app.group_handler = GroupChatHandler(app.settings)
        app.group_last_handled_msg = ""
        app.group_generation_lock = threading.Lock()
        app.group_generation_inflight = False
        app.pending_group_generation = None
        app.engine = MagicMock()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_generate(*_args, **_kwargs):
            started.set()
            release.wait(1.0)
            finished.set()
            return []

        app.engine.generate.side_effect = slow_generate
        context = "[发送者] 阳光@微信\n[对方] @程正恒 最新问题"
        timer = threading.Timer(0.3, release.set)
        timer.start()
        begin = time.perf_counter()
        app._handle_group_chat(SimpleNamespace(), context=context, full=object())
        elapsed = time.perf_counter() - begin

        self.assertTrue(started.wait(0.5))
        self.assertLess(elapsed, 0.15)
        self.assertTrue(finished.wait(1.0))

    def test_group_send_preflight_discards_old_reply_after_latest_event_changes(self):
        import main
        from group_chat_handler import GroupChatHandler

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(wecom_account_name="程正恒", chat_area=SimpleNamespace())
        app.group_handler = GroupChatHandler(app.settings)
        app.ocr = MagicMock()
        app._read_top_title = MagicMock(return_value="阳光、琳、阿琳")
        old_message = "@程正恒 旧问题"
        live_contexts = (
            "[发送者] 阿琳@微信\n[对方] 大家下午好",
            "[发送者] 阳光@微信\n[对方] @程正恒 旧问题\n[系统] 来自微信的阳光撤回了一条消息",
        )

        with (
            patch("main.WeChatSender._is_target_foreground", return_value=True),
            patch("capture_ocr._capture_window_bitmap", return_value=object()),
            patch("capture_ocr._crop_detected_chat_area_with_origin", return_value=(object(), (0, 0))),
        ):
            for live_context in live_contexts:
                with self.subTest(live_context=live_context):
                    app.ocr.read_image.return_value = live_context
                    allowed, _reason = app._group_send_preflight_allows(
                        SimpleNamespace(),
                        old_message,
                        "阳光",
                        "阳光、琳、阿琳",
                    )
                    self.assertFalse(allowed)

    def test_same_group_question_can_be_handled_again_after_a_message_boundary(self):
        import main
        from group_chat_handler import GroupChatHandler

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(wecom_account_name="程正恒", chat_area=SimpleNamespace())
        app.queue = Queue()
        app.group_handler = GroupChatHandler(app.settings)
        app.group_last_handled_msg = ""
        app._read_top_title = MagicMock(return_value="阳光、琳、阿琳")
        app._schedule_group_generation = MagicMock()
        window = SimpleNamespace()
        mentioned = "[发送者] 阳光@微信\n[对方] @程正恒 同一个问题"

        app._handle_group_chat(window, context=mentioned, full=object())
        app._handle_group_chat(window, context=mentioned, full=object())
        app._handle_group_chat(
            window,
            context="[发送者] 阿琳@微信\n[对方] 大家下午好",
            full=object(),
        )
        app._handle_group_chat(window, context=mentioned, full=object())

        self.assertEqual(app._schedule_group_generation.call_count, 2)

    def test_group_generation_exception_resets_worker_and_reports_status(self):
        import main
        from group_chat_handler import GroupChatHandler

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(wecom_account_name="程正恒")
        app.group_handler = GroupChatHandler(app.settings)
        app.queue = Queue()
        app.engine = MagicMock()
        app.engine.generate.side_effect = RuntimeError("model unavailable")
        app.group_generation_lock = threading.Lock()
        app.group_generation_inflight = True
        app.pending_group_generation = None
        job = (
            SimpleNamespace(),
            "[对方] @程正恒 问题",
            "@程正恒 问题",
            "问题",
            "阳光",
            "阳光、琳、阿琳",
        )

        app._group_generation_worker(job)

        self.assertFalse(app.group_generation_inflight)
        self.assertIn("群聊回复生成异常", app.queue.get_nowait()[1])

    def test_monitor_refreshes_conversation_type_before_personal_processing(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        window = SimpleNamespace(hwnd=9, title="企业微信")
        app.running = True
        app.detector = MagicMock()
        app.detector.foreground_chat.return_value = window
        app.settings = SimpleNamespace(
            auto_recognize=True,
            training_mode=False,
            managed_auto_reply=True,
            capture_cooldown_seconds=0.0,
        )
        app.queue = Queue()
        app.current_window = None
        app.last_window_key = ""
        app.current_hash = ""
        app.last_capture = 0.0
        app.force_refresh = False
        app.managed_send_inflight = False
        order = []

        def refresh_type(_window):
            order.append("refresh_type")
            app.current_chat_type = "group"

        def process(_window, force=False):
            order.append("process")
            app.running = False

        app._conversation_list_bypass = MagicMock(side_effect=refresh_type)
        app._process_window = MagicMock(side_effect=process)
        app._poll_interval = MagicMock(return_value=800.0)

        with patch("main.time.time", return_value=100.0), patch("main.time.sleep"):
            app._monitor_loop()

        self.assertEqual(order, ["refresh_type", "process"])

    def test_manual_switch_from_group_to_wechat_contact_refreshes_personal_type(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.current_chat_type = "group"
        app.current_hash = "old-hash"
        app.current_context = "old-context"
        app.force_refresh = False
        app.queue = Queue()

        app._refresh_current_chat_type("- @微信")

        self.assertEqual(app.current_chat_type, "personal")
        self.assertEqual(app.current_hash, "")
        self.assertEqual(app.current_context, "")
        self.assertTrue(app.force_refresh)
        self.assertIn("个人会话", app.queue.get_nowait()[1])

    def test_idle_group_accumulates_stability_and_triggers_unread_scan(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.settings = SimpleNamespace(
            managed_auto_reply=True,
            chat_area=SimpleNamespace(),
            wecom_account_name="程正恒",
        )
        app.current_chat_type = "group"
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.chat_state_guard = ChatStateGuard(CAL)
        app.group_handler = MagicMock()
        app.group_handler.latest_role_by_sender.return_value = "对方"
        app.group_handler.detect_at_mention.return_value = False
        app.group_handler.is_chat_idle.return_value = True
        app.ocr = MagicMock()
        app.ocr.read_image.return_value = "[发送者] 张三@微信\n[对方] 普通群消息"
        app._read_top_title = MagicMock(return_value="张三、李四、王五")
        app._user_typing_detected = MagicMock(return_value=False)
        app._bypass_try_switch = MagicMock()
        app.bypass_switch_in_progress = False
        app.bypass_self_since = 0.0
        app.bypass_last_scan_at = 0.0
        app.bypass_paused_until = 0.0
        app.bypass_retrying = False
        app.managed_send_inflight = False
        app.processing = False
        app.current_hash = ""
        app.current_context = ""
        app.force_refresh = False
        app.queue = Queue()
        window = SimpleNamespace()
        full = make_full_window()

        with (
            patch("capture_ocr._capture_window_bitmap", return_value=full),
            patch("capture_ocr._crop_detected_chat_area_with_origin", return_value=(full, (0, 0))),
            patch("main.WeChatSender._is_target_foreground", return_value=True),
            patch("main.time.time", side_effect=[100.0, 103.0]),
        ):
            app._conversation_list_bypass(window)
            app._conversation_list_bypass(window)

        app._bypass_try_switch.assert_called_once_with(window, full)

    def test_unread_switch_skips_system_entry_and_uses_group_row_title(self):
        import main

        app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
        app.chat_type_detector = ConversationTypeDetector(CAL)
        app.chat_state_guard = ChatStateGuard(CAL)
        app.chat_list_monitor = MagicMock()
        system_hit = RedDotHit(y=120, x_center=200)
        group_hit = RedDotHit(y=220, x_center=200)
        app.chat_list_monitor.scan.side_effect = [[system_hit, group_hit], []]
        system_crop = object()
        group_crop = object()
        app.chat_list_monitor.extract_row_title.side_effect = [system_crop, group_crop]
        app.chat_list_monitor.extract_row_avatar.return_value = make_grid_avatar().resize((45, 44))
        app.chat_list_monitor.click_point_for.return_value = (180, 220)
        app.ocr = MagicMock()
        app.ocr.read_chat_title.side_effect = lambda crop, limit=1: (
            "客户联系" if crop is system_crop else "张三、李四、王五"
        )
        app._read_top_title = MagicMock(side_effect=["旧会话", ""])
        app._user_typing_detected = MagicMock(return_value=False)
        app._after_switch = MagicMock()
        app.chat_identity = MagicMock()
        app.managed_send_inflight = False
        app.processing = False
        app.bypass_retrying = False
        app.bypass_fail_count = 0
        app.bypass_paused_until = 0.0
        app.queue = Queue()
        window = SimpleNamespace(rect=SimpleNamespace(left=10, top=20))
        full = make_full_window()
        full_after = make_full_window()

        with (
            patch("capture_ocr._capture_window_bitmap", return_value=full_after),
            patch("main.WeChatSender._is_target_foreground", return_value=True),
            patch("pyautogui.click") as click,
            patch("main.time.sleep"),
        ):
            app._bypass_try_switch(window, full)

        click.assert_called_once_with(190, 240)
        app._after_switch.assert_called_once_with(window, "张三、李四、王五", "group")


if __name__ == "__main__":
    unittest.main()
