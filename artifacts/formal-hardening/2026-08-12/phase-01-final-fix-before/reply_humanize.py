from __future__ import annotations

import re


AI_PHRASE_REPLACEMENTS = {
    "好的，收到，": "",
    "好的，收到": "收到",
    "我会尽快处理这个事情": "我看下",
    "我会尽快处理": "我看下",
    "第一时间同步给你": "弄完跟你说",
    "第一时间": "",
    "避免客户那边继续等待": "",
    "辛苦你提醒": "",
    "如有需要": "",
    "请随时告诉我": "",
    "感谢你的理解": "",
}


def humanize_reply(text: str, max_chars: int = 42) -> str:
    result = (text or "").strip()
    result = re.sub(r"^\s*\d+\s*[\.\)、]\s*", "", result)
    result = result.strip("\"'“”")
    for old, new in AI_PHRASE_REPLACEMENTS.items():
        result = result.replace(old, new)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"[。；;]\s*", "，", result)
    result = re.sub(r"[，,]\s*[，,]+", "，", result)
    result = result.strip(" ，。")

    if len(result) > max_chars:
        parts = re.split(r"[，。！？?]", result)
        compact = "，".join(part.strip() for part in parts[:2] if part.strip())
        result = compact or result[:max_chars]
    if len(result) > max_chars:
        result = result[:max_chars].rstrip("，。")
    return result or "我看下，晚点回你"


def normalize_replies(replies: list[str], count: int = 3, pad: bool = True) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for reply in replies:
        cleaned = humanize_reply(reply)
        key = re.sub(r"\W+", "", cleaned)
        if cleaned and key not in seen:
            normalized.append(cleaned)
            seen.add(key)
        if len(normalized) >= count:
            return normalized

    if not pad:
        return normalized[:count]

    fallbacks = ["我看下，晚点回你", "可以，我确认下", "行，我处理完跟你说"]
    for fallback in fallbacks:
        key = re.sub(r"\W+", "", fallback)
        if key not in seen:
            normalized.append(fallback)
            seen.add(key)
        if len(normalized) >= count:
            break
    return normalized[:count]
