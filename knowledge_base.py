from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias


OrgScope: TypeAlias = Literal["social", "mfei", "both", "unknown"]
RecordStatus: TypeAlias = Literal["active", "conflict", "pending", "inactive"]

_ORG_SCOPES = frozenset({"social", "mfei", "both", "unknown"})
_RECORD_STATUSES = frozenset({"active", "conflict", "pending", "inactive"})
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SOCIAL_MARKERS = ("社会团体", "社团", "协会", "商会")
_MFEI_MARKERS = ("民办非企业", "民非单位", "民非", "社会服务机构")
_EXPLICIT_BOTH_LABELS = frozenset(
    {
        "both",
        "通用",
        "共同",
        "共用",
        "两类",
        "两类机构",
        "两类机构均适用",
        "两类机构共同适用",
        "社会团体、民办非企业单位",
        "民办非企业单位、社会团体",
        "社会团体/民办非企业单位",
        "民办非企业单位/社会团体",
    }
)
_EXPLICIT_BOTH_PATTERN = re.compile(
    r"^(?:(?:社会团体|社团|协会|商会)[、/和及与](?:民办非企业(?:单位)?|民非|社会服务机构)"
    r"|(?:民办非企业(?:单位)?|民非|社会服务机构)[、/和及与](?:社会团体|社团|协会|商会))"
    r"(?:(?:共同|均)?适用)?$"
)
_SOCIAL_LABEL_PATTERN = re.compile(
    r"^.{0,16}(?:社会团体|社团|协会|商会)(?:单位|组织)?$"
)
_MFEI_LABEL_PATTERN = re.compile(
    r"^.{0,16}(?:民办非企业(?:单位)?|民非(?:单位)?|社会服务机构)$"
)
_NON_LABEL_SCOPE_PATTERN = re.compile(
    r"(?:并非|不是|不属于|不适用|不均适用|是否|是不是|还是|或者|"
    r"合作|服务的|服务于|面向|涉及|关于|参考|提到|改制|转为|变更为|"
    r"非(?=社会团体|社团|协会|商会|民办非企业|民非|社会服务机构))"
)
_QUERY_RELATION_OR_AMBIGUITY_PATTERN = re.compile(
    r"(?:是否|是不是|还是|或者|合作|服务的|服务于|改制|转为|变更为)"
)
_NEGATED_SCOPE_MARKER_PATTERN = re.compile(
    r"(?:并非|不是|不属于|非)(?:社会团体|社团|协会|商会|民办非企业(?:单位)?|民非(?:单位)?|社会服务机构)"
)
_SCOPE_MARKER_PATTERN = re.compile(
    r"社会团体|社团|协会|商会|民办非企业(?:单位)?|民非(?:单位)?|社会服务机构"
)
_MATCH_SCOPE_MARKER_PATTERN = re.compile(
    r"社会团体|社团|协会|商会|民办非企业(?:单位)?|民非(?:单位)?|社会服务机构"
)
_MATCH_ACTION_END_PATTERN = re.compile(
    r"(?:"
    r"(?:需要|应该|应当|应|该|要|需|可以|可)?"
    r"(?:怎么|如何|怎样|咋)(?:进行|开展)?"
    r"(?:办|做|整改|处理|解决|改正|纠正|办理|操作|应对)"
    r"|(?:整改|处理|解决)(?:办法|方案|方式|方法|措施)(?:是什么|有哪些|是啥)"
    r"|(?:有什么|有哪些)(?:整改|处理|解决)(?:办法|方案|方式|方法|措施)"
    r"|(?:应该|应当|应|需要|需|要)?采取什么"
    r"(?:整改|处理|解决)?(?:办法|方案|方式|方法|措施)"
    r")$"
)
_MIN_CANDIDATE_SCORE = 30
_CANDIDATE_MARGIN = 8


class KnowledgeSchemaError(ValueError):
    """A knowledge item violates the typed record contract."""


@dataclass(frozen=True)
class KnowledgeRecord:
    record_id: str
    question: str
    reply: str
    org_scope: OrgScope
    keywords: tuple[str, ...]
    source_file: str
    source_locator: str
    source_hash: str
    answer_hash: str
    semantic_duplicate_key: str
    status: RecordStatus
    source_excerpt: str = ""
    organization_type: str = ""

    def __post_init__(self) -> None:
        _validate_hash("record_id", self.record_id)
        _validate_nonempty_text("question", self.question)
        _validate_nonempty_text("reply", self.reply)
        if not isinstance(self.org_scope, str) or self.org_scope not in _ORG_SCOPES:
            raise KnowledgeSchemaError(
                "org_scope must be one of social, mfei, both, unknown"
            )
        if not isinstance(self.keywords, tuple):
            raise KnowledgeSchemaError("keywords must be an immutable tuple")
        for keyword in self.keywords:
            if not isinstance(keyword, str) or not _normalize_identity_text(keyword):
                raise KnowledgeSchemaError(
                    "keywords members must be non-empty strings"
                )
        _validate_string("source_file", self.source_file)
        _validate_string("source_locator", self.source_locator)
        _validate_string("source_excerpt", self.source_excerpt)
        _validate_string("organization_type", self.organization_type)
        _validate_hash("source_hash", self.source_hash)
        _validate_hash("answer_hash", self.answer_hash)
        _validate_hash("semantic_duplicate_key", self.semantic_duplicate_key)
        if not isinstance(self.status, str) or self.status not in _RECORD_STATUSES:
            raise KnowledgeSchemaError(
                "status must be one of active, conflict, pending, inactive"
            )
        if self.org_scope == "unknown" and self.status == "active":
            raise KnowledgeSchemaError("unknown scope cannot have active status")
        if self.status == "active" and not _has_complete_provenance(
            self.source_file, self.source_locator, self.source_excerpt
        ):
            raise KnowledgeSchemaError(
                "active status requires complete provenance: "
                "source_file, source_locator, and source_excerpt"
            )
        expected_answer_hash = _answer_hash(self.reply)
        if self.answer_hash != expected_answer_hash:
            raise KnowledgeSchemaError("answer_hash does not match exact reply content")
        expected_source_hash = _source_hash(self.source_excerpt)
        if self.source_hash != expected_source_hash:
            raise KnowledgeSchemaError(
                "source_hash does not match exact source_excerpt content"
            )
        expected_semantic_key = _semantic_duplicate_key(
            self.question, self.reply, self.org_scope
        )
        if self.semantic_duplicate_key != expected_semantic_key:
            raise KnowledgeSchemaError(
                "semantic_duplicate_key does not match normalized knowledge content"
            )
        expected_record_id = _record_id(
            question=self.question,
            org_scope=self.org_scope,
            answer_hash=self.answer_hash,
            source_file=self.source_file,
            source_locator=self.source_locator,
            source_hash=self.source_hash,
            organization_type=self.organization_type,
            keywords=self.keywords,
        )
        if self.record_id != expected_record_id:
            raise KnowledgeSchemaError(
                "record_id does not match canonical record identity"
            )


@dataclass(frozen=True)
class KnowledgeAudit:
    records: tuple[KnowledgeRecord, ...]
    archive_records: tuple[KnowledgeRecord, ...]
    duplicate_record_groups: tuple[tuple[str, ...], ...]
    conflict_group_count: int
    conflict_record_count: int
    exact_duplicate_count: int


@dataclass(frozen=True)
class KnowledgeCandidate:
    record_id: str
    question: str
    answer_hash: str
    org_scope: OrgScope
    score: int
    exact: bool
    reasons: tuple[str, ...]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_string(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise KnowledgeSchemaError(f"{field} must be a string")


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not _normalize_identity_text(value):
        raise KnowledgeSchemaError(f"{field} must be a non-empty string")


def _validate_hash(field: str, value: object) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise KnowledgeSchemaError(f"{field} must be a 64-character hex digest")


def _has_complete_provenance(
    source_file: str, source_locator: str, source_excerpt: str
) -> bool:
    return all(
        isinstance(value, str) and bool(_normalize_identity_text(value))
        for value in (source_file, source_locator, source_excerpt)
    )


def _normalize_identity_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def normalize_question(question: str) -> str:
    """Normalize a validated question for identity and comparison only."""
    if not isinstance(question, str):
        raise KnowledgeSchemaError("question must be a string")
    return _normalize_identity_text(question)


def normalize_answer(answer: str) -> str:
    """Normalize a validated answer for identity and comparison only."""
    if not isinstance(answer, str):
        raise KnowledgeSchemaError("answer must be a string")
    return _normalize_identity_text(answer)


def _canonical_keywords(keywords: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_identity_text(keyword) for keyword in keywords}))


def _answer_hash(reply: str) -> str:
    return _sha256(reply)


def _source_hash(source_excerpt: str) -> str:
    return _sha256(source_excerpt)


def _semantic_duplicate_key(
    question: str, reply: str, org_scope: OrgScope
) -> str:
    return _sha256(
        _identity_payload(
            "knowledge-semantic-v1",
            normalize_question(question),
            org_scope,
            normalize_answer(reply),
        )
    )


def _record_id(
    *,
    question: str,
    org_scope: OrgScope,
    answer_hash: str,
    source_file: str,
    source_locator: str,
    source_hash: str,
    organization_type: str,
    keywords: tuple[str, ...],
) -> str:
    return _sha256(
        _identity_payload(
            "knowledge-record-v2",
            normalize_question(question),
            org_scope,
            answer_hash,
            _normalize_identity_text(source_file),
            _normalize_identity_text(source_locator),
            source_hash,
            _normalize_identity_text(organization_type),
            _canonical_keywords(keywords),
        )
    )


def _required_core_text(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str):
        raise KnowledgeSchemaError(f"{field} must be a non-empty string")
    if not _normalize_identity_text(value):
        raise KnowledgeSchemaError(f"{field} must be a non-empty string")
    return value


def _optional_string(item: Mapping[str, object], field: str) -> str:
    if field not in item:
        return ""
    value = item[field]
    if not isinstance(value, str):
        raise KnowledgeSchemaError(f"{field} must be a string")
    return value


def _keywords_from_item(item: Mapping[str, object]) -> tuple[str, ...]:
    if "keywords" not in item:
        return ()
    keywords = item["keywords"]
    if not isinstance(keywords, (list, tuple)):
        raise KnowledgeSchemaError("keywords must be a list or tuple of strings")
    for keyword in keywords:
        if not isinstance(keyword, str) or not _normalize_identity_text(keyword):
            raise KnowledgeSchemaError("keywords members must be non-empty strings")
    return tuple(keywords)


def _scope_from_label(label: str) -> OrgScope:
    normalized = _normalize_identity_text(label)
    compact = normalized.replace(" ", "")
    if _NON_LABEL_SCOPE_PATTERN.search(compact):
        return "unknown"
    if compact.casefold() in _EXPLICIT_BOTH_LABELS:
        return "both"
    if _EXPLICIT_BOTH_PATTERN.fullmatch(compact):
        return "both"

    has_social = any(marker in compact for marker in _SOCIAL_MARKERS)
    has_mfei = any(marker in compact for marker in _MFEI_MARKERS)
    if has_social and has_mfei:
        return "unknown"
    if has_social and _SOCIAL_LABEL_PATTERN.fullmatch(compact):
        return "social"
    if has_mfei and _MFEI_LABEL_PATTERN.fullmatch(compact):
        return "mfei"
    return "unknown"


def _scope_from_item(item: Mapping[str, object], organization_type: str) -> OrgScope:
    if "org_scope" not in item:
        return _scope_from_label(organization_type)
    scope = item["org_scope"]
    if not isinstance(scope, str) or scope not in _ORG_SCOPES:
        raise KnowledgeSchemaError(
            "org_scope must be one of social, mfei, both, unknown"
        )
    return scope  # type: ignore[return-value]


def _query_scope_evidence(text: str) -> tuple[OrgScope, bool]:
    if not isinstance(text, str):
        raise KnowledgeSchemaError("scope text must be a string")
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    has_marker = _SCOPE_MARKER_PATTERN.search(compact) is not None
    if not has_marker:
        return "unknown", False
    if _QUERY_RELATION_OR_AMBIGUITY_PATTERN.search(compact):
        return "unknown", True

    positive = _NEGATED_SCOPE_MARKER_PATTERN.sub("", compact)
    has_social = any(marker in positive for marker in _SOCIAL_MARKERS)
    has_mfei = any(marker in positive for marker in _MFEI_MARKERS)
    if has_social == has_mfei:
        return "unknown", True
    return ("social" if has_social else "mfei"), True


def _context_messages(recent_context: str) -> tuple[str, ...]:
    if not isinstance(recent_context, str):
        raise KnowledgeSchemaError("recent_context must be a string")
    messages: list[str] = []
    for raw_line in recent_context.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[我(?:\s[^\]]*)?\]", line):
            continue
        if line.startswith("[") and "]" in line:
            line = line.split("]", 1)[1].strip()
        if line:
            messages.append(line)
    return tuple(messages)


def resolve_query_scope(current_question: str, recent_context: str) -> OrgScope:
    """Resolve scope locally; current explicit evidence always outranks history."""
    current_scope, current_has_marker = _query_scope_evidence(current_question)
    if current_scope != "unknown":
        return current_scope
    if current_has_marker:
        return "unknown"
    for message in reversed(_context_messages(recent_context)):
        context_scope, _ = _query_scope_evidence(message)
        if context_scope != "unknown":
            return context_scope
    return "unknown"


def _status_from_item(
    item: Mapping[str, object], scope: OrgScope, *, has_complete_provenance: bool
) -> RecordStatus:
    if "status" not in item:
        return (
            "active"
            if scope != "unknown" and has_complete_provenance
            else "pending"
        )
    status = item["status"]
    if not isinstance(status, str) or status not in _RECORD_STATUSES:
        raise KnowledgeSchemaError(
            "status must be one of active, conflict, pending, inactive"
        )
    return status  # type: ignore[return-value]


def _identity_payload(*parts: object) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def audit_records(records: Iterable[KnowledgeRecord]) -> KnowledgeAudit:
    """Deterministically collapse exact duplicates and quarantine active conflicts."""
    materialized = list(records)
    for record in materialized:
        if not isinstance(record, KnowledgeRecord):
            raise KnowledgeSchemaError("audit records must be KnowledgeRecord values")
    archive_records = tuple(
        sorted(materialized, key=lambda item: (item.record_id, item.status))
    )

    exact_groups: dict[tuple[str, OrgScope, str], list[KnowledgeRecord]] = {}
    for record in archive_records:
        key = (
            normalize_question(record.question),
            record.org_scope,
            record.answer_hash,
        )
        exact_groups.setdefault(key, []).append(record)

    canonical: list[KnowledgeRecord] = []
    duplicate_record_groups: list[tuple[str, ...]] = []
    exact_duplicate_count = 0
    for group in exact_groups.values():
        ordered = sorted(group, key=lambda item: item.record_id)
        statuses = {record.status for record in ordered}
        if "conflict" in statuses:
            canonical_status: RecordStatus = "conflict"
        elif "active" in statuses:
            canonical_status = "active"
        elif "pending" in statuses:
            canonical_status = "pending"
        else:
            canonical_status = "inactive"
        compatible = [
            record for record in ordered if record.status == canonical_status
        ]
        canonical.append(replace(compatible[0], status=canonical_status))
        if len(ordered) > 1:
            duplicate_record_groups.append(
                tuple(record.record_id for record in ordered)
            )
            exact_duplicate_count += len(ordered) - 1

    eligible_by_question_scope: dict[
        tuple[str, OrgScope], list[KnowledgeRecord]
    ] = {}
    for record in canonical:
        if record.status in {"active", "conflict"}:
            eligible_by_question_scope.setdefault(
                (question_match_key(record.question), record.org_scope), []
            ).append(record)

    conflicting_ids: set[str] = set()
    conflict_group_count = 0
    for group in eligible_by_question_scope.values():
        if len({record.answer_hash for record in group}) > 1:
            conflict_group_count += 1
            conflicting_ids.update(record.record_id for record in group)

    audited_records = [
        replace(record, status="conflict")
        if record.record_id in conflicting_ids
        else record
        for record in canonical
    ]
    audited = tuple(
        sorted(audited_records, key=lambda item: item.record_id)
    )
    return KnowledgeAudit(
        records=audited,
        archive_records=archive_records,
        duplicate_record_groups=tuple(sorted(duplicate_record_groups)),
        conflict_group_count=conflict_group_count,
        conflict_record_count=len(conflicting_ids),
        exact_duplicate_count=exact_duplicate_count,
    )


def _match_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _MATCH_SCOPE_MARKER_PATTERN.sub("", normalized)
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", normalized)
    return _MATCH_ACTION_END_PATTERN.sub("怎么办", compact)


def question_match_key(question: str) -> str:
    """Return the same question key used by runtime scoring and conflict audit."""
    if not isinstance(question, str):
        raise KnowledgeSchemaError("question must be a string")
    return _match_text(question) or normalize_question(question)


def _match_features(text: str) -> set[str]:
    cleaned = _match_text(text)
    if not cleaned:
        return set()
    features = {cleaned}
    for size in (2, 3, 4):
        for index in range(max(0, len(cleaned) - size + 1)):
            features.add(cleaned[index : index + size])
    return features


def _candidate_score(question: str, record: KnowledgeRecord) -> tuple[int, bool]:
    query_key = _match_text(question)
    record_key = _match_text(record.question)
    exact = bool(query_key and query_key == record_key)
    if exact:
        return 100, True
    query_features = _match_features(question)
    searchable = " ".join((record.question, *record.keywords))
    record_features = _match_features(searchable)
    if not query_features or not record_features:
        return 0, False
    shared = len(query_features & record_features)
    coverage = shared / len(query_features)
    precision = shared / len(record_features)
    return min(99, int(round(coverage * 70 + precision * 30))), False


class KnowledgeRepository:
    def __init__(
        self, records_or_audit: Iterable[KnowledgeRecord] | KnowledgeAudit
    ) -> None:
        self.audit = (
            audit_records(records_or_audit.records)
            if isinstance(records_or_audit, KnowledgeAudit)
            else audit_records(records_or_audit)
        )
        self._records_by_id = {
            record.record_id: record for record in self.audit.records
        }

    @property
    def records(self) -> tuple[KnowledgeRecord, ...]:
        return self.audit.records

    def get_active(self, record_id: str) -> KnowledgeRecord | None:
        record = self._records_by_id.get(record_id)
        return record if record is not None and record.status == "active" else None

    def _eligible_active_records(self, query_scope: OrgScope) -> list[KnowledgeRecord]:
        active = [record for record in self.audit.records if record.status == "active"]
        if query_scope in {"social", "mfei"}:
            scoped_question_keys = {
                question_match_key(record.question)
                for record in self.audit.records
                if record.org_scope == query_scope
            }
            return [
                record
                for record in active
                if record.org_scope == query_scope
                or (
                    record.org_scope == "both"
                    and question_match_key(record.question)
                    not in scoped_question_keys
                )
            ]
        if query_scope == "both":
            return [record for record in active if record.org_scope == "both"]

        scopes_by_question_answer: dict[tuple[str, str], set[OrgScope]] = {}
        for record in active:
            if record.org_scope in {"social", "mfei"}:
                scopes_by_question_answer.setdefault(
                    (question_match_key(record.question), record.answer_hash), set()
                ).add(record.org_scope)
        shared_keys = {
            key
            for key, scopes in scopes_by_question_answer.items()
            if scopes == {"social", "mfei"}
        }
        return [
            record
            for record in active
            if record.org_scope == "both"
            or (
                record.org_scope in {"social", "mfei"}
                and (question_match_key(record.question), record.answer_hash)
                in shared_keys
            )
        ]

    def candidates(
        self, question: str, recent_context: str, limit: int = 3
    ) -> list[KnowledgeCandidate]:
        if not isinstance(question, str):
            raise KnowledgeSchemaError("question must be a string")
        if type(limit) is not int or limit <= 0:
            raise KnowledgeSchemaError("limit must be a positive integer")
        query_scope = resolve_query_scope(question, recent_context)
        scored: list[tuple[int, str, KnowledgeRecord, bool]] = []
        for record in self._eligible_active_records(query_scope):
            score, exact = _candidate_score(question, record)
            if score >= _MIN_CANDIDATE_SCORE:
                scored.append((score, record.record_id, record, exact))
        scored.sort(key=lambda item: (-item[0], item[1]))

        deduplicated: list[tuple[int, str, KnowledgeRecord, bool]] = []
        seen_answers: set[tuple[str, str]] = set()
        for item in scored:
            record = item[2]
            answer_key = (question_match_key(record.question), record.answer_hash)
            if answer_key in seen_answers:
                continue
            seen_answers.add(answer_key)
            deduplicated.append(item)

        if not deduplicated:
            return []
        top_score = deduplicated[0][0]
        next_score = deduplicated[1][0] if len(deduplicated) > 1 else 0
        margin = top_score - next_score
        if deduplicated[0][3]:
            selected = [item for item in deduplicated if item[3]][:limit]
        elif len(deduplicated) > 1 and margin < _CANDIDATE_MARGIN:
            selected = [
                item for item in deduplicated if top_score - item[0] < _CANDIDATE_MARGIN
            ][:limit]
        else:
            selected = deduplicated[:1]

        return [
            KnowledgeCandidate(
                record_id=record.record_id,
                question=record.question,
                answer_hash=record.answer_hash,
                org_scope=record.org_scope,
                score=score,
                exact=exact,
                reasons=(
                    f"scope:{query_scope}",
                    "exact_question" if exact else "local_similarity",
                    f"margin:{margin}",
                ),
            )
            for score, _record_id_value, record, exact in selected
        ]


def record_from_example(item: Mapping[str, object]) -> KnowledgeRecord:
    """Build an immutable record without modifying its question or answer text."""
    if not isinstance(item, Mapping):
        raise KnowledgeSchemaError("knowledge example must be a mapping")

    question = _required_core_text(item, "question")
    reply = _required_core_text(item, "reply")
    source_file = _optional_string(item, "source_file")
    source_locator = _optional_string(item, "source_locator")
    source_excerpt = _optional_string(item, "source_excerpt")
    organization_type = _optional_string(item, "organization_type")
    keywords = _canonical_keywords(_keywords_from_item(item))
    scope = _scope_from_item(item, organization_type)
    status = _status_from_item(
        item,
        scope,
        has_complete_provenance=_has_complete_provenance(
            source_file, source_locator, source_excerpt
        ),
    )

    answer_hash = _answer_hash(reply)
    source_hash = _source_hash(source_excerpt)
    semantic_duplicate_key = _semantic_duplicate_key(question, reply, scope)
    record_id = _record_id(
        question=question,
        org_scope=scope,
        answer_hash=answer_hash,
        source_file=source_file,
        source_locator=source_locator,
        source_hash=source_hash,
        organization_type=organization_type,
        keywords=keywords,
    )
    return KnowledgeRecord(
        record_id=record_id,
        question=question,
        reply=reply,
        org_scope=scope,
        keywords=keywords,
        source_file=source_file,
        source_locator=source_locator,
        source_hash=source_hash,
        answer_hash=answer_hash,
        semantic_duplicate_key=semantic_duplicate_key,
        status=status,
        source_excerpt=source_excerpt,
        organization_type=organization_type,
    )
