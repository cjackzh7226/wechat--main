"""Release audit for the formal managed-knowledge source."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_base import KnowledgeRecord, KnowledgeRepository, question_match_key, record_from_example
from tools.migrate_formal_knowledge import MigrationBlocked, build_migration


KNOWN_QUESTION = "如果账号密码忘记怎么办？"
KNOWN_ANSWER = "进入福建省网上办事大厅用户登录页面，找到“忘记密码”按操作找回。"
NO_MATCH_QUESTION = "这是一个知识库中不存在的测试问题XYZ20260812"
_HASH = re.compile(r"[0-9a-f]{64}")


def _record_mapping(record: KnowledgeRecord) -> dict[str, object]:
    value = asdict(record)
    value["keywords"] = list(record.keywords)
    return value


def _hash_integrity_errors(records: tuple[KnowledgeRecord, ...]) -> list[str]:
    errors: list[str] = []
    for record in records:
        try:
            rebuilt = record_from_example(_record_mapping(record))
        except Exception:
            errors.append(record.record_id)
            continue
        if rebuilt != record or any(
            _HASH.fullmatch(value) is None
            for value in (record.record_id, record.answer_hash, record.source_hash)
        ):
            errors.append(record.record_id)
    return sorted(errors)


def _missing_fields(records: tuple[KnowledgeRecord, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        if not record.question:
            counts["question"] += 1
        if not record.reply:
            counts["reply"] += 1
        if record.status == "active":
            for field in ("source_file", "source_locator", "source_excerpt"):
                if not getattr(record, field):
                    counts[field] += 1
    return dict(sorted(counts.items()))


def _cross_scope_differing_answer_count(records: tuple[KnowledgeRecord, ...]) -> int:
    grouped: dict[str, dict[str, set[str]]] = {}
    for record in records:
        if record.status == "inactive" or record.org_scope not in {"social", "mfei"}:
            continue
        grouped.setdefault(question_match_key(record.question), {}).setdefault(
            record.org_scope, set()
        ).add(record.answer_hash)
    return sum(
        1
        for scopes in grouped.values()
        if set(scopes) == {"social", "mfei"}
        and scopes["social"] != scopes["mfei"]
    )


def audit_memory(source: Path | str, *, expected_source_count: int = 449) -> dict[str, object]:
    result = build_migration(source, expected_source_count=expected_source_count)
    records = result.audited_records
    repository = KnowledgeRepository(records)
    known_candidates = repository.candidates(KNOWN_QUESTION, KNOWN_QUESTION)
    known_record = (
        repository.get_active(known_candidates[0].record_id)
        if known_candidates
        else None
    )
    no_match_candidates = repository.candidates(NO_MATCH_QUESTION, NO_MATCH_QUESTION)
    report: dict[str, object] = {
        "schema_version": 1,
        "source_path": str(Path(source).resolve()),
        "source_sha256": result.source_sha256,
        "source_count": result.source_count,
        "archive_count": len(result.archive_records),
        "output_count": result.output_count,
        "scope_counts": result.scope_counts,
        "status_counts": result.status_counts,
        "exact_duplicate_count": result.exact_duplicate_count,
        "conflict_group_count": result.conflict_group_count,
        "conflict_record_count": result.conflict_record_count,
        "cross_scope_differing_answer_count": _cross_scope_differing_answer_count(records),
        "missing_fields": _missing_fields(records),
        "hash_integrity_errors": _hash_integrity_errors(records),
        "recall": {
            "known_match": {
                "question": KNOWN_QUESTION,
                "candidate_count": len(known_candidates),
                "record_id": known_record.record_id if known_record else "",
                "answer": known_record.reply if known_record else "",
                "expected_answer_match": bool(known_record and known_record.reply == KNOWN_ANSWER),
            },
            "no_match": {
                "question": NO_MATCH_QUESTION,
                "candidate_count": len(no_match_candidates),
            },
        },
    }
    report["ok"] = bool(
        not report["missing_fields"]
        and not report["hash_integrity_errors"]
        and report["recall"]["known_match"]["expected_answer_match"]
        and report["recall"]["no_match"]["candidate_count"] == 0
    )
    return report


def _summary(report: dict[str, object]) -> str:
    recall = report["recall"]
    return "\n".join(
        (
            "# 正式知识库审计摘要",
            "",
            f"- 结论：{'PASS' if report['ok'] else 'FAIL'}",
            f"- 来源记录：{report['source_count']}；规范输出：{report['output_count']}；来源归档：{report['archive_count']}",
            f"- 完全重复：{report['exact_duplicate_count']}；同分区冲突：{report['conflict_group_count']} 组 / {report['conflict_record_count']} 条",
            f"- 分区统计：{json.dumps(report['scope_counts'], ensure_ascii=False, sort_keys=True)}",
            f"- 状态统计：{json.dumps(report['status_counts'], ensure_ascii=False, sort_keys=True)}",
            f"- 缺失字段：{json.dumps(report['missing_fields'], ensure_ascii=False, sort_keys=True)}",
            f"- 哈希完整性错误：{len(report['hash_integrity_errors'])}",
            f"- 已知问题召回：{'PASS' if recall['known_match']['expected_answer_match'] else 'FAIL'}",
            f"- 不存在问题无候选：{'PASS' if recall['no_match']['candidate_count'] == 0 else 'FAIL'}",
        )
    ) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计正式托管知识库")
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--expected-source-count", type=int, default=449)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_memory(
            args.memory,
            expected_source_count=args.expected_source_count,
        )
    except (MigrationBlocked, OSError, ValueError) as error:
        print(f"KNOWLEDGE_AUDIT_BLOCKED: {error}", file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.summary.write_text(_summary(report), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "source_count": report["source_count"]}, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
