# -*- coding: utf-8 -*-
"""群聊客服处理器（企业微信 5.0.9.6060）。

职责（按 docs/群聊客服功能_功能交接.md）：
- detect_at_mention：最新对方消息开头或结尾是否完整 @ 了当前账号用户名
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
_MENTION_SEPARATORS = "：:，,、。 .；;！!？?"
_INVISIBLE_SPACES = "\u200b\u200c\u200d\u2060\ufeff"


def _clean_name(text: str) -> str:
    """去掉所有空白，用于名字比较与提取。"""
    return re.sub(
        rf"[\s\u3000{_INVISIBLE_SPACES}]+",
        "",
        unicodedata.normalize("NFKC", text or ""),
    )


def _is_mention_boundary_char(char: str) -> bool:
    return bool(
        char
        and (
            char.isspace()
            or char in _INVISIBLE_SPACES
            or unicodedata.category(char).startswith("P")
        )
    )


def _only_mention_boundaries(text: str) -> bool:
    return all(_is_mention_boundary_char(char) for char in text)


def _only_ignorable_space(text: str) -> bool:
    return all(char.isspace() or char in _INVISIBLE_SPACES for char in text)


def _looks_like_joined_question_suffix(text: str) -> bool:
    """Recognize non-ASCII question text joined to a leading mention by OCR.

    An ASCII letter/digit immediately after the configured name remains blocked,
    because it can be part of a longer account name (for example ``@Alice1``).
    """
    cleaned = (text or "").strip(_INVISIBLE_SPACES)
    if len(cleaned) < 2:
        return False
    return not (cleaned[0].isascii() and cleaned[0].isalnum())


def _find_account_mention_span(message: str, account_name: str) -> tuple[int, int, str] | None:
    """Return an exact full-account mention anchored at message start or end."""
    text = unicodedata.normalize("NFKC", message or "")
    name = _clean_name(account_name)
    if not text or not name:
        return None

    # OCR 可能在姓名中插入空格；仍须逐字匹配完整账号名，不能降级为前缀。
    gap = rf"[\s\u3000{_INVISIBLE_SPACES}]*"
    pattern = re.compile("@" + gap.join(re.escape(char) for char in name))
    for matched in pattern.finditer(text):
        before = text[: matched.start()]
        after = text[matched.end() :]
        leading = _only_ignorable_space(before)
        if leading and (
            not after
            or _is_mention_boundary_char(after[0])
            or _looks_like_joined_question_suffix(after)
        ):
            # OCR 偶尔把“@账号 问题”合并成“@账号问题”。只对完整姓名后的
            # 明显正文放宽；紧跟数字/拉丁字符仍视为更长账号名并阻断。
            return matched.start(), matched.end(), "leading"
        if (
            before
            and _is_mention_boundary_char(before[-1])
            and (not after or _is_mention_boundary_char(after[0]))
            and _only_mention_boundaries(after)
        ):
            return matched.start(), matched.end(), "trailing"
    return None


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


def _latest_chat_event(context: str) -> tuple[str, str]:
    """取最后一个聊天事件；发送者头仅是元数据，不代表一条新消息。"""
    prefixes = (
        ("[对方]", "对方"),
        ("[瀵规柟]", "对方"),
        ("[我]", "我"),
        ("[鎴]", "我"),
        ("[系统]", "系统"),
    )
    for line in reversed((context or "").splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        for prefix, role in prefixes:
            if stripped.startswith(prefix):
                return role, _line_text(stripped)
    return "", ""


def _latest_other_message(context: str) -> str:
    """仅当最后一个聊天事件来自对方时返回其文本，不向前追旧消息。"""
    role, text = _latest_chat_event(context)
    return text if role == "对方" else ""


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
        event_role, _event_text = _latest_chat_event(context)
        if event_role == "系统":
            return "系统"
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
        """检测最新对方消息开头或结尾是否完整 @ 当前账号用户名。"""
        if not account_name:
            return False
        message = _latest_other_message(context)
        if not message:
            return False
        return _find_account_mention_span(message, account_name) is not None

    def extract_question(self, message: str, account_name: str) -> str:
        """从消息开头或结尾的完整 @ 中提取问题正文。"""
        text = unicodedata.normalize("NFKC", message or "")
        mention = _find_account_mention_span(text, account_name)
        if mention is None:
            return text.strip()
        start, end, position = mention
        if position == "leading":
            return text[end:].lstrip(_MENTION_SEPARATORS + _INVISIBLE_SPACES).strip()
        return text[:start].rstrip(_INVISIBLE_SPACES).strip()

    def extract_sender_name(self, context: str) -> str:
        """从最新对方消息识别发送者名字（去平台标记）。

        覆盖两种 OCR 行格式：
        1. 单行合并：`阳光@微信 @程正恒 问题` → 取 @ 前的头部
        2. 上下双行：`阳光@微信`（上）+ `@程正恒 问题`（下）→ 内容行 @ 在行首时取上一行
        """
        if not _latest_other_message(context):
            return ""
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
        latest_role, _latest_text = _latest_chat_event(context)
        if latest_role == "系统":
            return True
        if latest_role != "对方":
            return False
        return not self.detect_at_mention(context, account_name)
