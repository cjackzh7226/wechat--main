# -*- coding: utf-8 -*-
"""群聊客服处理器（企业微信 5.0.9.6060）。

职责（按 docs/群聊客服功能_功能交接.md）：
- detect_at_mention：最新对方消息开头是否 @ 了当前账号用户名（容错匹配）
- extract_question：从 @ 我的消息里提取真正的问题内容
- extract_sender_name：识别 @ 我的消息的发送者名字（去掉平台标记后缀）
- build_reply：构建 "@发送者名字 回复内容"
- is_chat_idle：群聊"聊天内容无变化"（对方消息没 @ 我）判定
"""

from __future__ import annotations

import re
import unicodedata

# 平台标记后缀变体（企业微信 5.0.9 实测 + 常见变体，OCR 容错）
_PLATFORM_MARKERS = (
    "@-@微信",
    "@微信",
    "(企业微信)",
    "(微信)",
    "·微信",
    "·企业微信",
    "@企业微信",
    "(wechat)",
    "(WeCom)",
    "@WeCom",
    "@-@WeCom",
)

# @ 后的分隔标点（OCR 可能识别出 : ： ， , 空格等）
_MENTION_SEPARATORS = "：:，,、。 ."


def _clean_name(text: str) -> str:
    """去掉所有空白，用于名字比较与提取。"""
    return re.sub(r"[\s\u3000]+", "", unicodedata.normalize("NFKC", text or ""))


def _exact_mention_end(message: str, account_name: str) -> int | None:
    """Return the end offset of an exact leading @name, tolerating OCR spaces."""
    text = unicodedata.normalize("NFKC", message or "").lstrip()
    expected = _clean_name(account_name)
    if not text.startswith("@") or not expected:
        return None
    index = 1
    while index < len(text) and text[index].isspace():
        index += 1
    for expected_char in expected:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index].casefold() != expected_char.casefold():
            return None
        index += 1
    if index < len(text) and not (
        text[index].isspace() or text[index] in "：:，,、。.!！?？"
    ):
        tail = text[index:]
        if len(tail.strip()) < 2:
            return None
        if re.match(
            r"^[A-Za-z\u3400-\u9fff]{1,3}(?:\s|[:\uff1a,\uff0c\u3001\u3002.!\uff01\uff1f?])",
            tail,
        ):
            return None
    return index


def _strip_platform_marker(text: str) -> str:
    """去掉发送者头部里的平台标记后缀（保留名字本身，包括 '-' 等符号）。"""
    cleaned = (text or "").strip()
    for marker in _PLATFORM_MARKERS:
        cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _line_text(line: str) -> str:
    return line.split("]", 1)[1].strip() if "]" in line else line.strip()


def _is_sender_header_line(line: str) -> bool:
    return line.startswith("[发送者]")


def _has_platform_marker(text: str) -> bool:
    folded = (text or "").casefold()
    return any(marker.casefold() in folded for marker in _PLATFORM_MARKERS)


def _latest_other_message(context: str) -> str:
    """取最新一条 role==对方 的消息文本。"""
    for line in reversed((context or "").splitlines()):
        if line.startswith("[对方]") or line.startswith("[瀵规柟]"):
            return line.split("]", 1)[1].strip() if "]" in line else line.strip()
    return ""


def _latest_other_header(context: str) -> str:
    """取最新一条对方消息的发送者头部（名字+平台标记）。"""
    for line in reversed((context or "").splitlines()):
        if line.startswith("[对方]") or line.startswith("[瀵规柟]"):
            return line.split("]", 1)[1].strip() if "]" in line else ""
    return ""


class GroupChatHandler:
    """群聊客服处理器。"""

    def __init__(self, settings=None) -> None:
        self.settings = settings

    @staticmethod
    def latest_other_message(context: str) -> str:
        """最新一条对方消息文本（供 main 提取问题用）。"""
        return _latest_other_message(context)

    def latest_role_by_sender(self, context: str, account_name: str) -> str:
        """群聊里最新消息角色：按发送者名字判断（"我"/"对方"/""）。

        群聊 OCR 的 [对方]/[我] 标签来自气泡位置规则，长消息内容行会误判
        （如"阳光"的长消息被判成"我"）。改为看发送者名字行：
        名字 == 当前账号用户名 → "我"；其他名字 → "对方"；@ 开头的内容行跳过向上找名字行。
        """
        name = _clean_name(account_name)
        lines = (context or "").splitlines()
        meaningful = [index for index, line in enumerate(lines) if _line_text(line)]
        if meaningful:
            latest_index = meaningful[-1]
            latest_line = lines[latest_index]
            latest_text = _line_text(latest_line)
            if latest_line.startswith(("[我]", "[鎴]")) and not _has_platform_marker(
                latest_text
            ):
                previous_index = meaningful[-2] if len(meaningful) >= 2 else -1
                # 我方群聊气泡通常没有“发送者”头。若它不是紧跟在
                # 对方发送者头后的首行，就不能继续向上倒找并借用旧昵称。
                if previous_index < 0 or not _is_sender_header_line(lines[previous_index]):
                    return "我"
        for line in reversed(lines):
            text = _line_text(line)
            if not text:
                continue
            if _is_sender_header_line(line):
                sender = _strip_platform_marker(text)
            elif text.startswith("@") or not _has_platform_marker(text):
                # 内容行（@ 开头）无发送者名字，向上找名字行
                continue
            else:
                # 兼容旧版 OCR：发送者头曾被错误标成 [对方]/[我]。
                sender = _strip_platform_marker(text)
            if not sender:
                continue
            if name and sender == name:
                return "我"
            return "对方"
        return ""

    def detect_at_mention(self, context: str, account_name: str) -> bool:
        """检测最新对方消息开头是否 @ 了当前账号用户名。

        规则：@ 必须在消息第一个字符；比较前都去空格；@ 后第一个词与用户名
        相等或互为前缀（容错 OCR 在名字中插空格，如"@程 正恒"）。
        """
        if not account_name:
            return False
        message = _latest_other_message(context)
        if not message:
            return False
        return _exact_mention_end(message, account_name) is not None

    def extract_question(self, message: str, account_name: str) -> str:
        """从 @ 我的消息里提取问题内容（去掉 @用户名 前缀及分隔标点）。"""
        text = unicodedata.normalize("NFKC", message or "").lstrip()
        end = _exact_mention_end(text, account_name)
        if end is None:
            return text.strip()
        rest = text[end:]
        rest = rest.lstrip("：:，,、。 .")
        return rest.strip()

    def extract_sender_name(self, context: str) -> str:
        """从最新对方消息识别发送者名字（去平台标记）。

        覆盖两种 OCR 行格式：
        1. 单行合并：`阳光@微信 @程正恒 问题` → 取 @ 前的头部
        2. 上下双行：`阳光@微信`（上）+ `@程正恒 问题`（下）→ 内容行 @ 在行首时取上一行
        """
        all_lines = (context or "").splitlines()
        latest_other_index = next(
            (
                index
                for index in range(len(all_lines) - 1, -1, -1)
                if all_lines[index].startswith("[对方]") or all_lines[index].startswith("[瀵规柟]")
            ),
            -1,
        )
        if latest_other_index >= 0:
            for index in range(latest_other_index - 1, -1, -1):
                line = all_lines[index]
                if _is_sender_header_line(line):
                    header = _line_text(line)
                    if _has_platform_marker(header):
                        return _strip_platform_marker(header)
                    return ""
                if line.startswith(("[对方]", "[瀵规柟]", "[我]", "[鎴]")):
                    break

        lines = [
            line for line in (context or "").splitlines()
            if line.startswith("[对方]") or line.startswith("[瀵规柟]")
        ]
        if not lines:
            return ""
        last = lines[-1].split("]", 1)[1].strip() if "]" in lines[-1] else lines[-1].strip()
        # 找行内第一个 @：@ 前的部分即名字+平台标记（单行合并格式）
        match = re.search(r"@\S+", last)
        head = last[: match.start()].strip() if match else last
        if not head:
            # 双行格式：内容行 @ 在行首，头部在上一行
            if len(lines) >= 2:
                upper = lines[-2].split("]", 1)[1].strip() if "]" in lines[-2] else lines[-2].strip()
                head = upper
        if not _has_platform_marker(head):
            return ""
        return _strip_platform_marker(head)

    def build_reply(self, sender_name: str, reply_content: str) -> str:
        """构建回复：@发送者名字 回复内容（文字 @，非真 @ 提醒）。"""
        name = (sender_name or "").strip()
        content = (reply_content or "").strip()
        if not name or not content:
            return content
        return f"@{name} {content}"

    def is_chat_idle(self, context: str, account_name: str) -> bool:
        """群聊"聊天内容无变化"：最新消息为对方 且 没有开头 @ 我。

        用于扩展触发列表扫描的条件（没 @ 我的消息无需回复 → 可切走）。
        """
        if not context or not context.strip():
            return True
        latest_role = ""
        for line in reversed(context.splitlines()):
            if line.startswith("[对方]") or line.startswith("[瀵规柟]"):
                latest_role = "对方"
                break
            if line.startswith("[我]") or line.startswith("[鎴]"):
                latest_role = "我"
                break
        if latest_role != "对方":
            return False
        return not self.detect_at_mention(context, account_name)
