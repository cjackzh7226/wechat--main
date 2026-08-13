# -*- coding: utf-8 -*-
"""group_chat_handler 模块单测：@ 识别 / 问题提取 / 发送者名字 / 回复构建 / 无变化判定。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_chat_handler import GroupChatHandler

ACCOUNT = "程正恒"


class GroupChatHandlerTests(unittest.TestCase):
    def setUp(self):
        self.handler = GroupChatHandler()

    # ---- detect_at_mention ----
    def test_mention_at_beginning(self):
        context = "[对方] 阳光@微信\n[对方] @程正恒 民非单位未按规定换届怎么整改"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_with_colon(self):
        context = "[对方] @程正恒：你好"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_with_spaces_in_name(self):
        context = "[对方] @程 正恒 你好"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_no_mention(self):
        context = "[对方] 阳光@微信\n[对方] 民非单位未按规定换届怎么整改"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_not_at_start(self):
        context = "[对方] 你好 @程正恒"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_other_name(self):
        context = "[对方] @李四 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_prefix_of_account_name_is_rejected(self):
        context = "[对方] @程 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_longer_name_with_same_prefix_is_rejected(self):
        context = "[对方] @程正恒丰 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_real_mention_tag_concatenated_by_ocr_is_accepted(self):
        account = "\u7a0b\u6b63\u6052"
        question = "\u5e74\u68c0\u6750\u6599\u88ab\u9000\u56de\u5728\u54ea\u91cc\u67e5\u770b\u9000\u56de\u95ee\u9898\uff1f"
        context = f"[\u5bf9\u65b9] @{account}{question}"

        self.assertTrue(self.handler.detect_at_mention(context, account))
        self.assertEqual(
            self.handler.extract_question(f"@{account}{question}", account),
            "\u5e74\u68c0\u6750\u6599\u88ab\u9000\u56de\u5728\u54ea\u91cc\u67e5\u770b\u9000\u56de\u95ee\u9898?",
        )

    def test_mention_empty_account(self):
        context = "[对方] @程正恒 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ""))

    def test_mention_punctuation_variants(self):
        for variant in ("@程正恒：问题", "@程正恒:问题", "@程正恒，问题", "@程正恒 问题"):
            context = f"[对方] {variant}"
            self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT), variant)

    # ---- extract_question ----
    def test_extract_question_basic(self):
        self.assertEqual(
            self.handler.extract_question("@程正恒 民非单位未按规定换届怎么整改", ACCOUNT),
            "民非单位未按规定换届怎么整改",
        )

    def test_extract_question_colon(self):
        self.assertEqual(self.handler.extract_question("@程正恒：你好", ACCOUNT), "你好")

    def test_extract_question_no_mention(self):
        self.assertEqual(self.handler.extract_question("民非单位怎么整改", ACCOUNT), "民非单位怎么整改")

    def test_extract_question_spaced_name(self):
        self.assertEqual(self.handler.extract_question("@程 正恒 你好", ACCOUNT), "你好")

    # ---- extract_sender_name ----
    def test_sender_plain_at_wechat(self):
        context = "[对方] 阳光@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "阳光")

    def test_sender_at_dash_at_wechat(self):
        context = "[对方] -@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "-")

    def test_explicit_sender_header_is_metadata_not_a_message(self):
        context = "[发送者] -@微信\n[对方] @程正恒 年检材料被退回在哪里查看退回问题？"

        self.assertEqual(
            self.handler.latest_other_message(context),
            "@程正恒 年检材料被退回在哪里查看退回问题？",
        )
        self.assertEqual(self.handler.extract_sender_name(context), "-")
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_sender_parentheses_wecom(self):
        context = "[对方] 张三(企业微信)\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "张三")

    def test_sender_platform_marker_is_case_insensitive(self):
        context = "[对方] Alice(wecom)\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "Alice")

    def test_sender_dot_wechat(self):
        context = "[对方] 李四·微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "李四")

    def test_sender_at_dash_at_wechat_full(self):
        context = "[对方] 阳光@-@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "阳光")

    def test_sender_requires_a_recognized_sender_header(self):
        context = "[对方] 这是一条普通消息"
        self.assertEqual(self.handler.extract_sender_name(context), "")

    # ---- build_reply ----
    def test_build_reply(self):
        self.assertEqual(
            self.handler.build_reply("阳光", "民非单位未按规定换届，应当..."),
            "@阳光 民非单位未按规定换届，应当...",
        )

    def test_build_reply_no_sender(self):
        self.assertEqual(self.handler.build_reply("", "内容"), "内容")

    # ---- is_chat_idle ----
    def test_idle_when_other_no_mention(self):
        context = "[对方] 阳光@微信\n[对方] 你好"
        self.assertTrue(self.handler.is_chat_idle(context, ACCOUNT))

    def test_not_idle_when_mentioned(self):
        context = "[对方] @程正恒 民非单位怎么整改"
        self.assertFalse(self.handler.is_chat_idle(context, ACCOUNT))

    def test_not_idle_when_self(self):
        context = "[对方] 你好\n[我] 已回复"
        self.assertFalse(self.handler.is_chat_idle(context, ACCOUNT))

    def test_idle_when_empty_context(self):
        self.assertTrue(self.handler.is_chat_idle("", ACCOUNT))

    # ---- latest_role_by_sender（按发送者名字判角色，修复"阳光被识别成我"）----
    def test_role_by_sender_other(self):
        # 阳光 发的长消息（即使被位置规则标成 [我] 标签，按名字也判"对方"）
        context = "[我] 阳光@微信\n[对方] @程正恒 想查一下之前的年报"
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")

    def test_role_by_sender_self(self):
        context = "[对方] 阳光@微信\n[我] 程正恒@微信"
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "我")

    def test_role_by_sender_skips_at_content_line(self):
        # 最新一行是 @ 开头的内容行 → 跳过找上面名字行
        context = "[对方] 阳光@微信\n[对方] @程正恒 1234567"
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")

    def test_role_by_sender_empty(self):
        self.assertEqual(self.handler.latest_role_by_sender("", ACCOUNT), "")


if __name__ == "__main__":
    unittest.main()
