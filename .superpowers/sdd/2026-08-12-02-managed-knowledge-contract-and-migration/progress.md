# SDD ledger — plan: D:\Vibe C\客服\wechat--main\docs\superpowers\plans\2026-08-12-02-managed-knowledge-contract-and-migration.md
Execution mode: non-Git in-place workspace authorized by user; Phase 1 final gate is the validated baseline.
Baseline: Phase 1 PASS (0 Critical / 0 Important / 3 Minor); controlled gate 544 passed + 6 subtests, JUnit 550/0/0, compileall 0.
Formal AppData: current 32-file tree; historical transient 34-file incident remains preserved in Phase 1 quarantine/incident manifest. Phase 2 must not mutate formal AppData.
Phase 2 Task 1: dispatched.

## Pause checkpoint — 2026-08-12 12:39 +08:00

Execution was paused at the user's request. No child agents or background test/implementation tasks remain active.

Task 1 initial implementation and independent review are complete. The review result is Needs fixes (0 Critical / 6 Important / 1 Minor). Fix Round 1 has closed I1, the active-provenance portion of I3, and I6 with a last confirmed focused result of 42 passed / 62 deselected.

Current implemented boundary:
- `KnowledgeRecord.__post_init__` validates direct construction and `dataclasses.replace`.
- hash shapes, non-empty core strings, scope/status enums, keyword tuples, and source-field types are enforced.
- `source_excerpt` is part of the record contract.
- `unknown + active` is prohibited.
- active requires non-empty source_file, source_locator, and source_excerpt.
- known-scope records with incomplete provenance default to pending unless explicit active was requested, in which case validation fails.

Current file hashes at pause:
- knowledge_base.py = DA6047122194427E4A95D3658733813043A6A0A55A8D73A688A3BC7E1E5F85C5
- tests/test_knowledge_contract.py = FF494EC042B067F01896EB1D91E1819108A546496B70145F74D75FD218914006

Remaining Task 1 work:
1. exact answer integrity hash plus separate normalized semantic duplicate key;
2. content-based source hash;
3. source-aware identity and deterministic provenance/metadata preservation;
4. restricted explicit scope grammar with negation/incidental-context quarantine;
5. independent golden hash vectors;
6. focused/relevant/full regression, refreshed checkpoint/report, and same-reviewer re-review.

Do not start Task 2 until Task 1 re-review passes. Formal AppData migration, deletion/replacement of the three old records, real sender/E2E, and EXE packaging have not been performed.

## Resume and workflow override — 2026-08-13

The user supplied `C:\Users\14913\Downloads\规则.txt` as the controlling development-process rule. Work now proceeds in coherent batches with risk-matched focused tests. Per-subtask dual review, repeated full-suite/build runs, routine source hashes, and small-task blocking gates are disabled. High-risk architecture/data boundaries receive consolidated review at a runnable milestone; final delivery receives one overall review and complete verification.

Pause hashes matched exactly before work resumed. Task 1 Fix Round 1 is now functionally complete:

- `answer_hash` is SHA-256 of the exact stored reply, so layout/NFKC mutations cannot pass the post-judge integrity check.
- `semantic_duplicate_key` is a separately versioned normalized question/scope/answer key.
- `source_hash` is SHA-256 of exact `source_excerpt` content rather than a location tuple.
- `record_id` is versioned and source-aware; it includes exact answer hash, source file/locator/content hash, organization metadata and canonical keywords.
- keywords are normalized, de-duplicated, sorted and stored as an immutable tuple, removing arrival-order metadata drift.
- the public dataclass validates every derived hash against its fields, so direct construction and `dataclasses.replace` cannot create stale trusted records.
- organization inference uses anchored positive label grammar and quarantines negation, questions, relationship/incidental wording and transitions.
- independent literal golden vectors pin answer/source/semantic/record hashes.

Validation actually run on this code path:

- RED focused batch: 15 failed / 11 passed / 101 deselected (expected contract gaps).
- GREEN focused batch: 26 passed / 101 deselected.
- complete contract file after the batch fix: 127 passed.
- keyword determinism self-review follow-up: 11 passed / 116 deselected.

The complete contract file was not repeated after the isolated keyword change because the affected keyword/identity set passed and no other contract code changed. No full project suite, build, E2E, independent review agent or routine post-edit hash was run at this small boundary.

Environment note: the checked-in `.venv` launcher is currently broken because its configured base `C:\Users\14913\AppData\Local\Programs\Python\Python311\python.exe` no longer exists. Pure contract tests were run with Codex bundled Python 3.12.13 plus the existing `.venv\Lib\site-packages`. Restore/recreate the target Python 3.11.9 environment before a Phase 2 runnable milestone or release validation; do not treat the alternate interpreter as final compatibility proof.

## Tasks 2–5 consolidated progress — 2026-08-13

- Task 2 complete: local scope resolution, conflict/duplicate audit, cross-scope preservation, candidate thresholds and margin are implemented.
- Task 3 complete: strict candidate judgement accepts exact JSON types/keys only and does not expose answer bodies to the model.
- Task 4 complete: typed managed decisions are consumed by personal and group send paths and revalidated immediately before sender invocation; only exact knowledge text or `暂未查到对应答案` is sendable.
- Task 5 complete: whole-document transactional import is implemented in `knowledge_import.py`; structured files bypass the model, unstructured files are never split/truncated, >12000 characters fail explicitly, source excerpts and verbatim answers are verified, scope is locally derived from verified source text, PDF page failures block activation, checkpoints are atomic per completed file, and activation performs one audited replacement.
- The obsolete production `_chunk_text`/old LLM extraction path was removed. The legacy public import function is now a compatibility wrapper over the strict importer and deliberately ignores its obsolete `max_examples` ceiling.
- Memory replacement preserves exact answer/source fields and no longer truncates long knowledge metadata. Packaging declares `openpyxl` and `pypdf`.
- Task 5 validation: `tests/test_knowledge_contract.py` 183 passed; impacted `test_core.py` import/training/PDF/XLSX/long/build-release subset 46 passed, 210 deselected; relevant `py_compile` exit 0.
- Skipped by workflow rule: project-wide suite, EXE build, independent review and ordinary source hashes. These are deferred to the Phase 2 runnable milestone.
- Next: Task 6 read-only deterministic 449-record migration dry run. Formal AppData and its three old records remain untouched.

## Pause checkpoint — 2026-08-13 (latest)

Execution was paused immediately at the user's request. No code changes or tests will continue until the user explicitly resumes. Legacy child tasks were interrupted; no active implementation/test command is being waited on.

Completed since the preceding entry:

- Target runtime was restored at `C:\Users\14913\AppData\Local\Programs\Python\Python311\python.exe`; the existing `.venv` is usable again with Python 3.11.9 and its original dependencies.
- Task 6 deterministic migration tool and tests were completed. The real v4 source contains 449 rows; the dry-run produced 435 canonical output records, 14 exact duplicates, 17 conflict groups covering 36 source rows, and retained all 449 source rows in the archive section.
- The real dry-run report is `artifacts/formal-hardening/2026-08-13/knowledge-migration-dry-run.json`. It was a dry run only (`applied=false`). The formal target stayed at 3 records and its before/after SHA-256 stayed `9DD71CBD01468025011311F5481F115C6B39946E74842FAF81ED26FBABE1F2C8`.
- The first Phase 2 full run exposed 21 stale-fixture/integration failures and was preserved as `phase-02-knowledge-attempt-1-failed.xml`. After updating typed `ReplyDecision` fixtures and readiness ordering, the next target-runtime run passed: 748 tests + 9 subtests, 0 failures, with 74 pre-existing Pillow deprecation warnings.
- Milestone self-review then found and fixed a real conflict-bypass/persistence defect: exact duplicates with different statuses now collapse deterministically, conflict status dominates, re-importing one active answer cannot bypass an unresolved conflict group, and memory replacement persists canonical audited records instead of pre-audit archive statuses.
- Self-review also fixed `StyleMemory.knowledge_records()` stripping exact question text and coercing invalid values through `str(...)`; it now preserves exact questions and validates field types. The focused regression for these latest changes passed (5 conflict/persistence cases, then 1 exact-question/type case).

Current validation boundary:

- `artifacts/formal-hardening/2026-08-13/phase-02-knowledge.xml` is green for the state before the two latest self-review fixes and is therefore intentionally marked stale.
- No final Phase 2 full-suite rerun, final `py_compile`, final file manifest, Task 6 report, Phase 2 review report or Phase 2 completion claim has been made after those fixes.
- No new pause hash was generated: the milestone source is intentionally still in a locally focused, not-yet-final-gated state. Resume should inspect only the listed files and then perform the planned focused tests before the one final milestone gate.

Formal-data and release boundary:

- Formal AppData has not been migrated or deliberately changed by Phase 2.
- The 3 existing formal records have not been deleted.
- No real Enterprise WeChat message was sent and no EXE was built.

Exact resume entry:

1. Review and, if warranted, minimally fix the remaining strict-contract candidates: import checkpoint/live-data path collision, `ImportRecord` unknown+active validation, pending-count UI notice, and deterministic archive ordering for same record IDs.
2. Run one focused batch covering `tests/test_knowledge_contract.py` and `tests/test_migration.py`; do not repeat already-green unrelated commands.
3. Re-run the real migration in dry-run mode only and verify the formal target before/after remains byte-identical.
4. Run one final target-runtime full suite and one final `py_compile`, then create the meaningful Phase 2 source/test/JUnit/migration manifest.
5. Write `task-6-report.md`, the Phase 2 milestone review, update this ledger and `.workbuddy/memory/2026-08-13.md`; only after a PASS may Phase 3 begin.

## Phase 2 completion and pause — 2026-08-13

Phase 2 has completed its final milestone gate and is now **PASS**. Work is paused at the user's revised boundary; Phase 3 has not started.

Final work completed after the preceding pause checkpoint:

- Closed the remaining strict-contract candidates: checkpoint/source/live-data path collision, unknown+active import records, pending/conflict UI counts, deterministic archive ordering, forged audit revalidation, and a shared runtime/audit/migration question match key.
- The shared question key exposed two additional real conflict groups that differ only by spaces around digits. The honest migration baseline is therefore 19 conflict groups / 40 source rows, not the earlier 17 / 36.
- Hardened dry-run evidence: it now acquires the single-instance guard, detects target drift by rereading the formal target, and refuses to use any runtime-data or backup/transaction path as its report target.
- Final focused contract+migration batch: 220 passed.
- Final target-runtime full suite: 768 passed + 9 subtests, 0 failed, 74 pre-existing Pillow deprecation warnings. JUnit contains 777 tests, 0 failures/errors/skips.
- Target Python 3.11.9 `py_compile` for 11 affected modules exited 0.
- The authoritative pytest session guard recorded formal AppData before/after as 32 files with byte/size/mtime manifests exactly equal. The auxiliary PowerShell wrapper's unsupported `Path.GetRelativePath` attempt is disclosed and was not used as pass evidence.
- Final real migration dry-run: source 449, output 435, exact duplicates 14, conflicts 19 groups / 40 rows, archive 449, `applied=false`; the formal target remained 3 records and byte-identical with SHA-256 `9DD71CBD01468025011311F5481F115C6B39946E74842FAF81ED26FBABE1F2C8`.
- Reports written: `task-6-report.md` and `phase-02-review.md`.
- Final recovery/integrity manifest: `artifacts/formal-hardening/2026-08-13/phase-02-files.sha256.json`, 28 files, verification mismatch 0.

Boundaries at pause:

- Formal migration was not applied and the 3 old formal records were not deleted.
- No real WeCom/WeChat message was sent.
- No EXE was built.
- Phase 3 was not started.

Resume entry: read `phase-02-review.md`, verify the final Phase 2 manifest if files may have drifted, then begin Phase 3 only under a new explicit continuation. Preserve all formal-data and external-send gates.
