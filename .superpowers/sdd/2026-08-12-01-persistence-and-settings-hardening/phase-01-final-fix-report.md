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

---

# Fix Round 2 — R1–R5 targeted closure

Date: 2026-08-12  
Scope: only R1–R5 in `phase-01-final-rereview.md`. This appendix hands evidence to root-level targeted re-review and does **not** declare final phase PASS.

## Safety baseline

- Non-Git pre-edit snapshot: `phase-01-final-fix2-before`, 18 files; manifest `phase-01-final-fix2-before-files.sha256.json`, SHA-256 `39bb1bd99bbcbd81f956a2cde953a62aafe79455245e5833f23695c6797d4a5c`.
- Formal AppData before: 32 files; `phase-01-final-fix2-formal-appdata-before.json`, SHA-256 `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2`.
- All pytest runs used `tests/conftest.py` isolation, fake senders, and injected windows/captures. No real sender, WeChat/WeCom automation, network, or E2E was run.

## R1 Critical — app-wide serialized send and strict group live guard

**RED**: `phase-01-final-fix2-r1-red.txt` selected 9 cases: 8 failed and the normal-group control passed. Queue-then-chat-switch, wrong HWND, unknown/changed identity, empty capture, changed turn, group/group overlap, and group/personal overlap exposed the missing boundary. Failed GREEN/fixture attempts remain preserved as `...r1-green-attempt-1.txt`, `...attempt-2.txt`, and both old-regression attempts.

**Implementation**: `main.py` owns one `send_operation_lock` shared by personal and all group sends. A queued group turn carries explicit chat identity and a SHA-256 fingerprint of normalized sender/current customer turn. Under the lock, immediately before persistence/send, the group path requires the current foreground window, exact nonzero HWND/platform, fresh title/OCR, exact identity/sender/turn, then performs `ledger.prepare -> sender -> keypress+uncertain/failed`. Uncertainty exits with sender=0 and no misleading terminal ledger. Personal retains its existing preflight/fallback but places preflight, readiness, ledger, sender, and terminal update under the same lock.

**GREEN**: `phase-01-final-fix2-r1-green.txt` 9/9 passed. `phase-01-final-fix2-r1-focused-regression.txt` 25/25 passed.  
**Files**: `main.py`, `tests/test_phase1_final_fix.py`, `tests/test_core.py`, `tests/test_persistence.py`.  
**Concern**: without a Phase 3 bubble verifier, successful keypress remains conservatively `uncertain`; unreliable fresh OCR blocks send.

## R2 Important — fail-closed decoded custom JSON schema

**RED**: `phase-01-final-fix2-r2-red.txt` had 7 failures/1 control pass for `xauthorizationextra`, `myapikeyvalue`, `authorization2`, nested/list variants, and primary+five literal scanning. Required self-attack `phase-01-final-fix2-r2-self-attack-red.txt` failed 4/4 for `fooauthorizationpayload`, `tenantapikeyblob`, `vendorclientsecretdata`, and `bearertokenmaterial`, proving a finite prefix/suffix grammar remained bypassable.

**Implementation**: the temporary finite-wrapper approach was discarded. `settings.py` now uses a decoded positive schema. Sensitive fields accept only exact approved `{api_key}` sentinel values (including approved Bearer/Basic forms). `custom_headers` permits reviewed static header names or approved credential placeholders. `custom_body` permits reviewed business string fields; every unknown string-bearing nested/list key fails closed, while unknown numeric/boolean/container fields remain recursively inspected. Validation still uses sentinel rendering plus real `json.loads`.

**GREEN**: `phase-01-final-fix2-r2-green.txt` 19 passed; `phase-01-final-fix2-r2-self-attack-green-attempt-1.txt` 42 passed, including Unicode/camel/nested, primary+five scan, placeholders, and custom HTTP request/round-trip.  
**Files**: `settings.py`, `tests/test_phase1_final_fix.py`.  
**Concern**: a provider needing a new literal string field/static header must add it to the reviewed allowlist; unfamiliar fields fail closed.

## R3 Important — snapshots inside the transaction lock

**RED**: `phase-01-final-fix2-r3-red.txt` failed JSON and secure-store two-writer Event tests; writer 2 entered snapshot collection before writer 1 committed.

**Implementation**: `file_transaction.transactional_replace_with_backups` accepts a mutually exclusive `snapshot_loader`. One normalized per-path `RLock` now spans recover -> callback collection/validation -> journal -> backup/primary replace -> `COMMITTED`. `atomic_json` and `CredentialStore` pass loaders into that boundary and do not pre-recover/pre-sample in callers. Explicit snapshots remain supported for atomic salt under the same transaction boundary.

**GREEN**: `phase-01-final-fix2-r3-green-concurrency-and-failure.txt` 4/4 passed: JSON and protected-store writer 2 produced `bak.1 == writer1 committed primary`; injected writer-2 failure preserved writer-1 commit. Existing `phase-01-final-fix2-r3-green-fault-matrix.txt` remained 26/26 passed.  
**Files**: `file_transaction.py`, `atomic_json.py`, `secure_store.py`, `tests/test_phase1_final_fix.py`.  
**Concern**: the already approved Windows directory-flush error-1/error-5 best-effort boundary remains; file/journal/marker fsync and recovery stay strict.

## R4 Important — raw ledger inputs always hash

**RED**: `phase-01-final-fix2-r4-red.txt` showed exactly 64-hex raw record/source and answer values persisted verbatim because their shape was trusted as pre-hashed.

**Implementation**: digest-shape inference was removed. `SendLedger.prepare` is raw-only and unconditionally SHA-256 hashes turn, identity, record/source, and answer. The compatibility parameter name `answer_hash` is explicitly documented as raw; no pre-hashed API exists and `main.py` uses only this raw API.

**GREEN**: `phase-01-final-fix2-r4-green.txt` 5/5 passed. Four exact 64-hex raw values do not occur in ledger primary/backups; restart status/contains/dedupe remains correct.  
**Files**: `send_ledger.py`, `tests/test_phase1_final_fix.py`.  
**Concern**: any future pre-hashed API requires a separate explicit name/type and review.

## R5 Minor — managed tooltip

**RED**: `phase-01-final-fix2-r5-red.txt` showed `TEXT["managed_tip"]` still promised three generated candidates.  
**Implementation**: the tooltip now states approved knowledge only, model ranking/consistency only, no generation/rewrite, and exact miss reply `暂未查到对应答案`.  
**GREEN**: `phase-01-final-fix2-r5-green.txt` 2/2 passed.  
**Files**: `float_ui.py`, `tests/test_phase1_final_fix.py`.

## Verification and evidence

- New focused file: `phase-01-final-fix2-focused-all-attempt-1.txt`, 103 passed.
- Related core/persistence/group: `phase-01-final-fix2-related-regression-attempt-1.txt`, 413 passed plus 6 subtests, 0 failed.
- `phase-01-final-fix2-pytest-full-attempt-1.txt` records an execution-infrastructure 1-second timeout before a valid result.
- First valid full run: `phase-01-final-fix2-pytest-full-attempt-2.txt`, **537 passed, 0 failed, 74 warnings, 6 subtests in 34.06s**.
- Final JUnit run: `phase-01-final-fix2-pytest-full-junit.txt`, **537 passed, 0 failed, 74 warnings, 6 subtests in 34.15s**. All 74 warnings are existing Pillow `Image.Image.getdata` deprecations (72 at `conversation_list.py:425`, 2 at `:337`).
- JUnit `phase-01-final-fix2.xml`: tests=543, failures=0, errors=0, skipped=0, time=33.855; SHA-256 `08ee02c89fc638f48c2b7fb15a49a74e6cc5ce4b8cb8d3ecc2fb51eafaac94e9`.
- `phase-01-final-fix2-compileall.txt`: `COMPILEALL_EXIT=0`; SHA-256 `9b8c838578fe273a1d7f1db79f70c1d1f66a9c8bccd09f780e4bddaa326dfded`.
- `phase-01-final-fix2-files.sha256.json`: 16 current production/test files; SHA-256 `4801aa7534a70288b3d9e33ba96c9d18ba02beac9f21c84f24736125ac3faa58`; independent current mismatch=0.

### Formal AppData final evidence

- Final before/after/current manifests each contain 32 files; SHA-256/size/mtime mismatch=0. All three artifact SHA-256 values are `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2` (`phase-01-final-fix2-formal-appdata-comparison.txt`).
- During evidence generation, two post-baseline generated additions were detected: `logs/.runtime-salt` (32 bytes) and an empty `logs/runtime.log`, both created 2026-08-12 01:19:42Z and absent from the before manifest. They contained no pre-existing user content and were not silently deleted: after exact root/path validation, they were moved recoverably to `phase-01-final-fix2-formal-appdata-additions-quarantine/`, then after/current were recalculated.
- The creator PID/command was not captured, so attribution is explicitly **unknown**. Their unique runtime-logger shape and timestamp between R1 GREEN attempts make test/standalone-probe pollution likely, but not proven. The first observed state was 34 files with two mismatches; the initial 34-file after/current manifests were overwritten by the later 32-file recalculation, which is an evidence gap. The exact bytes and captured original metadata are preserved in `phase-01-final-fix2-formal-appdata-transient-additions.json`. Therefore `mismatch=0` describes the state only **after recoverable quarantine** and must not be represented as “formal AppData was never touched.”

## Round 2 changed files and remaining status

Production: `main.py`, `settings.py`, `file_transaction.py`, `atomic_json.py`, `secure_store.py`, `send_ledger.py`, `float_ui.py`.  
Tests: `tests/test_phase1_final_fix.py`, `tests/test_core.py`, `tests/test_persistence.py`.  
Report: this appended Round 2 section.

- Remaining test failures: **0**.
- M1 remains deferred to the Phase 3 state machine for complete `observe`/`controlled`/`full` semantics. Round 2 adds no authority outside `full`; non-`full` remains fail closed.
- M5 remains deferred to Phase 2 for deterministic 3001-record metadata merging.
- Root-level targeted re-review remains required; this report does not issue final phase PASS.

---

# Gate Fix — rereview2 two-Important closure

Date: 2026-08-12  
Scope: only the two Important findings in `phase-01-final-rereview2.md`. This appendix is evidence for root-level review and does **not** declare final phase PASS.

## Important 1 — anonymous list string leaves fail closed

**RED**: `phase-01-final-gate-r2-anonymous-list-red.txt` selected seven cases. Five failed and two controls passed. The failing cases proved that anonymous string leaves in a direct body list, a multi-level list, a mixed dict/list, an approved-placeholder sibling plus an anonymous leaf, and the primary-plus-five-backup persistence scan could bypass the positive schema. The header-list case already failed closed, and the approved `input` string-list control already passed. Artifact SHA-256: `acd07756e10c453f554e962c17ec786adaef6d14319ed24bca73444a01ca2a15`.

**Implementation**: `settings.py` now propagates both approved string context and unknown-container context through decoded dict/list recursion. Anonymous string leaves are rejected unless they remain beneath an explicitly approved body business field/container. Unknown containers cannot acquire approval merely by nesting a list. Header values remain scalar strings and header lists fail closed. Credential values still accept only the approved exact `{api_key}` forms.

**GREEN**: `phase-01-final-gate-r2-anonymous-list-green.txt` passed all seven cases; SHA-256 `b4c29ff019c42447cb3674c24ab6f14443dc70dae416a916c7b1ac59f0b43b7a`. `phase-01-final-gate-r2-existing42-and-new-focused.txt` passed 49/49 (the existing 42 plus seven gate cases), SHA-256 `d84c20f9d13bef29e8e1cd58842e80a05389509d602cf532175287f04acff05f`. `phase-01-final-gate-settings-regression.txt` passed 145 tests plus two subtests, SHA-256 `463aeed236ab9b34afb113c7d9ead4e8c994ab3aa13f2671fd5c30427e025a0c`. Approved placeholders, the reviewed custom HTTP request/round-trip templates, and primary-plus-five-backup literal scanning remained covered.

**Files**: `settings.py`, `tests/test_phase1_final_fix.py`.

**Concern**: the positive schema deliberately blocks unfamiliar string-bearing fields and anonymous list strings. A provider needing another literal-string field or list container requires an explicit reviewed allowlist addition.

## Important 2 — controlled formal AppData process/evidence gate

No file in the formal AppData root was moved, deleted, restored, or cleaned during this Gate Fix. The existing quarantine directory and `phase-01-final-fix2-formal-appdata-transient-additions.json` were preserved byte-for-byte; the incident manifest remains SHA-256 `d9df0f83c584744a601d62fe8b0c1f0ed4cfa290ea5265247aee1a5a42c32a1c`.

The first append-only wrapper attempt is preserved as `phase-01-final-gate-wrapper.ps1` and `phase-01-final-gate-wrapper-output.txt`. Windows PowerShell 5 decoded its UTF-8-without-BOM hard-coded Chinese workspace path incorrectly, so it stopped while resolving the formal root and **before any Python process or gate-before manifest was created**. Its unique temporary root was left intact. Attempt 2, `phase-01-final-gate-wrapper-attempt-2.ps1`, derives the workspace from `$PSScriptRoot`, and its complete raw output is preserved as `phase-01-final-gate-wrapper-output-attempt-2.txt`, SHA-256 `d8ac3fda337af2704e43af10eb6ef92ac49ad9324eb2b7bd358a939de701092b`.

Attempt 2 created and retained the isolated root `C:\Users\14913\AppData\Local\Temp\wechat-assistant-formal-gate-72c2965d4ee94a76bf36eb080a905755`, set `WECHAT_ASSISTANT_DATA_DIR` before starting Python, and ran both pytest and compileall in that same controlled PowerShell boundary.

### Append-only formal manifests

- `phase-01-final-gate-before.json`: count 32, tree SHA-256 `0c5f604f74bcb9e2b5cf0c4acf787c9b6eab2eaa031870e34884fdc7b19c58de`, artifact SHA-256 `cd4d7dd1b933d4b9d567bfb5241077a6149f79c7917b94d32d31821603d712ea`.
- `phase-01-final-gate-after.json`: count 32, the same tree SHA-256, before/after path-size-hash-mtime mismatch 0, artifact SHA-256 `8e5d390af9f1d5e2f64a08b5b5fd67844ba7f3682dce60b26dc6e61eaf8adca5`.
- `phase-01-final-gate-current.json`: count 32, the same tree SHA-256, before/current mismatch 0, artifact SHA-256 `8a5028df1e6af7eff3f239c7b62f559bb2b27596d24f363d22b4d381ca8be200`.

Thus the controlled Gate Fix process added no new formal-root item and changed no captured formal-root item. This evidence is deliberately limited to the controlled gate. It does **not** negate or rewrite the earlier 34-item incident: `.runtime-salt` and `runtime.log` were historically observed after the 32-item baseline, their creator remains unknown, and their previously quarantined bytes remain preserved. This report must not be read as claiming the formal directory was never touched.

## Gate verification

- Full controlled command: `pytest tests -v --junitxml=...phase-01-final-gate.xml`; result **544 passed, 0 failed, 74 warnings, 6 subtests passed in 35.15s**, pytest exit 0. All 74 warnings are the existing Pillow `Image.Image.getdata` deprecations (72 at `conversation_list.py:425`, 2 at `:337`).
- JUnit `phase-01-final-gate.xml`: tests=550, failures=0, errors=0, skipped=0, time=35.039; SHA-256 `4cf1e49f0323490f3929d9a891f4911ba6e0e3e2e02cf90ce1fc9dc06879afeb`.
- Controlled `compileall`: exit 0. `phase-01-final-gate-compileall.txt` SHA-256 `cec02bf8b7c368ecc16eac1599bf062351f403f305ee24aaa8f9f6e02e0169ff`.
- Final current source/test manifest `phase-01-final-gate-files.sha256.json`: 16 files, SHA-256 `d4a7689c53976b44567fab44225ca103a28ecfbb7463f8ae7b1e2b442702c6c2`; independent current recalculation mismatch 0.

## Gate Fix changed files and remaining status

Production: `settings.py`.  
Tests: `tests/test_phase1_final_fix.py`.  
Evidence/report: append-only Gate Fix artifacts and this appended section.

- Remaining test failures: **0** in the controlled full suite.
- The two rereview2 Important findings have RED/GREEN and process-gate evidence for root-level review.
- M1 remains deferred to Phase 3 for the complete runtime mode state machine; this Gate Fix adds no sending authority.
- M5 remains deferred to Phase 2 for deterministic 3001-record metadata merging.
- The historical formal-AppData 34-item incident and its attribution gap remain explicit concerns.
- Root-level targeted review remains required; this report does not issue final phase PASS.
