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

    def test_leading_mention_survives_ocr_dropped_separator_before_question(self):
        context = "[发送者] -@微信\n[对方] @程正恒负责人超过理事数的1/3"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))
        self.assertEqual(
            self.handler.extract_question(self.handler.latest_other_message(context), ACCOUNT),
            "负责人超过理事数的1/3",
        )

    def test_no_mention(self):
        context = "[对方] 阳光@微信\n[对方] 民非单位未按规定换届怎么整改"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_in_middle_is_not_a_trigger(self):
        context = "[对方] 你好 @程正恒 请看前面的记录"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_at_end_is_a_trigger(self):
        context = "[发送者] 阳光@微信\n[对方] 负责人超过理事数的1/3 @程正恒\u2005\u2005"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_at_end_allows_ocr_spaces_inside_full_name(self):
        context = "[发送者] 阳光@微信\n[对方] 负责人超过理事数的1/3 @程 正恒 "
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_at_end_normalizes_fullwidth_at_and_terminal_punctuation(self):
        context = "[发送者] 阳光@微信\n[对方] 负责人超过理事数的1/3　＠程正恒。"
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_at_either_boundary_supports_latin_account_names(self):
        self.assertTrue(self.handler.detect_at_mention("[对方] @Alice: Please check", "Alice"))
        self.assertTrue(self.handler.detect_at_mention("[对方] Please check @Alice", "Alice"))
        self.assertFalse(self.handler.detect_at_mention("[对方] Please @Alice check", "Alice"))

    def test_partial_or_extended_name_is_not_a_trigger_at_either_boundary(self):
        for message in (
            "@程 问题",
            "@程正 问题",
            "@程正恒1 问题",
            "问题 @程",
            "问题 @程正",
            "问题 @程正恒1",
        ):
            with self.subTest(message=message):
                self.assertFalse(self.handler.detect_at_mention(f"[对方] {message}", ACCOUNT))

    def test_mention_other_name(self):
        context = "[对方] @李四 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_mention_empty_account(self):
        context = "[对方] @程正恒 你好"
        self.assertFalse(self.handler.detect_at_mention(context, ""))

    def test_mention_punctuation_variants(self):
        for variant in ("@程正恒：问题", "@程正恒:问题", "@程正恒，问题", "@程正恒 问题"):
            context = f"[对方] {variant}"
            self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT), variant)

    def test_latest_own_message_blocks_older_mention(self):
        context = "[发送者] 阳光@微信\n[对方] @程正恒 旧问题\n[我] 已经回复"
        self.assertEqual(self.handler.latest_other_message(context), "")
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))

    def test_latest_recall_blocks_older_mention(self):
        context = "[发送者] 阳光@微信\n[对方] @程正恒 旧问题\n[系统] 来自微信的阳光撤回了一条消息"
        self.assertEqual(self.handler.latest_other_message(context), "")
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "系统")
        self.assertTrue(self.handler.is_chat_idle(context, ACCOUNT))

    def test_latest_non_mention_blocks_older_mention(self):
        context = (
            "[发送者] 阳光@微信\n"
            "[对方] @程正恒 旧问题\n"
            "[发送者] 阿琳@微信\n"
            "[对方] 大家下午好"
        )
        self.assertEqual(self.handler.latest_other_message(context), "大家下午好")
        self.assertFalse(self.handler.detect_at_mention(context, ACCOUNT))
        self.assertTrue(self.handler.is_chat_idle(context, ACCOUNT))

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

    def test_extract_question_when_full_mention_is_at_end(self):
        self.assertEqual(
            self.handler.extract_question("负责人超过理事数的1/3 @程正恒\u2005\u2005", ACCOUNT),
            "负责人超过理事数的1/3",
        )

    # ---- extract_sender_name ----
    def test_sender_plain_at_wechat(self):
        context = "[对方] 阳光@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "阳光")

    def test_sender_at_dash_at_wechat(self):
        context = "[对方] -@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "-")

    def test_sender_parentheses_wecom(self):
        context = "[对方] 张三(企业微信)\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "张三")

    def test_sender_dot_wechat(self):
        context = "[对方] 李四·微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "李四")

    def test_sender_at_dash_at_wechat_full(self):
        context = "[对方] 阳光@-@微信\n[对方] @程正恒 问题"
        self.assertEqual(self.handler.extract_sender_name(context), "阳光")

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

    def test_explicit_sender_header_is_metadata_not_a_message(self):
        context = "[发送者] -@微信\n[对方] @程正恒 年检材料被退回在哪里查看退回问题？"

        self.assertEqual(
            self.handler.latest_other_message(context),
            "@程正恒 年检材料被退回在哪里查看退回问题？",
        )
        self.assertEqual(self.handler.extract_sender_name(context), "-")
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")
        self.assertTrue(self.handler.detect_at_mention(context, ACCOUNT))

    def test_role_by_sender_keeps_latest_own_reply_without_sender_header(self):
        context = (
            "[发送者] -@微信\n"
            "[对方] @程正恒 社会团体怎么办？\n"
            "[我] @- 请按规定办理。"
        )

        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "我")

    def test_role_by_sender_still_uses_adjacent_header_for_misplaced_other_bubble(self):
        context = "[发送者] 阳光@微信\n[我] 这是被位置规则误标的对方长消息"

        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")

    def test_role_by_sender_skips_at_content_line(self):
        # 最新一行是 @ 开头的内容行 → 跳过找上面名字行
        context = "[对方] 阳光@微信\n[对方] @程正恒 1234567"
        self.assertEqual(self.handler.latest_role_by_sender(context, ACCOUNT), "对方")

    def test_role_by_sender_empty(self):
        self.assertEqual(self.handler.latest_role_by_sender("", ACCOUNT), "")


if __name__ == "__main__":
    unittest.main()
