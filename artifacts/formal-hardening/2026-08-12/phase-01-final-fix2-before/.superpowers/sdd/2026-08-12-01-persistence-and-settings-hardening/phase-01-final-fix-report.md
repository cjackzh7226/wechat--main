# Phase 01 final review fixes — implementation report

Date: 2026-08-12  
Scope: close the Phase 1 final review's 2 Critical, 3 Important, and directly related M2/M3/M4/M6/M7 findings.  
Assessment boundary: this report hands the result back for root-level targeted review. It does **not** declare the phase finally PASS.

## Safety and pre-change evidence

- This project is not a Git worktree. Before production/test edits, a file snapshot was created at `artifacts/formal-hardening/2026-08-12/phase-01-final-fix-before`.
- Snapshot: 36 files. Manifest: `phase-01-final-fix-before-files.sha256.json`; artifact SHA-256 `cc83a0f0018ce944b0b53ff00caea2863b0f2749253d209e6378a12addcd89ca`.
- Formal AppData baseline: 32 files. Manifest: `phase-01-final-fix-formal-appdata-before.json`; SHA-256 `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2`.
- All pytest runs went through `tests/conftest.py` session/per-test `WECHAT_ASSISTANT_DATA_DIR` isolation. No WeCom/WeChat UI, real sender, network, or E2E run was started.
- Raw snapshot command output: `phase-01-final-fix-snapshot-command-output.txt`.

## Finding closure

### C1 — real `knowledge_only` contract

**RED**

- `phase-01-final-fix-c1-red.txt`: 4/4 low-information, thanks, statement, and question cases escaped to `_fallback`.
- `phase-01-final-fix-c1-fake-sender-red.txt`: the personal fake sender received the old punctuation/copy variant, while the group fake sender could receive a locally generated reply.

**Implementation**

- `reply_engine.py` now has one explicit early `knowledge_only` branch.
- That branch uses only approved enterprise/local knowledge records. It does not call `_fallback`, general `_rule_replies`, free generation, or search.
- All unreliable/no-hit paths return the exact string `暂未查到对应答案`; enterprise and general knowledge misses use the same copy.
- Old fallback assertions in `tests/test_core.py` were migrated to the current formal contract instead of weakening production behavior.

**GREEN**

- `phase-01-final-fix-c1-green.txt`: 6 passed, including personal/group fake sender integration.
- `phase-01-final-fix-c1-c2-focused-regression-green.txt`: 36 focused legacy enterprise/reply/source tests passed.

**Files**: `reply_engine.py`, `tests/test_core.py`, `tests/test_phase1_final_fix.py`.

**Concern retained**: none for the Phase 1 `knowledge_only` contract. Other source modes are not made formally selectable by this change.

### C2 — strict consistency JSON, fail closed

**RED**

- `phase-01-final-fix-c2-red.txt`: 12 malformed or semantically invalid judge payloads were accepted or did not produce the exact no-hit result.
- `phase-01-final-fix-c2-fake-sender-red.txt`: an invalid string boolean could reach the fake sender as an approved knowledge answer.

**Implementation**

- The judge result must be a JSON object with exactly `chosen` and `consistent`.
- `type(chosen) is int` (therefore not `bool`), `type(consistent) is bool`, and the chosen index is range-checked.
- String booleans, 0/1 used as booleans, floats, `chosen=True`, missing/extra keys, parse errors, and `consistent=False` all return exactly `暂未查到对应答案`.
- A valid model result may only select an existing approved candidate; it cannot generate or rewrite the answer.

**GREEN**

- `phase-01-final-fix-c2-green.txt`: 14 passed, including the fake-sender path and a valid exact JSON selection.
- C1/C2 focused legacy regression: 36 passed.

**Files**: `reply_engine.py`, `tests/test_phase1_final_fix.py`.

**Concern retained**: model availability affects only ranking/consistency; a judge failure deliberately becomes a no-hit reply.

### I1 — decoded JSON credential semantics

**RED**

- `phase-01-final-fix-i1-red.txt`: 11 cases ran; Unicode-escaped `X-\u0041uth` bypass and the primary+five-backup plaintext scan exposed 2 failures.

**Implementation**

- `custom_headers` and `custom_body` are rendered with a non-secret sentinel and parsed by real `json.loads`.
- Validation recursively walks decoded dictionaries and lists.
- Key recognition normalizes Unicode-decoded keys, case, camelCase, and separators.
- Sensitive positions accept only the exact `{api_key}` sentinel or the approved `Bearer {api_key}` / `Basic {api_key}` forms; extra literal prefixes/suffixes are rejected.

**GREEN**

- `phase-01-final-fix-i1-green.txt`: 11 passed (Unicode, uppercase, camelCase, separators, nested dict/list, escaping, literal smuggling, valid templates, and primary+five-backup plaintext scan).
- `phase-01-final-fix-i1-focused-regression.txt`: 18 existing custom HTTP/template tests passed.

**Files**: `settings.py`, `tests/test_phase1_final_fix.py`.

**Concern retained**: only the explicitly approved placeholder forms are accepted; callers needing a new authentication scheme must add and review a new exact form.

### I2 — JSON/DPAPI crash-recoverable transaction; M2 fixed backup count

**RED**

- `phase-01-final-fix-i2-red.txt`: the 24-point backup write/fsync/replace/delete/final-primary fault matrix failed under the previous independent rotation.
- `phase-01-final-fix-i2-real-partial-commit-red.txt`: both JSON and protected credential tests showed earlier backup slots already changed when a later slot failed.

**Implementation**

- Added shared `file_transaction.py` with a durable write-ahead journal.
- The journal snapshots the primary and all five backup slots, fsyncs staged files, atomically publishes the journal, applies the five-slot rotation and primary replacement, and writes a durable `COMMITTED` marker.
- Ordinary exceptions roll back byte-for-byte immediately. A process-style `BaseException` leaves the journal for the next load to recover automatically.
- `atomic_json.py` and `secure_store.py` both use the shared transaction implementation.
- `backup_count` is strictly fixed to five in both JSON and DPAPI paths.
- Windows file data and journal/marker files are always flushed/fsynced. Windows directory `FlushFileBuffers` error 1 (`INVALID_FUNCTION`) and 5 (`ACCESS_DENIED`) use the explicitly approved best-effort boundary; other Windows errors and all non-Windows directory fsync errors remain strict.

**GREEN**

- `phase-01-final-fix-i2-green-attempt-1.txt` is retained as failed evidence: 16/26 passed; tests initially patched the old module seam and Windows directory flush returned error 5.
- After moving fault injection to shared `file_transaction.os.replace` and applying the approved Windows directory boundary, `phase-01-final-fix-i2-green.txt`: 26 passed (24 fault points plus crash recovery/shared secure-store semantics).
- `phase-01-final-fix-i2-focused-regression-green.txt`: 42 existing atomic/backup/credential/recovery tests passed.

**Files**: `file_transaction.py`, `atomic_json.py`, `secure_store.py`, `tests/test_persistence.py`, `tests/test_phase1_final_fix.py`.

**Concern retained**: Windows cannot guarantee directory-handle flushing on filesystems/APIs that return error 1/5; durable file flushes, journal publication, committed marker, and tested automatic recovery remain in place.

### I3 — persistent send ledger and process-lifetime single instance

**RED**

- `phase-01-final-fix-i3-modules-red.txt`: `send_ledger` and `single_instance` modules were absent.
- `phase-01-final-fix-i3-red.txt`: all 8 authoritative behavior cases failed on the initial skeleton (states/restart, corruption/write failure, baseline, group failure/retry, and mutex).
- `phase-01-final-fix-i3-main-mutex-red.txt`: `main()` had no injectable process-lifetime guard and could construct the app before duplicate-instance rejection.
- `phase-01-final-fix-i3-nonwindows-mutex-red.txt`: closing a duplicate non-owner handle incorrectly released the non-Windows adapter's owner.
- `phase-01-final-fix-i3-startup-baseline-red.txt`: a visible window with unreadable startup OCR could continue monitoring without a trustworthy baseline.

**Implementation**

- Added `send_ledger.py` using `app_paths.ledger_path()` and the repaired atomic JSON transaction.
- Persisted statuses are compatible with the Phase 3 authority: `prepared`, `keypress`, `verified`, `uncertain`, `failed`, and `baseline`.
- Disk records contain only SHA-256 turn/identity/record/answer values, status, retry count, timestamp, and redacted reason.
- Ledger read/recovery/write failures raise `LedgerBlockingError`; managed mode turns off and the UI queue receives a visible blocking message. There is no in-memory idempotency fallback.
- Personal and group flows check the stable ledger key, then atomically `prepare` immediately before calling the sender. Success is conservatively recorded `keypress -> uncertain` because Phase 1 has no bubble verifier; it is never mislabeled `verified`. Sender failure becomes `failed` and remains retryable.
- Restart suppresses `prepared`, `keypress`, `verified`, `uncertain`, and `baseline`. A group message is not marked handled before the sender result.
- Startup creates personal and group baselines for the currently visible turn. If a visible window cannot be read reliably, managed mode and automatic recognition are disabled and monitoring does not start; this prevents later OCR recovery from treating an old turn as new.
- Added `single_instance.py`: Windows uses a stable `Local\WeChatStyleReplyAssistant.ManagedRuntime.v1` named mutex shared by source and EXE. `main()` acquires it before app/UI construction or formal writes and releases it in `finally` after `App.start()` returns. Imports do not acquire the mutex. The non-Windows/test adapter is injectable and ownership-correct.

**GREEN**

- `phase-01-final-fix-i3-green-attempt-1.txt`: authoritative matrix 8/8 passed.
- `phase-01-final-fix-i3-focused-regression-green.txt`: 69 existing managed/group/startup tests passed (plus 2 subtests).
- `phase-01-final-fix-i3-main-mutex-green.txt`: duplicate main lifecycle passed.
- `phase-01-final-fix-i3-nonwindows-mutex-green.txt`: Windows-fake/non-Windows adapter/main lifecycle 3/3 passed.
- `phase-01-final-fix-i3-startup-baseline-green.txt`: normal and unreadable startup baseline cases 2/2 passed.

**Files**: `send_ledger.py`, `single_instance.py`, `main.py`, `app_paths.py` (consumer), `tests/test_core.py`, `tests/test_persistence.py`, `tests/test_phase1_final_fix.py`.

**Concern retained**: without Phase 3 bubble verification, a successful keypress is deliberately `uncertain` and blocks resend. Phase 3 must reuse the ledger and promote to `verified` only after real bubble verification.

## Directly related Minor findings

### M3 — `managed_mode` type normalization

- RED `phase-01-final-fix-m3-red.txt`: an unhashable list raised raw `TypeError`.
- Implementation: check `type(managed_mode) is str` before set membership.
- GREEN `phase-01-final-fix-m3-green.txt`: invalid types uniformly raise `SettingsValidationError`.
- Files: `settings.py`, `tests/test_phase1_final_fix.py`.

### M4 — formal UI copy

- RED `phase-01-final-fix-m4-red.txt`: tooltip/help still promised model generation and fallback.
- Implementation: help now says only approved knowledge is used, the model only ranks/checks consistency, and the fixed miss reply is `暂未查到对应答案`.
- GREEN `phase-01-final-fix-m4-green.txt`; existing setting/UI focused regression also passed.
- Files: `float_ui.py`, `tests/test_phase1_final_fix.py`.

### M6 — normalized runtime logger handler marker

- RED `phase-01-final-fix-m6-red.txt`: handler marker did not use the logger's normalized path key.
- Implementation: logger identity and handler ownership share a resolve/normcase/slash-normalized/casefold path key; existing raw case/slash variants are normalized when compared.
- GREEN `phase-01-final-fix-m6-green.txt`: new case variant plus existing duplicate/concurrency tests 3/3 passed.
- Files: `runtime_logging.py`, `tests/test_phase1_final_fix.py`.

### M7 — atomic salt and visible logger-unavailable state

- RED `phase-01-final-fix-m7-red.txt`: no shared atomic writer, no typed logger error, and managed readiness continued with a corrupt salt (3/3 failed).
- Implementation: `.runtime-salt` uses the shared atomic fsynced transaction and recovery; invalid/recovery/write/init failures raise `RuntimeLoggerUnavailableError`. Managed readiness checks the diagnostic foundation and, on failure, sets managed mode off and queues a visible `诊断日志安全基础不可用...已关闭托管自动发送` message.
- GREEN `phase-01-final-fix-m7-green.txt`: 8/8 new and existing runtime logger/diagnostic tests passed.
- Files: `runtime_logging.py`, `main.py`, `tests/test_phase1_final_fix.py`.

## Full verification and evidence

### Focused tests

- New final-fix file before the last baseline addition: 74/74 passed (`phase-01-final-fix-new-tests-green-attempt-2.txt`).
- I3 startup-baseline final addition: 2/2 passed.
- Existing I3 managed/group/startup focused regression: 69/69 passed plus 2 subtests.
- Minor settings/UI/logging focused regression: 12/12 passed.

### Full suite and JUnit

- First full attempt is retained at `phase-01-final-fix-full-pytest.txt`: 502 passed, 7 failed, 74 warnings, 6 subtests. Six failures were stale free-fallback assertions and one was the diagnostic enum expected set. They were migrated to the confirmed knowledge-only contract and the two ledger diagnostic states were added to the positive audit allowlist.
- Final command output: `phase-01-final-fix-full-pytest-final.txt`.
- Final terminal result: **510 passed, 0 failed, 74 warnings, 6 subtests passed in 33.47s**.
- Warnings are the pre-existing Pillow `Image.getdata` deprecations only: 72 at `conversation_list.py:425` and 2 at `conversation_list.py:337`.
- Final JUnit: `phase-01-final-fix.xml`; root suite `tests=516`, `failures=0`, `errors=0`, `skipped=0`, `time=32.848`; SHA-256 `ec4b075c861bbc463024999cec094c1913fb6f6e237a38f873745b416f1d0282`.
- The first failed JUnit was preserved as `phase-01-final-fix-full-attempt-1-failed.xml`.

### Compile

- `phase-01-final-fix-compileall.txt`: `COMPILEALL_EXIT=0`.

### Formal AppData

- `phase-01-final-fix-formal-appdata-before.json`, `...-after.json`, and `...-current.json` each contain 32 files.
- Before/after/current size, SHA-256, and mtime match for every file: after mismatch `0`; current mismatch `0`.
- All three manifest artifact SHA-256 values are `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2`.
- Recalculation output: `phase-01-final-fix-formal-appdata-comparison.txt`.

### Current source/test manifest

- `phase-01-final-fix-files.sha256.json` includes 16 Phase 1/final-fix production and test files, including all new transaction/ledger/mutex modules and the new final-fix tests.
- Manifest SHA-256: `88461a5c9a51ada4692bfd69820011535ae8eaa054c560ba0eac73a78a1be153`.
- Independent current recalculation mismatch: `0` (`phase-01-final-fix-manifest-verification.txt`).

## Files changed or added

Production changed: `atomic_json.py`, `secure_store.py`, `settings.py`, `reply_engine.py`, `runtime_logging.py`, `float_ui.py`, `main.py`.  
Production added: `file_transaction.py`, `send_ledger.py`, `single_instance.py`.  
Tests changed: `tests/test_core.py`, `tests/test_persistence.py`.  
Tests added: `tests/test_phase1_final_fix.py`.

## Remaining status and deferred Minor items

- Remaining test failures: **0**.
- C1/C2/I1/I2/I3 and M2/M3/M4/M6/M7 are implemented with RED/GREEN evidence.
- **M1 remains deferred** to the Phase 3 state machine: complete `observe`/`controlled`/`full` runtime semantics. This change does not add any new sending authority; non-`full` remains fail closed.
- **M5 remains deferred** to the Phase 2 knowledge schema: deterministic metadata merging at the 3001-record dedupe boundary.
- The result must now receive root-level targeted re-review. This implementation report intentionally does not issue the final phase PASS decision.
