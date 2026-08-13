# Phase 1 Final-Fix Targeted Re-review

Date: 2026-08-12  
Scope: read-only review of the Phase 1 final fix, its tests, and its evidence artifacts. No WeChat/WeCom process, real sender, E2E path, or network was started. The only new file is this report.

## Assessment

**Needs fixes.** Current count: **1 Critical, 3 Important, 3 Minor**. C1 and C2 are closed, but I1, I2, and I3 are not fully closed. In particular, the real group-send path lacks a last-moment chat/window guard and a process-wide send serialization boundary; the current code can therefore send into the wrong active chat under ordinary timing races.

## Strengths

- `reply_engine.py:98-103,166-194` now gives `knowledge_only` a true early branch. It returns only a selected local knowledge answer or the exact `暂未查到对应答案`; `_fallback`, rule replies, free generation, and search are unreachable from this mode.
- `reply_engine.py:494-507` parses the judgement as JSON and enforces exact root keys, exact Python types, and a valid candidate index. The model can only select an original candidate.
- The single-thread transaction design has materially improved: five slots are fixed, the old six candidates are journaled before mutation, `COMMITTED` follows primary replacement, and ordinary `Exception` failures trigger restoration (`file_transaction.py:195-271`). The fault matrix verifies every backup slot and the primary in the tested single-writer cases.
- The ledger has strict root/entry keys, typed states, durable-before-send `prepared`, conservative `uncertain` after keypress, retryable `failed`, and in-object locking (`send_ledger.py:61-86,122-225`). The Windows named mutex is import-safe and is acquired before app/UI construction and released for the process lifetime.
- Runtime logger construction normalizes handler paths and converts salt/logger initialization failures into a typed blocking error (`runtime_logging.py:182-214`).

## Original finding closure

| Original | Status | Direct result |
|---|---|---|
| C1: `knowledge_only` could fall through to fallback/free/search | **ADDRESSED** | `reply_engine.py:98-103,166-194` is a complete early-return path. Personal and group generation both use the same engine contract. No-answer text is exactly `暂未查到对应答案` (`reply_engine.py:38-39`). |
| C2: permissive judgement schema/model-generated content | **ADDRESSED** | `reply_engine.py:494-507` requires exactly `chosen` and `consistent`; `chosen` must be `type(...) is int`, `consistent` must be `type(...) is bool`, and index must be in range. It returns a candidate index, never model-authored reply text. |
| I1: credential smuggling in custom JSON templates | **NOT ADDRESSED** | Recursive decoded-JSON validation is present, but all-lowercase concatenated prefixes/suffixes evade `_is_sensitive_credential_key`; see R2. |
| I2: primary plus five backups were not one recoverable transaction | **NOT ADDRESSED** | Crash/slot recovery is addressed for one writer and `backup_count` is fixed at five, but snapshot collection remains outside the per-path transaction lock for both JSON and DPAPI; see R3. |
| I3: persistent idempotency, startup baseline, and single-instance/send safety | **NOT ADDRESSED** | Ledger state handling, startup baseline, and process mutex are substantially addressed. The real group send still lacks the required pre-send chat guard and common sender serialization (R1), and a 64-hex plaintext can be stored verbatim (R4). |
| M2: public `backup_count` disagreed with five-slot recovery | **ADDRESSED** | `atomic_json.py:117-121` and `secure_store.py:49-56` accept exactly five. |
| M3: unhashable `managed_mode` raised raw `TypeError` | **ADDRESSED** | `settings.py:256-257` checks exact string type before membership. |
| M4: obsolete formal-mode help/tooltip text | **NOT ADDRESSED** | Main source/help copy is corrected, but `float_ui.py:44` still promises three candidates; see R5. |
| M6: Windows handler path case mismatch | **ADDRESSED** | `runtime_logging.py:183-196,205` uses the same normalized/casefolded key for logger ownership and comparison. |
| M7: damaged/missing salt silently disabled logs | **ADDRESSED** | Salt/logger construction fails with `RuntimeLoggerUnavailableError`; managed-send readiness surfaces the block and turns managed mode off. The previous silent managed-send continuation was not found. |

## New/current findings

### R1 — Critical — Group managed sends have neither a last-moment chat guard nor a shared sender lock

**Evidence:** `main.py:673-739` starts a new daemon thread for every group reply. Inside the worker the only repeated gate is `_managed_reply_readiness_allows_send()` (`main.py:695-710`); it then calls `self.sender.send(wechat, text)` directly (`main.py:712`). It does not reacquire the live window, OCR the current turn, compare chat identity/content, or enter any shared send lock. By contrast, the personal worker performs `_managed_live_send_window` and `_managed_send_preflight_allows` at `main.py:1478-1496`, but its `managed_send_inflight` flag (`main.py:1472-1474,1549-1554`) is not used by group sends.

**Reproduction condition:** (a) queue a group reply, switch the selected conversation in the same WeCom top-level window before the worker reaches line 712; the stale window handle remains valid and the sender operates on the newly selected chat; or (b) queue two group replies, or one group and one personal reply, close together. Distinct ledger keys can both become `prepared`, after which the daemon threads concurrently operate the shared window/clipboard sender. The current tests (`tests/test_phase1_final_fix.py:753-788`) use one fake group sender and do not switch the chat or overlap send workers.

**Impact:** a managed reply can be pasted/sent to the wrong person or group. This is an external side effect and violates fail-closed sending, so it is Critical.

**Fix direction:** introduce one app-wide send coordinator/lock shared by personal and group paths. While holding it, immediately before `ledger.prepare` and `sender.send`, reacquire the live window and validate HWND/platform/chat identity plus a fresh OCR turn against the queued turn. Any capture/identity/content ambiguity must abort without keypress. Keep the ledger transition and sender call inside the same serialized guarded sequence, then add deterministic overlap and chat-switch tests.

### R2 — Important — Lowercase sensitive-key prefixes/suffixes still bypass template credential validation

**Evidence:** `_is_sensitive_credential_key` tokenizes only separators/camel case, then accepts a compact form only on exact finite-set equality (`settings.py:141-145`). A pure in-memory probe showed all of the following decoded JSON templates were accepted: `{"xauthorizationextra":"literal-secret"}`, `{"myapikeyvalue":"literal-secret"}`, and `{"authorization2":"literal-secret"}`. Existing adversarial cases cover Unicode, uppercase, camelCase, separators, nesting, and value prefixes/suffixes, but not lowercase concatenated key prefixes/suffixes (`tests/test_phase1_final_fix.py:244-315`).

**Impact:** a literal API token/password can be accepted as part of real request headers/body and then persist in `settings.json` plus five backups. The intended invariant is that settings artifacts contain placeholders only.

**Fix direction:** validate key names by a documented canonical-key grammar that rejects sensitive stems at safe boundaries and in concatenated prefix/suffix forms; alternatively use explicit allowlisted schemas for custom headers/body and reject ambiguous credential-bearing fields. Add decoded nested/list tests for lowercase prefix/suffix variants and verify the literal never reaches primary or five backups.

### R3 — Important — JSON and DPAPI snapshot acquisition occurs outside the per-path transaction lock

**Evidence:** `atomic_json.atomic_write_json` runs recovery and evaluates `_valid_snapshots(...)` before calling `atomic_write_bytes` (`atomic_json.py:126-127`); `CredentialStore.save` likewise gathers snapshots before recovery/transaction (`secure_store.py:119-122`). The per-path `RLock` is acquired only inside `transactional_replace_with_backups` (`file_transaction.py:212-214`). A no-disk, two-thread barrier probe forced both writers to pause after sampling: the two serialized commits both received `['OLD']`, proving the later commit did not include the earlier successful version in its backup history.

**Production reachability:** memory persistence is invoked from the recognition/training path (`main.py:865-866`) and from the independent manual-send worker (`main.py:1629-1643`), and UI/import/edit callbacks also save the same memory/settings stores. These are separate callbacks/background threads. The same ordering defect is shared by DPAPI credentials even if normal credential saves are less frequent.

**Impact:** simultaneous writers can serialize their replacements yet rotate a stale snapshot set. A successfully committed intermediate version is absent from the five-slot recovery chain, breaking the promised recoverable history and creating formal-data loss after a later corruption/failure. The existing slot/crash matrix is single-writer and cannot establish concurrency correctness.

**Fix direction:** make `recover -> collect/validate snapshots -> create journal -> replace -> COMMITTED` one per-path critical section. The smallest design is to expose/use the same path lock around the whole caller sequence, or pass a snapshot-loader callback into `transactional_replace_with_backups` and invoke it only after line 212 acquires the lock. Add two-writer barrier tests for JSON and secure-store bytes and assert the first committed version becomes the second transaction's `.bak.1`.

### R4 — Important — A plaintext answer that looks like a SHA-256 digest is persisted verbatim

**Evidence:** `_hash_or_preserve_digest` returns any 64-hex input unchanged (`send_ledger.py:45-49`). `prepare` applies it to `record_id` and `answer_hash` (`send_ledger.py:150-169`), while both real call sites pass raw `engine.last_source`/reply text (`main.py:700-705,1500-1505`). A pure in-memory probe confirmed `_hash_or_preserve_digest('a' * 64) == 'a' * 64`.

**Impact:** a reply that is a 64-character hexadecimal token, order identifier, key, or other private content is written to the ledger and its backups in plaintext, violating the hashes-only ledger privacy contract.

**Fix direction:** raw-value parameters must always be hashed. If a prehashed API is required, make it an explicitly typed/separate method and validate callers; do not infer digest-vs-plaintext from content shape. Add a persistence test using an exact 64-hex chat answer.

### R5 — Minor — One visible managed-mode tooltip still describes obsolete three-candidate behavior

`float_ui.py:44` says managed mode generates three candidates. Formal `knowledge_only` can return a single selected knowledge answer or the fixed no-answer response. Update this remaining tooltip and extend `test_formal_source_help_states_knowledge_only_contract` to cover `TEXT["managed_tip"]` (current assertions at `tests/test_phase1_final_fix.py:799-808` omit it).

## Evidence verification

- **Current 16-file manifest:** independently recalculated all entries in `artifacts/formal-hardening/2026-08-12/phase-01-final-fix-files.sha256.json`; path, size, SHA-256, and UTC mtime mismatches: **0**. Manifest artifact SHA-256: `88461a5c9a51ada4692bfd69820011535ae8eaa054c560ba0eac73a78a1be153`.
- **JUnit:** parsed `artifacts/formal-hardening/2026-08-12/phase-01-final-fix.xml`; its sole pytest suite reports **516 tests, 0 failures, 0 errors, 0 skipped, time 32.848s**. Artifact SHA-256 independently matches the fix report: `ec4b075c861bbc463024999cec094c1913fb6f6e237a38f873745b416f1d0282`. The suite was not rerun because this re-review permitted only the report to be written; the artifact proves that recorded run, not the missing adversarial contracts above.
- **Formal AppData artifacts:** before, after, and current manifests each contain **32 files**, are structurally identical, and each has SHA-256 `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2`. A fresh read-only enumeration of `C:\Users\14913\AppData\Roaming\WeChatStyleReplyAssistant` found 32 files; keyed comparison of every relative path, size, SHA-256, and UTC mtime against the current manifest produced **0 mismatches**. (Raw list order differed under the independent casefold sort, so equality was correctly evaluated by relative path.)
- **Adversarial probes:** executed with `python -B`, monkeypatched transaction functions, and a non-existent probe pathname; they performed no filesystem writes. They reproduced R2, R3, and R4 as described above.
- No production code/test/artifact was modified, no network was used, and no sender or WeChat/WeCom process was started.

## Remaining minor triage

- **M1 — remains Minor:** observe/controlled are still accepted compatibility labels but normalize to the same fail-closed managed behavior. No new authorization bypass was found.
- **M5 — remains Minor:** retained metadata merging for deduplicated memory entries still depends on arrival order at the boundary. No new over-3000 loss or managed-send authorization consequence was found.
- **M4/R5 — remains Minor:** the stale three-candidate tooltip is user-visible but does not change runtime sending behavior.

The current Minor count is therefore **3** (M1, M5, and residual M4). M2, M3, M6, and M7 are closed.
