from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app_paths import memory_path


ENTERPRISE_ANNUAL_REPORT_SOURCE = "enterprise_annual_report_kb"
ENTERPRISE_LOCAL_CONFIDENT_MARGIN = 8
ENTERPRISE_LOCAL_MIN_SCORE = 8
# 企业年报"可直接发送"的可靠底线：第一名分数不低于该值且明显领先第二名（>=8 分）且无冲突。
# 2026-08-05 用户要求低分阈值 20 -> 30：命中评分 < 30 时回复企业未命中文案。
ENTERPRISE_LOCAL_CONFIDENT_FLOOR = 30

# 普通话术库共享打分的最低匹配值：低于该值视为知识库未准备该问题，
# 运行时不会把低分候选发给用户，而是老实回答"暂时未查到对应答案"。
PHRASEBOOK_CONFIDENT_SCORE = 30

# 企业年报问题常见的同义表达。它们只用于本地召回排序，不会改写知识库原问题或原答案。
_ENTERPRISE_SEMANTIC_TERMS: dict[str, tuple[str, ...]] = {
    "topic_annual_report": ("年度工作报告", "年度报告", "年报"),
    "topic_annual_inspection": ("年检", "年审"),
    "time_historical": ("往年", "以前", "之前", "历史", "过往", "已提交"),
    "time_current": ("本年度", "本年", "今年", "当年", "当前"),
    "action_view": ("查看", "查询", "查找", "检索", "怎么查", "如何查", "哪里看", "在哪看", "怎么看", "看到"),
    "action_edit": ("修改", "更正", "修正", "编辑"),
    "action_fill": ("填报", "填写", "填表", "提交", "上报", "申报"),
    "action_print": ("打印", "导出"),
    "action_login": ("登录", "入口", "进入", "账号", "密码"),
    "status_returned": ("退回", "被退", "驳回"),
    "status_error": ("报错", "异常", "提示", "无法", "问题"),
    "action_handle": ("解决", "怎么办", "处理", "如何办", "整改"),
}
_ENTERPRISE_TAG_WEIGHTS = {
    "topic_annual_report": 18,
    "topic_annual_inspection": 16,
    "time_historical": 35,
    "time_current": 35,
    "action_view": 32,
    "action_edit": 30,
    "action_fill": 30,
    "action_print": 30,
    "action_login": 28,
    "status_returned": 26,
    "status_error": 12,
    "action_handle": 12,
}
_ENTERPRISE_TIME_TAGS = {"time_historical", "time_current"}
_ENTERPRISE_PRIMARY_ACTION_TAGS = {
    "action_view",
    "action_edit",
    "action_fill",
    "action_print",
    "action_login",
}
_ENTERPRISE_SCOPE_TERMS = (
    "年检",
    "年审",
    "年报",
    "年度报告",
    "年度工作报告",
    "报告",
    "社会组织",
    "社会团体",
    "民办非企业",
    "登记管理",
    # 企业年报知识库常见业务问法词（2026-08-04 补充），
    # 让"负责人超理事数1/3怎么整改"这类整改/治理问题也能进入企业库匹配。
    "整改",
    "理事",
    "负责人",
    "常务理事",
    "监事",
    "换届",
    "备案",
    "会员大会",
    "章程",
    # 分支机构/代表机构管理办法问答库（2026-08-09 补充），
    # 让"我们协会想设个分会"“分支机构能叫会长吗”这类问题在企业库
    # 未命中时被固定文案拦截，避免漏到大模型自由发挥。
    "分支",
    "分会",
    "代表机构",
    "办事处",
    "联络处",
    "专业委员会",
    "工作委员会",
    "专家委员会",
    "技术委员会",
    "主任委员",
    "副主任委员",
    "学组",
    "会费",
    "专项基金",
    "印章",
)


def default_memory_path() -> Path:
    return memory_path()


@dataclass
class StyleMemory:
    path: Path = field(default_factory=default_memory_path)
    style_summary: str = "偏短句、微信口语、少解释、少承诺。"
    examples: list[dict[str, str]] = field(default_factory=list)
    common_phrases: Counter = field(default_factory=Counter)
    max_examples: int = 3000

    @property
    def vector_path(self) -> Path:
        return self.path.with_name("vector_memory.json")

    @property
    def sop_library_path(self) -> Path:
        return self.path.with_name("sop_library.json")

    def load(self) -> "StyleMemory":
        if not self.path.exists():
            return self
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.style_summary = str(data.get("style_summary", self.style_summary))
        raw_examples = list(data.get("examples", []))[-self.max_examples :]
        self.examples = [_normalize_example(item) for item in raw_examples if _normalize_example(item)]
        self.common_phrases = Counter(data.get("common_phrases", {}))
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 数量骤减保护：磁盘已有大量知识库（≥50 条）而内存只有极少（<20 条）时，
        # 说明程序可能 load 到了旧/空数据（常见于程序运行中数据被外部同步/替换），
        # 先备份磁盘当前文件再覆盖，避免知识库被永久冲掉。
        try:
            if self.path.exists():
                disk_examples = json.loads(self.path.read_text(encoding="utf-8")).get("examples", [])
                if len(disk_examples) >= 50 and len(self.examples) < 20:
                    backup_path = self.path.with_name(
                        f"memory.json.bak-auto-{time.strftime('%Y%m%d-%H%M%S')}"
                    )
                    shutil.copy2(self.path, backup_path)
        except Exception:
            pass
        self.examples = _dedupe_memory_examples(self.examples)[-self.max_examples :]
        data = {
            "style_summary": self.style_summary,
            "examples": self.examples,
            "common_phrases": dict(self.common_phrases.most_common(120)),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save_vector_index()

    def learn_from_chat_text(self, conversation_text: str) -> None:
        lines = [line.strip() for line in conversation_text.splitlines() if line.strip()]
        observed_examples: list[dict[str, str]] = []
        for index, line in enumerate(lines):
            if not _is_own_line(line):
                continue
            reply = line.split("]", 1)[-1].strip()
            cue = "\n".join(lines[max(0, index - 8) : index])
            if cue and reply:
                observed_examples.append(
                    _build_example(
                        partner="训练模式观察",
                        cue=cue,
                        reply=reply,
                        source="observed_chat",
                    )
                )
        for item in observed_examples[-8:]:
            self.examples.append(item)
            for phrase in self._extract_phrases(item["reply"]):
                self.common_phrases[phrase] += 1
        self._refresh_summary()
        self.save()

    def learn_from_sent_reply(
        self,
        partner: str,
        conversation_text: str,
        sent_reply: str,
        source: str = "manual_edit",
    ) -> None:
        cleaned = sent_reply.strip()
        if not cleaned:
            return
        self.examples.append(
            _build_example(
                partner=partner,
                cue=conversation_text,
                reply=cleaned,
                source=_clean_source(source),
            )
        )
        for phrase in self._extract_phrases(cleaned):
            self.common_phrases[phrase] += 3
        self._refresh_summary()
        self.save()

    def learn_from_training_examples(self, examples: list[dict[str, str]]) -> None:
        for item in examples:
            reply = str(item.get("reply", "")).strip()
            cue = str(item.get("cue", "")).strip()
            if not reply or not cue:
                continue
            source = str(item.get("source", "chat_zip_training"))
            self.examples.append(
                _with_training_metadata(
                    _build_example(
                        partner=str(item.get("partner", "聊天记录训练")),
                        cue=cue,
                        reply=reply,
                        source=source,
                        created_at=str(item.get("created_at", "")),
                    ),
                    item,
                )
            )
            if source != ENTERPRISE_ANNUAL_REPORT_SOURCE:
                for phrase in self._extract_phrases(reply):
                    self.common_phrases[phrase] += 2
        self._refresh_summary()
        self.save()

    def prompt_block(self, conversation_text: str = "", exclude_sources: set[str] | None = None) -> str:
        excluded = exclude_sources or set()
        if excluded:
            phrase_counter: Counter[str] = Counter()
            for item in self.examples:
                if item.get("source") in excluded:
                    continue
                for phrase in self._extract_phrases(str(item.get("reply", ""))):
                    phrase_counter[phrase] += 1
            phrases = "、".join(phrase for phrase, _ in phrase_counter.most_common(12))
        else:
            phrases = "、".join(phrase for phrase, _ in self.common_phrases.most_common(12))
        relevant = self.relevant_examples(conversation_text, limit=5) if conversation_text else self.examples[-5:]
        relevant = [item for item in relevant if item.get("source") not in excluded]
        examples = "\n".join(_format_prompt_example(index, item) for index, item in enumerate(relevant, 1))
        return (
            f"用户风格摘要：{self.style_summary}\n"
            f"常用表达：{phrases or '可以、我看下、晚点、确认下'}\n"
            "相似历史聊天片段与真实回复：\n"
            f"{examples or '- 暂无历史样例，可按当前上下文自然接话'}"
        )

    def relevant_examples(self, conversation_text: str, limit: int = 6) -> list[dict[str, str]]:
        relevant = self.strict_relevant_examples(conversation_text, limit=limit)
        return relevant or self.examples[-limit:]

    def strict_relevant_examples(self, conversation_text: str, limit: int = 6) -> list[dict[str, str]]:
        query_tokens = _text_tokens(conversation_text)
        if not query_tokens:
            return []
        last_other = _last_other_text(conversation_text)
        last_tokens = _text_tokens(last_other)
        scored = []
        for index, item in enumerate(self.examples):
            cue = str(item.get("cue", ""))
            reply = str(item.get("reply", ""))
            cue_tokens = _text_tokens(cue)
            reply_tokens = _text_tokens(reply)
            score = len(query_tokens & cue_tokens) * 3 + len(query_tokens & reply_tokens)
            if last_tokens:
                score += len(last_tokens & cue_tokens) * 4
            score += _phrasebook_intent_bonus(conversation_text, item)
            if score:
                score += _example_priority(item) / 10
                scored.append((score, index, item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        selected = [item for _, _, item in scored[:limit]]
        if len(selected) < limit:
            seen = {item.get("conversation_hash", "") for item in selected}
            for item in self.vector_relevant_examples(conversation_text, limit=limit * 2):
                key = item.get("conversation_hash", "")
                if key and key not in seen:
                    selected.append(item)
                    seen.add(key)
                if len(selected) >= limit:
                    break
        return selected[:limit]

    def enterprise_candidates(self, conversation_text: str, limit: int = 6) -> list[dict[str, str]]:
        """Return the shared local ranking used by enterprise runtime and phrasebook testing."""

        query = _last_other_text(conversation_text) or conversation_text
        query_tokens = _text_tokens(query)
        query_vector = _text_vector(query)
        if not query_tokens or not query_vector:
            return []

        scored: list[tuple[int, int, dict[str, str]]] = []
        normalized_query = _enterprise_question_key(query)
        query_tags = _enterprise_semantic_tags(query)
        for index, item in enumerate(self.examples):
            if item.get("source") != ENTERPRISE_ANNUAL_REPORT_SOURCE:
                continue
            question = str(item.get("question", "")).strip() or str(item.get("cue", "")).split("/")[-1].strip()
            if not question:
                continue
            searchable = "\n".join(
                str(item.get(key, ""))
                for key in ("question", "module", "subcategory", "organization_type", "cue")
                if item.get(key)
            )
            token_hits = len(query_tokens & _text_tokens(searchable))
            vector_score = _cosine_sparse(query_vector, _text_vector(searchable))
            normalized_question = _enterprise_question_key(question)
            exact_question = bool(normalized_query and normalized_query == normalized_question)
            candidate_tags = _enterprise_semantic_tags(searchable)
            shared_tags = query_tags & candidate_tags
            semantic_bonus = sum(_ENTERPRISE_TAG_WEIGHTS[tag] for tag in shared_tags)
            conflict_penalty, conflicts = _enterprise_semantic_conflicts(query_tags, candidate_tags)
            containment_bonus = 0
            if normalized_question and normalized_question in normalized_query:
                containment_bonus = 12
            elif normalized_query and normalized_query in normalized_question:
                containment_bonus = 8
            # 数字比例/关键数字短语加分：让"负责人超理事数1/3"明确命中"理事数的1/3"，
            # 而不是与"常务理事数的1/2"并列。
            ratio_bonus = _enterprise_ratio_bonus(normalized_query, normalized_question)
            if exact_question:
                score = 100
            else:
                token_coverage = token_hits / max(len(query_tokens), 1)
                base_score = int(round(token_coverage * 55 + vector_score * 25 + containment_bonus + ratio_bonus))
                score = max(0, min(99, base_score + semantic_bonus - conflict_penalty))
            if score <= 0:
                continue
            candidate = dict(item)
            candidate["question"] = question
            candidate["enterprise_local_score"] = score
            candidate["enterprise_exact_question"] = "1" if exact_question else ""
            candidate["enterprise_conflict_count"] = str(len(conflicts))
            candidate["enterprise_match_reasons"] = "、".join(
                _enterprise_reason_labels(shared_tags, conflicts, exact_question)
            )
            scored.append((score, index, candidate))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        candidates = [item for _score, _index, item in scored[:limit]]
        _annotate_enterprise_decision(candidates)
        return candidates

    def enterprise_scored_examples(self, conversation_text: str, limit: int = 50) -> list[dict[str, object]]:
        """Adapt the shared enterprise ranking for the phrasebook test UI."""

        rows: list[dict[str, object]] = []
        candidates = self.enterprise_candidates(conversation_text, limit=limit)
        decision_fields = {
            "enterprise_decision": str(candidates[0].get("enterprise_decision", "ambiguous")) if candidates else "",
            "enterprise_decision_label": str(candidates[0].get("enterprise_decision_label", "")) if candidates else "",
            "enterprise_decision_reason": str(candidates[0].get("enterprise_decision_reason", "")) if candidates else "",
            "enterprise_next_score": str(candidates[0].get("enterprise_next_score", "0")) if candidates else "0",
            "enterprise_score_margin": str(candidates[0].get("enterprise_score_margin", "0")) if candidates else "0",
        }
        for candidate in candidates:
            row: dict[str, object] = dict(candidate)
            row.update(
                {
                    "library_key": ENTERPRISE_ANNUAL_REPORT_SOURCE,
                    "library_name": _library_name(ENTERPRISE_ANNUAL_REPORT_SOURCE),
                    "score": int(candidate.get("enterprise_local_score", 0) or 0),
                    "reasons": [
                        item
                        for item in str(candidate.get("enterprise_match_reasons", "")).split("、")
                        if item
                    ]
                    or ["关键词"],
                    **decision_fields,
                }
            )
            rows.append(row)
        return rows

    def has_enterprise_annual_report_knowledge(self) -> bool:
        return any(item.get("source") == ENTERPRISE_ANNUAL_REPORT_SOURCE for item in self.examples)

    def is_enterprise_annual_report_query(self, conversation_text: str) -> bool:
        query = _last_other_text(conversation_text) or conversation_text
        compact = _enterprise_question_key(query)
        return bool(compact and any(term in compact for term in _ENTERPRISE_SCOPE_TERMS))

    def vector_relevant_examples(self, conversation_text: str, limit: int = 6) -> list[dict[str, str]]:
        query_vector = _text_vector(conversation_text)
        if not query_vector or not self.vector_path.exists():
            return []
        try:
            data = json.loads(self.vector_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        by_hash = {item.get("conversation_hash", ""): item for item in self.examples}
        scored: list[tuple[float, int, dict[str, str]]] = []
        for index, item in enumerate(data.get("items", [])):
            if not isinstance(item, dict):
                continue
            key = str(item.get("conversation_hash", ""))
            example = by_hash.get(key)
            if not example:
                continue
            score = _cosine_sparse(query_vector, item.get("vector", {}))
            if score >= 0.08:
                scored.append((score, index, example))
        scored.sort(key=lambda row: (row[0], -row[1]), reverse=True)
        return [item for _, _, item in scored[:limit]]

    def library_summaries(self) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in self.examples:
            key = str(item.get("source", "manual_edit") or "manual_edit")
            grouped.setdefault(key, []).append(item)
        summaries = []
        for key, items in grouped.items():
            summaries.append(
                {
                    "key": key,
                    "name": _library_name(key),
                    "count": len(items),
                    "latest": max((str(item.get("created_at", "")) for item in items), default=""),
                }
            )
        summaries.sort(key=lambda item: (int(item["count"]), str(item["latest"])), reverse=True)
        return summaries

    def examples_for_library(self, library_key: str = "", limit: int = 300) -> list[dict[str, str]]:
        items = self.examples
        if library_key:
            items = [item for item in items if item.get("source") == library_key]
        return [dict(item) for item in items[-limit:]][::-1]

    def scored_examples(self, conversation_text: str, library_key: str = "", limit: int = 50) -> list[dict[str, object]]:
        query_tokens = _text_tokens(conversation_text)
        query_vector = _text_vector(conversation_text)
        vector_by_hash = self._vector_by_hash()
        rows: list[dict[str, object]] = []
        for item in self.examples:
            if library_key and item.get("source") != library_key:
                continue
            cue = str(item.get("cue", ""))
            reply = str(item.get("reply", ""))
            cue_tokens = _text_tokens(cue)
            reply_tokens = _text_tokens(reply)
            keyword_hits = len(query_tokens & cue_tokens)
            reply_hits = len(query_tokens & reply_tokens)
            vector_score = _cosine_sparse(query_vector, vector_by_hash.get(str(item.get("conversation_hash", "")), {}))
            priority = _example_priority(item)
            raw_score = keyword_hits * 16 + reply_hits * 8 + vector_score * 45
            raw_score += _phrasebook_intent_bonus(conversation_text, item) * 6
            if keyword_hits or reply_hits or vector_score >= 0.08:
                raw_score += priority * 0.22
            score = max(0, min(100, int(round(raw_score))))
            reasons = []
            if keyword_hits:
                reasons.append("keyword")
            if reply_hits:
                reasons.append("reply")
            if vector_score >= 0.08:
                reasons.append("vector")
            if _phrasebook_intent_bonus(conversation_text, item):
                reasons.append("intent")
            if priority:
                reasons.append(f"priority:{priority}")
            if not reasons:
                reasons.append("recent")
            row = dict(item)
            row.update(
                {
                    "library_key": item.get("source", ""),
                    "library_name": _library_name(str(item.get("source", ""))),
                    "score": score,
                    "reasons": reasons,
                    "priority": priority,
                    "vector_score": round(vector_score, 4),
                }
            )
            rows.append(row)
        rows.sort(
            key=lambda item: (
                int(item.get("score", 0)),
                int(item.get("priority", 0) or 0),
                str(item.get("created_at", "")),
            ),
            reverse=True,
        )
        return rows[:limit]

    def phrasebook_candidates(self, conversation_text: str, limit: int = 12) -> list[dict[str, object]]:
        """普通话术库共享排序入口：测试命中页和运行时消费同一份打分结果。

        内部先把对话收敛到"对方最后一句"再打分，与测试命中页输入一句问法的
        行为保持一致；企业年报源不参与普通话术库匹配。返回的每行带
        ``phrasebook_score``（与测试页 ``score`` 同源，即 keyword/reply/vector/intent 加权分）。
        """
        query = _last_other_text(conversation_text) or conversation_text
        rows = self.scored_examples(query, library_key="", limit=limit)
        rows = [
            row
            for row in rows
            if str(row.get("source", "")) != ENTERPRISE_ANNUAL_REPORT_SOURCE
        ]
        for row in rows:
            row["phrasebook_score"] = int(row.get("score", 0) or 0)
        return rows

    def has_phrasebook_entries(self) -> bool:
        """是否存在普通话术库条目（非企业年报源）。"""
        return any(
            item.get("source") != ENTERPRISE_ANNUAL_REPORT_SOURCE
            for item in self.examples
        )

    def update_example(self, conversation_hash: str, cue: str | None = None, reply: str | None = None) -> bool:
        target = str(conversation_hash or "")
        if not target:
            return False
        for index, item in enumerate(self.examples):
            if str(item.get("conversation_hash", "")) != target:
                continue
            new_cue = _normalize_context(cue if cue is not None else str(item.get("cue", "")))
            new_reply = _normalize_reply(reply if reply is not None else str(item.get("reply", "")))
            if not new_reply:
                return False
            updated = dict(item)
            updated["cue"] = new_cue
            updated["reply"] = new_reply
            updated["conversation_hash"] = _conversation_hash(new_cue, new_reply)
            updated["created_at"] = _utc_now()
            self.examples[index] = updated
            self.save()
            return True
        return False

    def _vector_by_hash(self) -> dict[str, dict[str, float]]:
        if not self.vector_path.exists():
            return {}
        try:
            data = json.loads(self.vector_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        result: dict[str, dict[str, float]] = {}
        for item in data.get("items", []):
            if isinstance(item, dict):
                key = str(item.get("conversation_hash", ""))
                vector = item.get("vector", {})
                if key and isinstance(vector, dict):
                    result[key] = vector
        return result

    def suggest_replies(self, conversation_text: str, limit: int = 3) -> list[str]:
        return self.suggest_replies_strict(conversation_text, limit=limit) or _unique_replies(
            self.relevant_examples(conversation_text, limit=limit * 4),
            limit,
        )

    def suggest_replies_strict(self, conversation_text: str, limit: int = 3) -> list[str]:
        return _unique_replies(self.strict_relevant_examples(conversation_text, limit=limit * 4), limit)

    def _refresh_summary(self) -> None:
        examples = [
            item["reply"]
            for item in self.examples[-24:]
            if item.get("reply") and item.get("source") != ENTERPRISE_ANNUAL_REPORT_SOURCE
        ]
        avg_len = int(sum(len(x) for x in examples) / len(examples)) if examples else 18
        emoji = any(re.search(r"[\U0001F300-\U0001FAFF]", text) for text in examples)
        question = any("？" in text or "?" in text or "吗" in text for text in examples)
        length_note = "短句" if avg_len <= 24 else "中等长度"
        emoji_note = "会少量用表情" if emoji else "基本不用表情"
        question_note = "常用反问或确认句" if question else "表达直接"
        self.style_summary = f"{length_note}、微信口语、{emoji_note}、{question_note}、少客服腔。"

    def _save_vector_index(self) -> None:
        items = []
        for item in self.examples:
            vector = _text_vector(f"{item.get('cue', '')}\n{item.get('reply', '')}")
            if not vector:
                continue
            items.append(
                {
                    "conversation_hash": item.get("conversation_hash", ""),
                    "source": item.get("source", ""),
                    "reply": item.get("reply", ""),
                    "vector": vector,
                }
            )
        data = {"version": 1, "dim": 384, "items": items}
        self.vector_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _extract_phrases(text: str) -> list[str]:
        pieces = re.split(r"[，。！？、,.!?\s]+", text)
        return [piece for piece in pieces if 2 <= len(piece) <= 10]


def _unique_replies(examples: list[dict[str, str]], limit: int) -> list[str]:
    suggestions: list[str] = []
    for item in examples:
        reply = str(item.get("reply", "")).strip()
        if reply and reply not in suggestions:
            suggestions.append(reply)
        if len(suggestions) >= limit:
            break
    return suggestions


def _library_name(source: str) -> str:
    names = {
        "enterprise_annual_report_kb": "企业年报知识库",
        "tiantian_html_training": "甜甜话术库",
        "chat_zip_training": "导入话术库",
        "wechat_export_training": "微信记录话术库",
        "sop_analysis_training": "SOP分析话术库",
        "manual_edit": "手动发送记忆",
        "selected_candidate": "候选发送记忆",
        "observed_chat": "训练模式观察",
        "managed_auto": "全托管记录",
    }
    return names.get(source, source or "默认话术库")


def _build_example(
    partner: str,
    cue: str,
    reply: str,
    source: str,
    created_at: str = "",
) -> dict[str, str]:
    full_cue = _normalize_context(cue)
    full_reply = _normalize_enterprise_reply(reply) if source == ENTERPRISE_ANNUAL_REPORT_SOURCE else _normalize_reply(reply)
    return {
        "partner": partner.strip()[:50] or "聊天记录训练",
        "cue": full_cue,
        "reply": full_reply,
        "source": _clean_source(source),
        "created_at": created_at or _utc_now(),
        "conversation_hash": _conversation_hash(full_cue, full_reply),
    }


def _with_training_metadata(example: dict[str, str], raw_item: dict[str, object]) -> dict[str, str]:
    for key in (
        "scenario_id",
        "scenario_title",
        "why",
        "question",
        "module",
        "subcategory",
        "organization_type",
        "source_sheet",
        "source_cell",
        "source_file",
    ):
        value = str(raw_item.get(key, "")).strip()
        if value:
            example[key] = value[:240]
    priority = _parse_priority(raw_item.get("priority", ""))
    if priority:
        example["priority"] = str(priority)
    return example


def _normalize_example(item: object) -> dict[str, str]:
    if not isinstance(item, dict):
        return {}
    cue = _normalize_context(str(item.get("cue", "")))
    source = _clean_source(str(item.get("source", "manual_edit")))
    reply = _normalize_enterprise_reply(str(item.get("reply", ""))) if source == ENTERPRISE_ANNUAL_REPORT_SOURCE else _normalize_reply(str(item.get("reply", "")))
    if not reply:
        return {}
    result = {
        "partner": str(item.get("partner", "聊天记录训练")).strip()[:50],
        "cue": cue,
        "reply": reply,
        "source": source,
        "created_at": str(item.get("created_at", "")) or _utc_now(),
        "conversation_hash": str(item.get("conversation_hash", "")) or _conversation_hash(cue, reply),
    }
    for key in (
        "scenario_id",
        "scenario_title",
        "why",
        "question",
        "module",
        "subcategory",
        "organization_type",
        "source_sheet",
        "source_cell",
        "source_file",
    ):
        value = str(item.get(key, "")).strip()
        if value:
            result[key] = value[:240]
    priority = _parse_priority(item.get("priority", ""))
    if priority:
        result["priority"] = str(priority)
    return result


def _parse_priority(value: object) -> int:
    try:
        number = int(float(str(value).strip()))
    except Exception:
        return 0
    return max(0, min(100, number))


def _example_priority(item: dict[str, str]) -> int:
    return _parse_priority(item.get("priority", ""))


def _phrasebook_intent_bonus(conversation_text: str, item: dict[str, str]) -> int:
    query = conversation_text or ""
    haystack = "\n".join(
        str(item.get(key, ""))
        for key in ("cue", "reply", "scenario_title", "why")
        if item.get(key)
    )
    if _contains_any(query, ("怎么放", "放哪里", "放哪", "摆哪里", "摆哪", "位置", "手机后面", "收到", "收到了")):
        placement_hits = _contains_any(
            haystack,
            ("怎么放", "放哪里", "放置", "使用说明", "位置", "手机壳", "钱包", "床头柜", "衣柜", "财位", "正南"),
        )
        if placement_hits and _contains_any(query, ("怎么放", "放哪里", "放哪", "摆哪里", "摆哪", "位置", "手机后面")):
            return 80
        if placement_hits and _contains_any(query, ("收到", "收到了")):
            return 5
    if _contains_any(query, ("刚加", "添加", "开始聊", "可以开始")) and _contains_any(
        haystack,
        ("刚添加", "初始接待", "开始聊天"),
    ):
        return 12
    return 0


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _normalize_context(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _normalize_reply(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _normalize_enterprise_reply(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _format_prompt_example(index: int, item: dict[str, str]) -> str:
    cue = _truncate_for_prompt(str(item.get("cue", "")), 700)
    reply = _truncate_for_prompt(str(item.get("reply", "")), 180)
    return f"{index}. 历史聊天片段：\n{cue}\n当时真实回复：{reply}"


def _truncate_for_prompt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _conversation_hash(cue: str, reply: str) -> str:
    payload = f"{_compact_for_hash(cue)}\n---\n{_compact_for_hash(reply)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _compact_for_hash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _enterprise_question_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


def _enterprise_semantic_tags(text: str) -> set[str]:
    compact = _enterprise_question_key(text)
    if not compact:
        return set()
    tags = {
        tag
        for tag, terms in _ENTERPRISE_SEMANTIC_TERMS.items()
        if any(_enterprise_question_key(term) in compact for term in terms)
    }
    # “哪里能看”“在哪儿看”这类问法的中间常夹入助词，不能只靠固定短语识别。
    if "哪" in compact and "看" in compact:
        tags.add("action_view")
    return tags


def _enterprise_ratio_bonus(query_key: str, candidate_question_key: str) -> int:
    """数字比例/关键数字短语加分。

    问法里的关键数字（如"1/3"归一化为"13"、"70周岁"的"70"）如果同时出现在
    候选问题里，且数字前 1-4 个字的上下文有重叠，说明候选与问法在同一业务点
    （如"负责人超理事数1/3" 应命中"负责人超过理事数的1/3"，而不是
    "常务理事数的1/2"）。返回 0 或固定加分（15），避免并列误判。
    """
    if not query_key or not candidate_question_key:
        return 0
    query_nums = re.findall(r"\d+", query_key)
    if not query_nums:
        return 0
    for num in query_nums:
        if len(num) < 1 or num not in candidate_question_key:
            continue
        query_pos = query_key.find(num)
        cand_pos = candidate_question_key.find(num)
        query_ctx = query_key[max(0, query_pos - 4) : query_pos]
        cand_ctx = candidate_question_key[max(0, cand_pos - 4) : cand_pos]
        overlap = len(set(query_ctx) & set(cand_ctx))
        if overlap >= 2:
            return 15
    return 0


def _enterprise_semantic_conflicts(query_tags: set[str], candidate_tags: set[str]) -> tuple[int, list[str]]:
    conflicts: list[str] = []
    query_times = query_tags & _ENTERPRISE_TIME_TAGS
    candidate_times = candidate_tags & _ENTERPRISE_TIME_TAGS
    if query_times and candidate_times and not query_times & candidate_times:
        conflicts.append("时间冲突")

    query_actions = query_tags & _ENTERPRISE_PRIMARY_ACTION_TAGS
    candidate_actions = candidate_tags & _ENTERPRISE_PRIMARY_ACTION_TAGS
    if query_actions and candidate_actions and not query_actions & candidate_actions:
        conflicts.append("操作冲突")

    penalty = 44 * ("时间冲突" in conflicts) + 35 * ("操作冲突" in conflicts)
    return penalty, conflicts


def _enterprise_reason_labels(shared_tags: set[str], conflicts: list[str], exact_question: bool) -> list[str]:
    labels = []
    if exact_question:
        labels.append("精确问题")
    label_map = {
        "topic_annual_report": "年度报告",
        "topic_annual_inspection": "年检",
        "time_historical": "历史时间",
        "time_current": "当前年度",
        "action_view": "查看查询",
        "action_edit": "修改",
        "action_fill": "填报",
        "action_print": "打印导出",
        "action_login": "登录入口",
        "status_returned": "退回",
        "status_error": "问题状态",
        "action_handle": "处理",
    }
    labels.extend(label_map[tag] for tag in sorted(shared_tags) if tag in label_map)
    labels.extend(conflicts)
    return labels


def _annotate_enterprise_decision(candidates: list[dict[str, str]]) -> None:
    """Mark the single local decision consumed by both runtime and phrasebook UI."""

    if not candidates:
        return
    top = candidates[0]
    try:
        top_score = int(float(str(top.get("enterprise_local_score", "0"))))
    except (TypeError, ValueError):
        top_score = 0
    try:
        next_score = int(float(str(candidates[1].get("enterprise_local_score", "0")))) if len(candidates) > 1 else 0
    except (TypeError, ValueError):
        next_score = 0
    margin = max(0, top_score - next_score)
    has_conflict = str(top.get("enterprise_conflict_count", "")) not in {"", "0"}
    is_exact = str(top.get("enterprise_exact_question", "")).strip() == "1"
    is_confident = bool(
        top_score >= ENTERPRISE_LOCAL_CONFIDENT_FLOOR
        and not has_conflict
        and (is_exact or margin >= ENTERPRISE_LOCAL_CONFIDENT_MARGIN)
    )
    top["enterprise_local_confident"] = "1" if is_confident else ""
    # 并列待裁决：分数达到可靠底线但前三名并列/接近（margin<8），
    # 运行时交给大模型从并列候选中裁决，测试页显示"并列待裁决"。
    is_tie = bool(
        not is_confident
        and not has_conflict
        and top_score >= ENTERPRISE_LOCAL_CONFIDENT_FLOOR
        and margin < ENTERPRISE_LOCAL_CONFIDENT_MARGIN
        and len(candidates) >= 2
    )
    if is_confident:
        top["enterprise_decision"] = "send"
        top["enterprise_decision_label"] = "可直接发送"
    elif is_tie:
        top["enterprise_decision"] = "tie"
        top["enterprise_decision_label"] = "并列待裁决"
    else:
        top["enterprise_decision"] = "ambiguous"
        top["enterprise_decision_label"] = "歧义不发送"
    top["enterprise_next_score"] = str(next_score)
    top["enterprise_score_margin"] = str(margin)
    if is_confident:
        top["enterprise_decision_reason"] = "精确问题" if is_exact else "第一名领先第二名且无时间/操作冲突"
    elif is_tie:
        top["enterprise_decision_reason"] = "前三名并列/接近，运行时交大模型裁决"
    else:
        reasons = []
        if has_conflict:
            reasons.append("存在时间/操作冲突")
        if margin < ENTERPRISE_LOCAL_CONFIDENT_MARGIN:
            reasons.append("第一名未明显领先第二名")
        if top_score < ENTERPRISE_LOCAL_MIN_SCORE:
            reasons.append("有效匹配信息不足")
        top["enterprise_decision_reason"] = "；".join(reasons) or "匹配不可靠"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_own_line(line: str) -> bool:
    return line.startswith("[我]") or line.startswith("[我 ")


def _last_other_text(conversation_text: str) -> str:
    for line in reversed(conversation_text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("[对方]"):
            return stripped.split("]", 1)[-1].strip()
    return ""


def _text_tokens(text: str) -> set[str]:
    text = re.sub(r"\[(?:对方|我)\]", "", text)
    cleaned = re.sub(r"\s+", "", text)
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,}", cleaned))
    for size in (2, 3, 4):
        for index in range(max(0, len(cleaned) - size + 1)):
            piece = cleaned[index : index + size]
            if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
                tokens.add(piece)
    return tokens


def _text_vector(text: str, dim: int = 384) -> dict[str, float]:
    features = Counter()
    cleaned = re.sub(r"\s+", "", text or "").lower()
    for token in _text_tokens(text):
        features[token] += 2
    for size in (2, 3, 4):
        for index in range(max(0, len(cleaned) - size + 1)):
            features[cleaned[index : index + size]] += 1
    if not features:
        return {}
    buckets: Counter[str] = Counter()
    for feature, weight in features.items():
        digest = hashlib.sha1(feature.encode("utf-8", errors="ignore")).hexdigest()
        buckets[str(int(digest[:8], 16) % dim)] += float(weight)
    norm = sum(value * value for value in buckets.values()) ** 0.5
    if not norm:
        return {}
    return {key: round(value / norm, 6) for key, value in buckets.items()}


def _cosine_sparse(left: dict[str, float], right: object) -> float:
    if not isinstance(right, dict) or not left:
        return 0.0
    score = 0.0
    for key, value in left.items():
        try:
            score += value * float(right.get(key, 0.0))
        except Exception:
            continue
    return score


def _dedupe_memory_examples(examples: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for raw_item in examples:
        item = _normalize_example(raw_item)
        if not item:
            continue
        key = f"{item.get('conversation_hash', '')}:{_compact_for_hash(item.get('reply', ''))}"
        if not key:
            continue
        existing = seen.get(key)
        if existing is None:
            seen[key] = item
        elif item.get("source_file") and not existing.get("source_file"):
            # 新条目带了来源文件名而旧条目没有（旧版本导入）→ 用新条目补充来源信息
            seen[key] = item
    return list(seen.values())


def _clean_source(source: str) -> str:
    allowed = {
        "selected_candidate",
        "manual_edit",
        "managed_auto",
        "observed_chat",
        "chat_zip_training",
        "tiantian_html_training",
        "wechat_export_training",
        "sop_analysis_training",
        "enterprise_annual_report_kb",
    }
    return source if source in allowed else "manual_edit"
