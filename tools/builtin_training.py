from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


SCENARIO_PATH = Path(__file__).with_name("builtin_training_scenarios.json")


@lru_cache(maxsize=1)
def load_builtin_scenarios() -> list[dict]:
    if not SCENARIO_PATH.exists():
        return []
    try:
        data = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("scenarios", [])
    return [item for item in data if isinstance(item, dict)]


def relevant_builtin_replies(conversation_text: str, limit: int = 3) -> list[str]:
    query = _tokens(conversation_text)
    if not query:
        return []
    scored: list[tuple[int, int, dict]] = []
    for index, item in enumerate(load_builtin_scenarios()):
        incoming = item.get("incoming", [])
        if isinstance(incoming, str):
            incoming = [incoming]
        haystack = " ".join(str(x) for x in incoming)
        score = len(query & _tokens(haystack))
        if score:
            scored.append((score, index, item))
    scored.sort(key=lambda row: (row[0], -row[1]), reverse=True)

    replies: list[str] = []
    seen: set[str] = set()
    for _score, _index, item in scored:
        good = item.get("good_replies", [])
        if isinstance(good, str):
            good = [good]
        for reply in good:
            text = str(reply).strip()
            key = _compact(text)
            if text and key not in seen:
                replies.append(text)
                seen.add(key)
            if len(replies) >= limit:
                return replies
    return replies


def builtin_count() -> int:
    return len(load_builtin_scenarios())


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text)
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,}", cleaned))
    for size in (2, 3, 4):
        for index in range(max(0, len(cleaned) - size + 1)):
            piece = cleaned[index : index + size]
            if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
                tokens.add(piece)
    return tokens


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)
