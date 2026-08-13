from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atomic_json import atomic_write_json, recover_atomic_write
from knowledge_base import (
    KnowledgeAudit,
    KnowledgeRecord,
    KnowledgeSchemaError,
    audit_records,
    question_match_key,
    record_from_example,
)
from memory import MEMORY_SCHEMA_VERSION, validate_memory_document
from single_instance import SingleInstance


class MigrationBlocked(RuntimeError):
    """A migration precondition failed before the formal target was touched."""


@dataclass(frozen=True)
class MigrationResult:
    source_count: int
    source_sha256: str
    scope_counts: dict[str, int]
    status_counts: dict[str, int]
    conflict_group_count: int
    conflict_record_count: int
    exact_duplicate_count: int
    output_count: int
    archive_records: tuple[dict[str, object], ...]
    audited_records: tuple[KnowledgeRecord, ...]
    output_document: dict[str, object]
    applied: bool = False
    target_count_before: int | None = None
    target_sha256_before: str = ""
    target_sha256_after: str = ""
    required_backup: str = ""
    required_backup_sha256: str = ""

    def to_report(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_count": self.source_count,
            "source_sha256": self.source_sha256,
            "scope_counts": dict(self.scope_counts),
            "status_counts": dict(self.status_counts),
            "conflict_group_count": self.conflict_group_count,
            "conflict_record_count": self.conflict_record_count,
            "exact_duplicate_count": self.exact_duplicate_count,
            "output_count": self.output_count,
            "archive_count": len(self.archive_records),
            "archive_records": [dict(record) for record in self.archive_records],
            "target_count_before": self.target_count_before,
            "target_sha256_before": self.target_sha256_before,
            "target_sha256_after": self.target_sha256_after,
            "required_backup": self.required_backup,
            "required_backup_sha256": self.required_backup_sha256,
            "applied": self.applied,
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json_object(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise MigrationBlocked(f"cannot read {label}: {type(error).__name__}") from error
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationBlocked(f"invalid {label} JSON: {type(error).__name__}") from error
    if not isinstance(value, dict):
        raise MigrationBlocked(f"{label} root must be an object")
    return value, payload


def _examples_from_document(document: Mapping[str, object], label: str) -> list[dict[str, object]]:
    examples = document.get("examples")
    if type(examples) is not list:
        raise MigrationBlocked(f"{label} examples must be a list")
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(examples):
        if type(item) is not dict:
            raise MigrationBlocked(f"{label} examples[{index}] must be an object")
        normalized.append(dict(item))
    return normalized


def _source_locator(item: Mapping[str, object]) -> str:
    explicit = item.get("source_locator", "")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    sheet = item.get("source_sheet", "")
    cell = item.get("source_cell", "")
    if not isinstance(sheet, str) or not isinstance(cell, str):
        raise KnowledgeSchemaError("source sheet and cell must be strings")
    sheet = sheet.strip()
    cell = cell.strip()
    return f"{sheet}!{cell}" if sheet and cell else cell


def _string(item: Mapping[str, object], field: str, default: str = "") -> str:
    value = item.get(field, default)
    if not isinstance(value, str):
        raise KnowledgeSchemaError(f"{field} must be a string")
    return value


def _adapt_source_example(
    item: Mapping[str, object], *, migration_source_name: str, source_index: int
) -> KnowledgeRecord:
    original_question = _string(item, "question")
    question = (
        original_question
        if original_question.strip()
        else _string(item, "cue")
    )
    reply = _string(item, "reply")
    source_file = _string(item, "source_file").strip()
    locator = _source_locator(item)
    source_excerpt = _string(item, "source_excerpt")
    if not source_excerpt and source_file and locator:
        source_excerpt = f"{question}\t{reply}"
    if not source_file or not locator:
        source_file = migration_source_name
        locator = f"examples[{source_index}]"
        source_excerpt = reply
    keywords = [
        value.strip()
        for value in (
            _string(item, "module"),
            _string(item, "subcategory"),
            _string(item, "keywords"),
        )
        if value.strip()
    ]
    raw: dict[str, object] = {
        "question": question,
        "reply": reply,
        "organization_type": _string(item, "organization_type"),
        "keywords": keywords,
        "source_file": source_file,
        "source_locator": locator,
        "source_excerpt": source_excerpt,
    }
    raw_scope = item.get("org_scope")
    if raw_scope not in (None, ""):
        raw["org_scope"] = raw_scope
    elif not _string(item, "organization_type").strip():
        legacy_scope = _scope_from_legacy_cue_tags(item)
        if legacy_scope:
            raw["org_scope"] = legacy_scope
    raw_status = item.get("status")
    if raw_status not in (None, ""):
        raw["status"] = raw_status
    return record_from_example(raw)


def _metadata_for_output(item: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in (
        "partner",
        "cue",
        "created_at",
        "conversation_hash",
        "module",
        "subcategory",
        "source_sheet",
        "source_cell",
    ):
        value = item.get(field, "")
        if isinstance(value, str) and value:
            result[field] = value
    return result


def _scope_from_legacy_cue_tags(item: Mapping[str, object]) -> str:
    """Read only the explicit legacy suffix tags; never infer scope from answer text."""
    cue = item.get("cue", "")
    if not isinstance(cue, str):
        raise KnowledgeSchemaError("cue must be a string")
    has_social = re.search(r"(?:^|\s)/\s*社团(?:\s|$)", cue) is not None
    has_mfei = re.search(r"(?:^|\s)/\s*民非(?:\s|$)", cue) is not None
    if has_social and has_mfei:
        return "both"
    if has_social:
        return "social"
    if has_mfei:
        return "mfei"
    return ""


def _record_to_memory_example(
    record: KnowledgeRecord, metadata: Mapping[str, str]
) -> dict[str, str]:
    result = {
        "partner": metadata.get("partner", "企业年报知识库"),
        "cue": metadata.get("cue", f"[对方] {record.question}"),
        "reply": record.reply,
        "source": "enterprise_annual_report_kb",
        "created_at": metadata.get("created_at", ""),
        "conversation_hash": metadata.get("conversation_hash", record.record_id),
        "question": record.question,
        "organization_type": record.organization_type,
        "source_file": record.source_file,
        "source_locator": record.source_locator,
        "source_excerpt": record.source_excerpt,
        "org_scope": record.org_scope,
        "status": record.status,
        "keywords": "、".join(record.keywords),
    }
    for field in ("module", "subcategory", "source_sheet", "source_cell"):
        value = metadata.get(field, "")
        if value:
            result[field] = value
    return result


def _archive_entry(
    index: int, record: KnowledgeRecord, original: Mapping[str, object]
) -> dict[str, object]:
    return {
        "source_index": index,
        "record_id": record.record_id,
        "answer_hash": record.answer_hash,
        "source_hash": record.source_hash,
        "semantic_duplicate_key": record.semantic_duplicate_key,
        "org_scope": record.org_scope,
        "status": record.status,
        "source_file": record.source_file,
        "source_locator": record.source_locator,
        "original": dict(original),
    }


def _ordered_counts(values: Counter[str], order: tuple[str, ...]) -> dict[str, int]:
    return {key: int(values.get(key, 0)) for key in order}


def _migration_question_key(question: str) -> str:
    """Use the exact same semantic question key as managed runtime scoring."""
    return question_match_key(question)


def _migration_conflicting_groups(
    records: list[KnowledgeRecord],
) -> dict[tuple[str, str], list[KnowledgeRecord]]:
    grouped: dict[tuple[str, str], list[KnowledgeRecord]] = {}
    for record in records:
        if record.status != "inactive":
            grouped.setdefault(
                (_migration_question_key(record.question), record.org_scope),
                [],
            ).append(record)
    return {
        key: group
        for key, group in grouped.items()
        if len({record.answer_hash for record in group}) > 1
    }


def _migration_audit(records: list[KnowledgeRecord]) -> KnowledgeAudit:
    """Count source-row conflicts, then collapse only exact output duplicates."""
    base = audit_records(records)
    conflicting_groups = _migration_conflicting_groups(records)
    audited_records = tuple(
        sorted(
            (
                replace(record, status="conflict")
                if (_migration_question_key(record.question), record.org_scope)
                in conflicting_groups
                else record
                for record in base.records
            ),
            key=lambda record: record.record_id,
        )
    )
    return KnowledgeAudit(
        records=audited_records,
        archive_records=base.archive_records,
        duplicate_record_groups=base.duplicate_record_groups,
        conflict_group_count=len(conflicting_groups),
        conflict_record_count=sum(len(group) for group in conflicting_groups.values()),
        exact_duplicate_count=base.exact_duplicate_count,
    )


def _require_expected_count(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise MigrationBlocked(f"{name} must be a non-negative integer")
    return value


def build_migration(
    source: Path | str, *, expected_source_count: int
) -> MigrationResult:
    expected_source_count = _require_expected_count(
        "expected source count", expected_source_count
    )
    source_path = Path(source)
    document, payload = _read_json_object(source_path, "source")
    examples = _examples_from_document(document, "source")
    if len(examples) != expected_source_count:
        raise MigrationBlocked(
            f"expected {expected_source_count}, got {len(examples)} source records"
        )

    records: list[KnowledgeRecord] = []
    metadata_by_record_id: dict[str, dict[str, str]] = {}
    for index, item in enumerate(examples):
        try:
            record = _adapt_source_example(
                item,
                migration_source_name=source_path.name,
                source_index=index,
            )
        except (KnowledgeSchemaError, TypeError, ValueError) as error:
            raise MigrationBlocked(
                f"source record {index} is invalid: {error}"
            ) from error
        records.append(record)
        metadata_by_record_id.setdefault(record.record_id, _metadata_for_output(item))

    audit = _migration_audit(records)
    conflicting_groups = _migration_conflicting_groups(records)
    archive = [
        _archive_entry(
            index,
            replace(record, status="conflict")
            if (_migration_question_key(record.question), record.org_scope)
            in conflicting_groups
            else record,
            item,
        )
        for index, (record, item) in enumerate(zip(records, examples, strict=True))
    ]
    output_examples = [
        _record_to_memory_example(
            record, metadata_by_record_id.get(record.record_id, {})
        )
        for record in audit.records
    ]
    output_document: dict[str, object] = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "style_summary": document.get(
            "style_summary", "偏短句、微信口语、少解释、少承诺。"
        ),
        "examples": output_examples,
        "common_phrases": document.get("common_phrases", {}),
        "migration": {
            "source_sha256": _sha256_bytes(payload),
            "source_count": len(records),
            "archive_count": len(archive),
            "output_count": len(output_examples),
        },
    }
    try:
        validate_memory_document(output_document)
    except ValueError as error:
        raise MigrationBlocked(f"migrated memory validation failed: {error}") from error

    return MigrationResult(
        source_count=len(records),
        source_sha256=_sha256_bytes(payload),
        scope_counts=_ordered_counts(
            Counter(record.org_scope for record in records),
            ("unknown", "social", "mfei", "both"),
        ),
        status_counts=_ordered_counts(
            Counter(record.status for record in audit.records),
            ("active", "conflict", "pending", "inactive"),
        ),
        conflict_group_count=audit.conflict_group_count,
        conflict_record_count=audit.conflict_record_count,
        exact_duplicate_count=audit.exact_duplicate_count,
        output_count=len(audit.records),
        archive_records=tuple(archive),
        audited_records=audit.records,
        output_document=output_document,
    )


def _target_state(target: Path) -> tuple[int, str]:
    if not target.exists():
        return 0, ""
    document, payload = _read_json_object(target, "target")
    examples = _examples_from_document(document, "target")
    return len(examples), _sha256_bytes(payload)


def _verified_backup(path: Path | None, expected_sha256: str) -> tuple[str, str]:
    if path is None or not expected_sha256:
        raise MigrationBlocked("apply requires a verified runtime backup")
    if not path.is_file():
        raise MigrationBlocked("required runtime backup does not exist")
    normalized = expected_sha256.strip().casefold()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise MigrationBlocked("required backup SHA-256 is invalid")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise MigrationBlocked(
            f"cannot read required runtime backup: {type(error).__name__}"
        ) from error
    actual = _sha256_bytes(payload)
    if actual != normalized:
        raise MigrationBlocked(
            f"backup SHA-256 mismatch: expected {normalized}, got {actual}"
        )
    return str(path.resolve()), actual


def _is_reserved_persistence_path(candidate: Path, primary: Path) -> bool:
    candidate = candidate.resolve()
    primary = primary.resolve()
    if candidate == primary:
        return True
    reserved_names = {
        *(f"{primary.name}.bak.{index}" for index in range(1, 6)),
        f".{primary.name}.transaction",
    }
    return candidate.parent == primary.parent and (
        candidate.name in reserved_names
        or candidate.name.startswith(f".{primary.name}.transaction.prep-")
    )


def _is_runtime_data_path(candidate: Path, runtime_primary: Path) -> bool:
    candidate = candidate.resolve()
    runtime_root = runtime_primary.resolve().parent
    for name in (
        "settings.json",
        "memory.json",
        "vector_memory.json",
        "credentials.dat",
        "send_ledger.json",
        "knowledge_import_stage.json",
        "sop_library.json",
    ):
        if _is_reserved_persistence_path(candidate, runtime_root / name):
            return True
    logs_root = runtime_root / "logs"
    candidate_key = str(candidate).casefold()
    logs_key = str(logs_root).casefold()
    return candidate_key == logs_key or candidate_key.startswith(logs_key + os.sep)


def _apply_migration_locked(
    source_path: Path,
    target_path: Path,
    *,
    expected_source_count: int,
    expected_old_target_count: int,
    resolved_backup: Path | None,
    required_backup_sha256: str,
) -> MigrationResult:
    result = build_migration(source_path, expected_source_count=expected_source_count)
    try:
        recover_atomic_write(target_path)
    except Exception as error:
        raise MigrationBlocked(
            f"target recovery failed: {type(error).__name__}"
        ) from error
    target_count, before_sha = _target_state(target_path)
    if target_count != expected_old_target_count:
        raise MigrationBlocked(
            f"expected old target count {expected_old_target_count}, got {target_count}"
        )
    backup_path, backup_sha = _verified_backup(
        resolved_backup,
        required_backup_sha256,
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(
            target_path,
            result.output_document,
            validate_memory_document,
            backup_count=5,
        )
    except Exception as error:
        raise MigrationBlocked(f"target write failed: {type(error).__name__}") from error
    target_count_after, after_sha = _target_state(target_path)
    if target_count_after != result.output_count:
        raise MigrationBlocked(
            f"target verification failed: expected {result.output_count}, got {target_count_after}"
        )
    return replace(
        result,
        applied=True,
        target_count_before=target_count,
        target_sha256_before=before_sha,
        target_sha256_after=after_sha,
        required_backup=backup_path,
        required_backup_sha256=backup_sha,
    )


def apply_migration(
    source: Path | str,
    target: Path | str,
    *,
    expected_source_count: int,
    expected_old_target_count: int,
    required_backup: Path | str | None = None,
    required_backup_sha256: str = "",
) -> MigrationResult:
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if source_path == target_path:
        raise MigrationBlocked("source and target must be different files")
    expected_old_target_count = _require_expected_count(
        "expected old target count", expected_old_target_count
    )
    resolved_backup = (
        Path(required_backup).resolve() if required_backup is not None else None
    )
    if resolved_backup in {source_path, target_path}:
        raise MigrationBlocked("backup must differ from source and target")
    if resolved_backup is not None and _is_reserved_persistence_path(
        resolved_backup, target_path
    ):
        raise MigrationBlocked("backup path is reserved for target persistence")
    instance = SingleInstance()
    try:
        acquired = instance.acquire()
    except OSError as error:
        raise MigrationBlocked(
            f"cannot acquire runtime single-instance guard: {type(error).__name__}"
        ) from error
    if not acquired:
        raise MigrationBlocked("assistant is running; close it before apply")
    try:
        return _apply_migration_locked(
            source_path,
            target_path,
            expected_source_count=expected_source_count,
            expected_old_target_count=expected_old_target_count,
            resolved_backup=resolved_backup,
            required_backup_sha256=required_backup_sha256,
        )
    finally:
        instance.release()


def _validate_report(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("migration report root must be an object")
    required = {
        "schema_version",
        "source_count",
        "source_sha256",
        "scope_counts",
        "status_counts",
        "conflict_group_count",
        "conflict_record_count",
        "exact_duplicate_count",
        "output_count",
        "archive_count",
        "archive_records",
        "target_count_before",
        "target_sha256_before",
        "target_sha256_after",
        "required_backup",
        "required_backup_sha256",
        "applied",
    }
    if set(value) != required:
        raise ValueError("migration report fields mismatch")
    if value["schema_version"] != 1 or type(value["applied"]) is not bool:
        raise ValueError("invalid migration report schema")
    if type(value["archive_records"]) is not list:
        raise ValueError("migration archive must be a list")
    if value["archive_count"] != len(value["archive_records"]):
        raise ValueError("migration archive count mismatch")


def run_migration(
    *,
    source: Path | str,
    target: Path | str,
    report_path: Path | str,
    expected_source_count: int,
    expected_old_target_count: int | None = None,
    apply: bool = False,
    required_backup: Path | str | None = None,
    required_backup_sha256: str = "",
) -> MigrationResult:
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    report = Path(report_path).resolve()
    if report in {source_path, target_path}:
        raise MigrationBlocked("report path must differ from source and target")
    if _is_reserved_persistence_path(report, source_path) or _is_reserved_persistence_path(
        report, target_path
    ):
        raise MigrationBlocked("report path is reserved for source or target persistence")
    if _is_runtime_data_path(report, source_path) or _is_runtime_data_path(
        report, target_path
    ):
        raise MigrationBlocked("report path must not overwrite runtime data")
    if apply:
        if expected_old_target_count is None:
            raise MigrationBlocked("apply requires expected old target count")
        if required_backup is not None and Path(required_backup).resolve() == report:
            raise MigrationBlocked("backup must differ from report path")
        result = apply_migration(
            source_path,
            target_path,
            expected_source_count=expected_source_count,
            expected_old_target_count=expected_old_target_count,
            required_backup=required_backup,
            required_backup_sha256=required_backup_sha256,
        )
    else:
        instance = SingleInstance()
        try:
            acquired = instance.acquire()
        except OSError as error:
            raise MigrationBlocked(
                f"cannot acquire runtime single-instance guard: {type(error).__name__}"
            ) from error
        if not acquired:
            raise MigrationBlocked("assistant is running; close it before dry run")
        try:
            target_count, target_sha_before = _target_state(target_path)
            if (
                expected_old_target_count is not None
                and target_count != expected_old_target_count
            ):
                raise MigrationBlocked(
                    f"expected old target count {expected_old_target_count}, got {target_count}"
                )
            result = build_migration(
                source_path, expected_source_count=expected_source_count
            )
            target_count_after, target_sha_after = _target_state(target_path)
            if (
                target_count_after != target_count
                or target_sha_after != target_sha_before
            ):
                raise MigrationBlocked("target changed during dry run")
            result = replace(
                result,
                target_count_before=target_count,
                target_sha256_before=target_sha_before,
                target_sha256_after=target_sha_after,
            )
        finally:
            instance.release()
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(report, result.to_report(), _validate_report, backup_count=5)
    except Exception as error:
        prefix = "target was applied; " if result.applied else ""
        raise MigrationBlocked(
            f"{prefix}report write failed: {type(error).__name__}"
        ) from error
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic knowledge migration (dry-run by default)."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-source-count", type=int, required=True)
    parser.add_argument("--expected-old-target-count", type=int)
    parser.add_argument("--required-backup", type=Path)
    parser.add_argument("--required-backup-sha256", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_migration(
            source=args.source,
            target=args.target,
            report_path=args.report,
            expected_source_count=args.expected_source_count,
            expected_old_target_count=args.expected_old_target_count,
            apply=bool(args.apply),
            required_backup=args.required_backup,
            required_backup_sha256=args.required_backup_sha256,
        )
    except MigrationBlocked as error:
        print(f"MIGRATION_BLOCKED: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "source_count": result.source_count,
                "output_count": result.output_count,
                "conflict_group_count": result.conflict_group_count,
                "conflict_record_count": result.conflict_record_count,
                "applied": result.applied,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
