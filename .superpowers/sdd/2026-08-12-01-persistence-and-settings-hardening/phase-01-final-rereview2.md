# Phase 1 Final Fix Round 2 — Targeted Re-review

Date: 2026-08-12  
Scope: read-only review of Round 2 production code, tests, and preserved artifacts. No WeChat/WeCom process, sender, E2E path, network, or standalone application/runtime-logger probe was started. This report is the only file written by this review.

## Assessment

**Needs fixes.** Final count: **0 Critical, 2 Important, 2 Minor**.

R1, R3, R4, and R5 are addressed. R2 is not fully addressed because an unknown container can carry a literal string in a list without the positive schema ever associating the string with the unknown key. Separately, Round 2 did write two runtime-logger files into formal AppData, overwrite the first 34-file manifests, and later quarantine the additions. That is an Important process/evidence failure. It is not evidence that the current `app_paths`/pytest isolation code itself misroutes an isolated test, but the missing creator provenance prevents such a code defect from being ruled out by the artifacts.

## R1–R5 closure table

| Finding | Status | Direct verification |
|---|---|---|
| R1 — group chat-switch/concurrent-send safety | **ADDRESSED** | `main.py:254` creates one `send_operation_lock` per app. Every group worker and the personal worker use that same instance (`main.py:712,1561`). Production group queuing obtains identity from the actual captured window title and derives sender/turn from the actual OCR context (`main.py:620-677`). Under the lock, group send requires the current foreground window, nonzero exact HWND, exact platform, fresh title/OCR, identity, sender, and normalized-turn fingerprint (`main.py:715-725,772-819,2166-2199`). A failed guard occurs before `ledger.prepare`, so sender calls and terminal ledger entries remain zero. `prepare -> sender -> keypress/uncertain or failed` stays inside the lock (`main.py:726-766`). Deterministic tests cover changed chat/HWND/identity/turn, empty capture, group/group overlap, group/personal overlap, and a normal control (`tests/test_phase1_final_fix.py:1198-1392`). The fakes inject observation inputs but do not bypass the production guard function or lock. |
| R2 — fail-closed decoded custom JSON schema | **NOT ADDRESSED** | Dict string values now use a positive header/body allowlist, and known credential positions still require exact approved placeholders (`settings.py:154-176`). However, a string reached through a list is validated without its parent key and only scanned for `Bearer`/`Basic` (`settings.py:178-185`). See I1 below. |
| R3 — snapshot collection within one transaction lock | **ADDRESSED** | A single normalized per-path `RLock` covers recovery, `snapshot_loader`, journal creation, replacement, and commit (`file_transaction.py:29-32,196-276`). JSON passes its loader directly at `atomic_json.py:120-137`; DPAPI passes its loader at `secure_store.py:109-124`. Neither caller pre-recovers or pre-samples. The two-writer tests verify writer 1 becomes writer 2's `.bak.1`, and failed writer 2 preserves writer 1 (`tests/test_phase1_final_fix.py:651-848`). The existing 26-case slot/primary/crash matrix remains represented in the final JUnit. |
| R4 — raw 64-hex ledger values persisted verbatim | **ADDRESSED** | Shape inference is gone. `SendLedger.prepare` hashes turn, identity, record/source, and answer unconditionally (`send_ledger.py:143-174`). Main's turn/identity helpers already return stable digests, and the raw-only ledger hashes those inputs again; no prehashed/trusted bypass exists. Exact 64-hex values are absent from primary/backups and restart dedupe remains stable in `tests/test_phase1_final_fix.py:899-931`. |
| R5 — obsolete managed tooltip | **ADDRESSED** | `float_ui.py:44` now states knowledge-only, ranking/consistency-only, no generation/rewrite, and the exact no-answer reply. The tooltip itself is asserted at `tests/test_phase1_final_fix.py:1415-1421`. |

## New/current findings

### I1 — Important — Anonymous list strings bypass the custom-body positive schema

**File/lines:** `settings.py:154-185`, especially `178-185`.

**Reproduction:** set `custom_body` to `{"outer":["literal-secret"]}` (equally, an unknown nested container such as `{"unknown":{"items":["literal-secret"]}}`). The dict branch sees `outer` with a list, so it does not apply the unknown string-bearing-key rejection at lines 164-173. The list branch drops key context. The leaf string contains neither `Bearer` nor `Basic`, so lines 182-185 accept it. This is a static call-path reproduction; no write-path probe was run.

The Round 2 self-attacks at `tests/test_phase1_final_fix.py:441-466` all retain a dict key at the ultimate string leaf. They do not cover a bare string element. The positive control at lines 427-438 covers ordinary scalar business fields but also does not exercise the anonymous-list boundary.

**Impact:** changing an arbitrary field into an unknown container plus list permits a literal credential to pass settings validation and be stored in `settings.json` plus five backups. This violates the stated fail-closed rule for unknown string-bearing nested/list content. It also means R2's new tests establish only keyed-leaf safety, not recursive value safety.

**Fix direction:** carry schema context through recursion. Unknown containers may contain only recursively credential-free non-string scalars; if any descendant is a string, reject the whole unknown field. Permit string/list content only beneath explicitly reviewed container fields with a field-specific schema (including approved placeholder forms). Add direct and deeply nested anonymous-list cases and scan primary plus all five backups after rejection. Keep current custom HTTP positive round-trip tests so the reviewed provider contract is not accidentally removed.

### I2 — Important — Formal AppData was modified and the first contaminated manifests were overwritten

**Evidence:** the append-only incident manifest records a 32-file baseline, a 34-file transient state, and two additions at 2026-08-12 01:19:42Z:

- `logs/.runtime-salt`, 32 bytes, SHA-256 `e83b2ae3395e29d7ced86f0ad681c547f4bd9f6dcc21ff133e2abe212f13788f`;
- `logs/runtime.log`, 0 bytes, SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Both files were later moved recoverably to `artifacts/formal-hardening/2026-08-12/phase-01-final-fix2-formal-appdata-additions-quarantine/`. This review independently matched both quarantined files' size, SHA-256, and captured mtime against `phase-01-final-fix2-formal-appdata-transient-additions.json` (0 mismatches). The current formal root has returned to 32 files and matches the baseline by path, size, hash, and mtime.

**Evidence gap and classification:** the initial 34-file `after`/`current` manifests were overwritten by a later 32-file recalculation. Creator PID and command were not captured. The file shape suggests runtime logger initialization, and the timestamp falls inside Round 2 work, but test/probe attribution is only an inference. `tests/conftest.py:35-57` does establish a temporary override during pytest execution and compares formal before/after, while `app_paths.py` resolves the override dynamically; no current code path proving an isolated pytest write to formal AppData was found. Therefore this is classified as a **process/evidence finding**, not a proven production path-isolation defect. It remains Important because the formal directory was in fact touched, material was moved, and the strongest contemporaneous manifests were destroyed.

**Impact:** the claim “formal AppData was never touched” is false. The final zero-mismatch evidence describes only the post-quarantine state. Without creator provenance, neither the responsible command nor the effectiveness boundary of pytest versus standalone-command isolation is auditable.

**Closure/down-grade conditions:** preserve all future before/observed/final manifests under unique append-only names; set a unique temporary `WECHAT_ASSISTANT_DATA_DIR` in the command environment before Python starts; run a clean controlled full suite while independently watching/snapshotting the formal root; fail immediately and capture PID/command on any formal-root change. A clean controlled run plus an enforced launcher/check can close the current-process control gap (or downgrade it if the historical attribution remains unknowable). If a production/test code path is reproduced despite the override, this must instead be reclassified as a code-isolation Important or Critical and fixed at the path consumer.

## Regression review

- **C1 remains addressed:** `reply_engine.py:98-103,166-194` still makes `knowledge_only` an early, complete knowledge/no-answer branch. Personal and group generation use it; no free/rule/search path was reintroduced.
- **C2 remains addressed:** `reply_engine.py:494-507` still requires exact keys, exact int/bool types, range, and selection of an original candidate only.
- **M2 remains addressed:** JSON and secure-store backup count is fixed at five.
- **M3 remains addressed:** `settings.py:307-308` checks exact string type before managed-mode membership.
- **M6 remains addressed:** runtime logger ownership uses the normalized/casefolded path key.
- **M7 remains addressed:** corrupt/unavailable salt produces a visible typed managed-send block; no silent sending continuation was found.

No new unauthorized sending or atomic data-loss path was found outside I1's settings plaintext persistence risk. Successful managed keypress remains deliberately `uncertain`, not falsely `verified`.

## Evidence verification

- **16-file manifest:** independently recalculated every entry in `phase-01-final-fix2-files.sha256.json`; path, size, SHA-256, and UTC mtime mismatches: **0**. Artifact SHA-256: `4801aa7534a70288b3d9e33ba96c9d18ba02beac9f21c84f24736125ac3faa58`.
- **JUnit:** read `phase-01-final-fix2.xml` as UTF-8 XML. Suite attributes are **tests=543, failures=0, errors=0, skipped=0, time=33.855**. Artifact SHA-256: `08ee02c89fc638f48c2b7fb15a49a74e6cc5ce4b8cb8d3ecc2fb51eafaac94e9`. This establishes the recorded run, not the missing I1 case.
- **Compile evidence:** `phase-01-final-fix2-compileall.txt` contains `COMPILEALL_EXIT=0`; SHA-256 `9b8c838578fe273a1d7f1db79f70c1d1f66a9c8bccd09f780e4bddaa326dfded`.
- **Formal baseline/final:** `phase-01-final-fix2-formal-appdata-before.json`, `...-after.json`, and `...-current.json` each contain 32 files and each has SHA-256 `a55ef5a730bb363c9e52a9996afb244a09e9863da7370029a410f922742690b2`. Before/after and before/current keyed mismatches: **0**. A fresh read-only enumeration of the live formal root found 32 files and **0** path/size/hash/mtime mismatches against `current`.
- **Incident/quarantine:** incident-manifest SHA-256 `d9df0f83c584744a601d62fe8b0c1f0ed4cfa290ea5265247aee1a5a42c32a1c`; recorded baseline/transient/final counts are 32/34/32. Two quarantine files exist and produced **0** size/hash/mtime mismatches.
- The full suite was not rerun, because this review was authorized to write only its report and the requested evidence could be verified without executing application/runtime initialization. No artifact was overwritten.

## Formal-AppData incident

The correct statement is: **formal AppData was transiently changed after the Round 2 baseline, two new runtime-logger-shaped files were recoverably quarantined, and the current restored state matches the 32-file baseline.** It must not be reported as “never touched.” The original 34-file manifests no longer exist; the incident JSON and quarantined bytes partially reconstruct the event but cannot restore command/PID provenance.

## Remaining Minor triage

- **M1 — remains Minor:** `observe` and `controlled` remain compatibility labels rather than complete Phase 3 semantics. Only `full` enables the legacy sender, so no added sending authority was found.
- **M5 — remains Minor:** 3001-record dedupe metadata merging remains arrival-order-dependent. Round 2 did not introduce new formal data loss or authorization impact at this boundary.

R5/M4 is closed and is no longer counted. Current Minor count: **2**.
