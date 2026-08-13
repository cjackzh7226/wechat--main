# SDD ledger — plan: D:\Vibe C\客服\wechat--main\docs\superpowers\plans\2026-08-12-01-persistence-and-settings-hardening.md
Execution mode: non-Git in-place workspace authorized by user; project/runtime ZIP backups are the recovery baseline.
Baseline: 297 passed, 74 warnings, 4 subtests passed; production memory SHA-256 unchanged A8AE540D89ED970AF9D4CE788BD09EF35D72B75B37210445E60364DBA6F567FE.
Task 1: dispatched
Task 1: minor (deferred): tests/test_persistence.py does not yet assert credential_path, ledger_path, and logs_dir; final review must triage after their consuming tasks add coverage.
Task 1: fix round 1/5 pending — review package omitted the existing 01-paths.sha256.json checkpoint evidence.
Task 1: fix round 1/5 (1 addressed, 0 open — checkpoint JSON and live hashes included in fix evidence package; non-Git)
Task 1: complete (non-Git snapshot/checkpoint, scoped re-review clean)
Task 2: minor (deferred): atomic_json backup_count input is not constrained while load scans five slots; final review must triage API consistency.
Task 2: fix round 1/5 pending — corrupt backup slots can overwrite valid older backups during rotation; mixed valid/corrupt regression coverage missing.
Task 2: fix round 1/5 (2 addressed, 0 open — valid backups compacted before rotation; mixed valid/corrupt regression added; non-Git)
Task 2: complete (non-Git snapshot/checkpoint, scoped re-review clean)
Task 3: dispatched
Task 3: minor (deferred): unhashable managed_mode should normalize to SettingsValidationError; tooltip still describes removed model fallback; report test-count evidence says 102+supplement rather than claimed 103.
Task 3: fix round 1/5 pending — plaintext legacy settings survive in backups; custom templates can persist literal secrets; old UI callbacks corrupt source/mode state; load validator misses persisted-field types.
Task 3: fix round 1/5 (3 addressed, 2 open — custom credential names X-Auth/Proxy-Authorization bypass guard; malformed v1 credential fields still block valid backup)
Task 3: fix round 2/5 pending — close compound credential-name and v1 candidate-validation gaps.
Task 3: fix round 2/5 (2 addressed, 0 open — compound auth/token names guarded; malformed v1 credentials yield to valid backup)
Task 3: complete (non-Git snapshot/checkpoint, scoped re-review clean)
Task 4: dispatched
Task 4: minor (deferred): 3001-record test distinguishes hard truncation but not the exact metadata-preserving dedupe boundary; final knowledge-schema review must triage.
Task 4: cross-task ruling: this task must expose persistent vector health and a typed blocking error; main/preflight consumption is completed in Task 5/runtime plans because Task 4 file scope excludes main.py.
Task 4: fix round 1/5 pending — vector failure health absent; missing primary ignores backups; semantic memory validation incomplete; vector validator admits non-finite/invalid buckets.
Task 4: fix round 1/5 (3 addressed, 1 open — validator-accepted one-character memory example can be silently omitted from an items=[] vector index marked ready)
Task 4: fix round 2/5 pending — ensure every valid non-empty memory example produces a non-empty vector item.
Task 4: fix round 2/5 (1 addressed, 0 open — single-character ASCII/Chinese examples now generate stable non-empty vectors shared by queries)
Task 4: complete (non-Git snapshot/checkpoint, scoped re-review clean)
Task 5: dispatched
Task 5: root review found 2 Critical / 3 Important / 1 Minor — log-schema smuggling, queued-send TOCTOU, typed startup gaps, concurrent handler duplication, and incomplete full-suite/formal-AppData isolation.
Task 5: fix round 1/5 (4 original findings and original Minor addressed; C1 still open because generic safe-token fields allow legal-shape secrets; new Important: production diagnostic reasons are discarded)
Task 5: fix round 2/5 (C1 and diagnostic observability addressed — per-field enums, HMAC record IDs, bounded scores, formatter timestamps, and 12 explicit production diagnostic codes)
Task 5: minor (deferred): case-variant Windows paths could install duplicate handlers; a corrupt runtime salt can make diagnostic logging silently unavailable. Final phase review must triage both.
Task 5: complete (root scoped re-review PASS; 386 passed + 6 subtests, JUnit 392/0/0, compileall 0, 11-file manifest mismatch 0, formal AppData 32-file mismatch 0)
Phase final review: initial Needs fixes (2 Critical / 3 Important / 7 Minor); cross-module knowledge-only fallback, judgement typing, credential JSON semantics, transactional backups, and persistent send idempotency required fixes.
Phase final fix round 1: closed original C1/C2/I1/I2/I3 and related M2/M3/M4/M6/M7, then targeted re-review found group-send serialization/live-preflight, decoded-key, concurrent-snapshot, and ledger-hash gaps.
Phase final fix round 2: closed R1-R5; full 537 passed + 6 subtests. A formal AppData process incident was preserved: two runtime-log initialization files appeared after baseline and were recoverably quarantined; attribution remains unknown and the original 34-file manifests were overwritten.
Phase final gate: anonymous list string credential smuggling closed; controlled wrapper set a unique WECHAT_ASSISTANT_DATA_DIR before any Python, retained the temp root, and produced 544 passed + 6 subtests, JUnit 550/0/0, compileall 0, formal tree 32/32/32 with diff 0.
Phase 1 final assessment: PASS (0 Critical / 0 Important / 3 Minor). Remaining minors: complete observe/controlled/full state-machine semantics in Phase 3; deterministic metadata merge at the 3001-record boundary in Phase 2; retain the historical 34-file formal-AppData incident as a known process finding.
