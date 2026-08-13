from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tools.migrate_formal_knowledge as migration_module
from tools.migrate_formal_knowledge import (
    MigrationBlocked,
    apply_migration,
    build_migration,
    run_migration,
)


REAL_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "dist"
    / "聊小智_开发测试_20260803_v4"
    / "聊小智"
    / "user_data"
    / "memory.json"
)


def _legacy_example(index: int, *, organization_type: str = "社会团体") -> dict[str, str]:
    return {
        "partner": "企业年报知识库",
        "cue": f"{organization_type} / 问题{index}",
        "question": f"问题{index}",
        "reply": f"答案{index}",
        "source": "enterprise_annual_report_kb",
        "created_at": "2026-08-03T00:00:00+00:00",
        "conversation_hash": hashlib.sha1(str(index).encode()).hexdigest(),
        "organization_type": organization_type,
        "module": "年检",
        "subcategory": "填报",
        "source_file": "source.xlsx",
        "source_sheet": "Sheet1",
        "source_cell": f"A{index + 2}:B{index + 2}",
    }


def _write_memory(path: Path, examples: list[dict[str, str]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "style_summary": "测试",
                "examples": examples,
                "common_phrases": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_migration_requires_exact_source_count(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])

    with pytest.raises(MigrationBlocked, match="expected 449, got 1"):
        build_migration(source, expected_source_count=449)


def test_migration_enriches_missing_provenance_from_read_only_source_record(tmp_path):
    item = _legacy_example(1)
    item.pop("source_file")
    item.pop("source_sheet")
    item.pop("source_cell")
    item["cue"] += " / 社团"
    source = _write_memory(tmp_path / "source.json", [item])

    result = build_migration(source, expected_source_count=1)
    record = result.output_document["examples"][0]

    assert record["source_file"] == "source.json"
    assert record["source_locator"] == "examples[0]"
    assert record["source_excerpt"] == "答案1"
    assert record["org_scope"] == "social"
    assert record["status"] == "active"


def test_migration_preserves_question_and_answer_text_exactly(tmp_path):
    item = _legacy_example(1)
    item["question"] = "  原始问题\n"
    item["reply"] = "  第一行\n第二行  "
    source = _write_memory(tmp_path / "source.json", [item])

    result = build_migration(source, expected_source_count=1)
    record = result.output_document["examples"][0]

    assert record["question"] == item["question"]
    assert record["reply"] == item["reply"]


def test_migration_conflict_key_treats_legacy_chinese_quotes_as_formatting(tmp_path):
    rows = [
        {**_legacy_example(1), "question": "“财务审计报告不规范”问题该如何修改？", "reply": "答案一"},
        {**_legacy_example(2), "question": "财务审计报告不规范问题该如何修改？", "reply": "答案二"},
        {**_legacy_example(3), "question": "“财务审计报告不规范”问题该如何修改？", "reply": "答案三"},
    ]
    source = _write_memory(tmp_path / "source.json", rows)

    result = build_migration(source, expected_source_count=3)

    assert result.conflict_group_count == 1
    assert result.conflict_record_count == 3
    assert all(item["status"] == "conflict" for item in result.output_document["examples"])


def test_migration_conflict_key_matches_runtime_spacing_and_scope_markers(tmp_path):
    rows = [
        {
            **_legacy_example(1),
            "question": "监事不足 3 人设置监事长怎么办？",
            "reply": "答案一",
            "org_scope": "social",
        },
        {
            **_legacy_example(2),
            "question": "社会团体监事不足3人设置监事长怎么办",
            "reply": "答案二",
            "org_scope": "social",
        },
    ]
    source = _write_memory(tmp_path / "source.json", rows)

    result = build_migration(source, expected_source_count=2)

    assert result.conflict_group_count == 1
    assert result.conflict_record_count == 2
    assert all(
        item["status"] == "conflict"
        for item in result.output_document["examples"]
    )


def test_migration_counts_original_conflict_rows_before_exact_duplicate_collapse(tmp_path):
    rows = [
        {**_legacy_example(1), "question": "同一个问题？", "reply": "相同答案"},
        {**_legacy_example(2), "question": "同一个问题？", "reply": "相同答案"},
        {**_legacy_example(3), "question": "同一个问题？", "reply": "不同答案"},
    ]
    source = _write_memory(tmp_path / "source.json", rows)

    result = build_migration(source, expected_source_count=3)

    assert result.conflict_group_count == 1
    assert result.conflict_record_count == 3
    assert result.exact_duplicate_count == 1
    assert result.output_count == 2
    assert len(result.archive_records) == 3
    assert all(item["status"] == "conflict" for item in result.archive_records)
    assert all(item["status"] == "conflict" for item in result.output_document["examples"])


def test_dry_run_writes_report_but_never_changes_target(tmp_path):
    source = _write_memory(
        tmp_path / "source.json", [_legacy_example(1), _legacy_example(2)]
    )
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    report_path = tmp_path / "report.json"
    before = target.read_bytes()

    result = run_migration(
        source=source,
        target=target,
        report_path=report_path,
        expected_source_count=2,
        expected_old_target_count=1,
        apply=False,
    )

    assert result.applied is False
    assert target.read_bytes() == before
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source_count"] == 2
    assert report["target_count_before"] == 1
    assert report["target_sha256_before"] == hashlib.sha256(before).hexdigest()
    assert report["target_sha256_after"] == report["target_sha256_before"]
    assert report["archive_count"] == 2
    assert len(report["archive_records"]) == 2


def test_dry_run_rejects_report_path_that_would_overwrite_runtime_sibling(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = _write_memory(source_root / "memory.json", [_legacy_example(1)])
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    target = _write_memory(runtime_root / "memory.json", [_legacy_example(9)])
    settings_path = runtime_root / "settings.json"
    marker = b"settings-must-stay-byte-identical"
    settings_path.write_bytes(marker)

    with pytest.raises(MigrationBlocked, match="runtime data"):
        run_migration(
            source=source,
            target=target,
            report_path=settings_path,
            expected_source_count=1,
            expected_old_target_count=1,
            apply=False,
        )

    assert settings_path.read_bytes() == marker


def test_dry_run_refuses_when_runtime_single_instance_is_held(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    report = tmp_path / "report.json"

    class HeldInstance:
        def acquire(self):
            return False

        def release(self):
            raise AssertionError("unacquired instance must not be released")

    monkeypatch.setattr(migration_module, "SingleInstance", HeldInstance)

    with pytest.raises(MigrationBlocked, match="assistant is running"):
        run_migration(
            source=source,
            target=target,
            report_path=report,
            expected_source_count=1,
            expected_old_target_count=1,
            apply=False,
        )

    assert not report.exists()


def test_dry_run_rechecks_target_and_rejects_a_changed_snapshot(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    report = tmp_path / "report.json"
    states = iter(((1, "a" * 64), (1, "b" * 64)))

    monkeypatch.setattr(migration_module, "_target_state", lambda _path: next(states))

    with pytest.raises(MigrationBlocked, match="target changed during dry run"):
        run_migration(
            source=source,
            target=target,
            report_path=report,
            expected_source_count=1,
            expected_old_target_count=1,
            apply=False,
        )

    assert not report.exists()


def test_apply_refuses_without_verified_runtime_backup(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])

    with pytest.raises(MigrationBlocked, match="verified runtime backup"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
        )


def test_apply_rejects_backup_that_aliases_source_or_target(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])

    for backup in (source, target):
        before = target.read_bytes()
        with pytest.raises(MigrationBlocked, match="backup must differ"):
            apply_migration(
                source,
                target,
                expected_source_count=1,
                expected_old_target_count=1,
                required_backup=backup,
                required_backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
            )
        assert target.read_bytes() == before


def test_run_rejects_report_or_backup_in_target_persistence_slots(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    backup_slot = target.with_name(f"{target.name}.bak.1")
    backup_slot.write_bytes(b"existing-target-backup")

    with pytest.raises(MigrationBlocked, match="report path is reserved"):
        run_migration(
            source=source,
            target=target,
            report_path=backup_slot,
            expected_source_count=1,
        )
    assert backup_slot.read_bytes() == b"existing-target-backup"

    with pytest.raises(MigrationBlocked, match="backup path is reserved"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
            required_backup=backup_slot,
            required_backup_sha256=hashlib.sha256(backup_slot.read_bytes()).hexdigest(),
        )


def test_apply_requires_runtime_single_instance_guard(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    backup = tmp_path / "runtime-backup.zip"
    backup.write_bytes(b"backup")
    before = target.read_bytes()

    class BusyInstance:
        def acquire(self):
            return False

        def release(self):
            raise AssertionError("unowned mutex must not be released")

    monkeypatch.setattr(migration_module, "SingleInstance", BusyInstance)
    with pytest.raises(MigrationBlocked, match="assistant is running"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
            required_backup=backup,
            required_backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        )
    assert target.read_bytes() == before


def test_dry_run_report_failure_is_typed_and_never_changes_target(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    before = target.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(migration_module, "atomic_write_json", fail_write)
    with pytest.raises(MigrationBlocked, match="report write failed: OSError"):
        run_migration(
            source=source,
            target=target,
            report_path=tmp_path / "report.json",
            expected_source_count=1,
        )
    assert target.read_bytes() == before


def test_apply_converts_backup_read_error_to_typed_block(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    backup = tmp_path / "runtime-backup.zip"
    backup.write_bytes(b"backup")
    original_read_bytes = Path.read_bytes

    def fail_backup_read(path):
        if path.resolve() == backup.resolve():
            raise OSError("private filesystem detail")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_backup_read)
    with pytest.raises(MigrationBlocked, match="cannot read required runtime backup"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
            required_backup=backup,
            required_backup_sha256=hashlib.sha256(b"backup").hexdigest(),
        )


def test_apply_atomic_failure_leaves_target_unchanged(tmp_path, monkeypatch):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    backup = tmp_path / "runtime-backup.zip"
    backup.write_bytes(b"backup")
    before = target.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("injected atomic failure")

    monkeypatch.setattr(migration_module, "atomic_write_json", fail_write)
    with pytest.raises(MigrationBlocked, match="target write failed: OSError"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
            required_backup=backup,
            required_backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        )
    assert target.read_bytes() == before


def test_migration_rejects_boolean_and_negative_expected_counts(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])

    for invalid in (True, -1):
        with pytest.raises(MigrationBlocked, match="expected source count"):
            build_migration(source, expected_source_count=invalid)


def test_apply_replaces_old_records_instead_of_merging(tmp_path):
    source = _write_memory(
        tmp_path / "source.json", [_legacy_example(1), _legacy_example(2)]
    )
    old = [
        {
            **_legacy_example(90 + index),
            "source": "chat_zip_training",
        }
        for index in range(3)
    ]
    target = _write_memory(tmp_path / "target.json", old)
    backup = tmp_path / "runtime-backup.zip"
    backup.write_bytes(b"verified-runtime-backup")
    backup_sha = hashlib.sha256(backup.read_bytes()).hexdigest()

    result = apply_migration(
        source,
        target,
        expected_source_count=2,
        expected_old_target_count=3,
        required_backup=backup,
        required_backup_sha256=backup_sha,
    )

    data = json.loads(target.read_text(encoding="utf-8"))
    assert result.applied is True
    assert result.source_count == 2
    assert result.target_count_before == 3
    assert len(data["examples"]) == 2
    assert all(item["source"] == "enterprise_annual_report_kb" for item in data["examples"])
    assert not any(item["source"] == "chat_zip_training" for item in data["examples"])


def test_apply_rejects_wrong_backup_hash_and_old_target_count(tmp_path):
    source = _write_memory(tmp_path / "source.json", [_legacy_example(1)])
    target = _write_memory(tmp_path / "target.json", [_legacy_example(9)])
    backup = tmp_path / "runtime-backup.zip"
    backup.write_bytes(b"backup")

    with pytest.raises(MigrationBlocked, match="backup SHA-256 mismatch"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=1,
            required_backup=backup,
            required_backup_sha256="0" * 64,
        )
    with pytest.raises(MigrationBlocked, match="expected old target count 3, got 1"):
        apply_migration(
            source,
            target,
            expected_source_count=1,
            expected_old_target_count=3,
            required_backup=backup,
            required_backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        )


def test_real_449_source_reproduces_approved_scope_and_conflict_baseline():
    result = build_migration(REAL_SOURCE, expected_source_count=449)

    assert result.source_count == 449
    assert result.scope_counts == {
        "unknown": 248,
        "social": 82,
        "mfei": 68,
        "both": 51,
    }
    assert result.conflict_group_count == 19
    assert result.conflict_record_count == 40
    assert result.exact_duplicate_count == 14
    assert result.output_count == 435
    assert len(result.archive_records) == 449
