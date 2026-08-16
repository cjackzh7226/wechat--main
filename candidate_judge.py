from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - optional runtime dependency
    requests = None

from knowledge_base import KnowledgeCandidate
from llm_clients import build_text_generation_request, extract_text_from_response
from settings import AppSettings


@dataclass(frozen=True)
class Judgement:
    chosen_index: int
    consistent: bool


def parse_judgement(content: str, candidate_count: int) -> Judgement | None:
    """Accept one exact JSON object and reject all coercion or decoration."""
    if not isinstance(content, str):
        return None
    if type(candidate_count) is not int or candidate_count <= 0:
        return None
    try:
        data = json.loads(content.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or set(data) != {"chosen", "consistent"}:
        return None
    chosen = data["chosen"]
    consistent = data["consistent"]
    if type(chosen) is not int or type(consistent) is not bool:
        return None
    if not 1 <= chosen <= candidate_count:
        return None
    return Judgement(chosen_index=chosen - 1, consistent=consistent)


class CandidateJudge:
    """Use a model only to choose an existing candidate, never to create text."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        post: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._post = post if post is not None else getattr(requests, "post", None)
        self.last_error_category = ""

    def judge(
        self, question: str, candidates: Sequence[KnowledgeCandidate]
    ) -> Judgement | None:
        self.last_error_category = ""
        if not isinstance(question, str) or not question.strip() or not candidates:
            self.last_error_category = "invalid_input"
            return None
        if not isinstance(self.settings.api_key, str) or not self.settings.api_key.strip():
            self.last_error_category = "not_configured"
            return None
        if self._post is None:
            self.last_error_category = "http_component_unavailable"
            return None
        if any(not isinstance(candidate, KnowledgeCandidate) for candidate in candidates):
            self.last_error_category = "invalid_input"
            return None

        candidate_payload = [
            {
                "number": index,
                "record_id": candidate.record_id,
                "question": candidate.question,
                "org_scope": candidate.org_scope,
                "score": candidate.score,
                "exact": candidate.exact,
            }
            for index, candidate in enumerate(candidates, start=1)
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "你只能选择候选编号：先选出与客户问题最匹配的候选编号，再判断所选候选问题与"
                    "客户问题是否属于同一事项和同一机构范围。"
                    "不得生成、补写或改写任何答案。"
                    "只输出严格 JSON，且键只能是 chosen 和 consistent："
                    '{"chosen":1,"consistent":true}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "customer_question": question,
                        "candidates": candidate_payload,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            request = build_text_generation_request(self.settings, messages)
            if self.settings.api_provider != "custom_http":
                request.payload["temperature"] = 0
                request.payload["max_tokens"] = 80
            response = self._post(
                request.url,
                headers=request.headers,
                json=request.payload,
                timeout=20,
            )
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code >= 400:
                self.last_error_category = "http_or_response_error"
                return None
            response.raise_for_status()
            content = extract_text_from_response(response.json(), self.settings)
        except Exception:
            self.last_error_category = "http_or_response_error"
            return None

        judgement = parse_judgement(content, len(candidates))
        if judgement is None:
            self.last_error_category = "invalid_judgement"
            return None
        return judgement
