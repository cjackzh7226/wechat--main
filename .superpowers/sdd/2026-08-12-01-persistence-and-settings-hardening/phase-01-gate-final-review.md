# Phase 1 Final Gate Closure Review

Date: 2026-08-12  
Scope: closure-only review of the two Important findings remaining in `phase-01-final-rereview2.md`. Review was read-only; no Python, pytest, compile, application, runtime logger, WeChat/WeCom, sender, E2E, or network operation was executed. This report is the only file written.

## Assessment

**PASS.** Final count: **0 Critical, 0 Important, 3 Minor**.

Both gate-blocking Important findings are addressed. The earlier 34-item formal-AppData event is not erased or recharacterized as “never touched”; it remains a known historical process incident with unknown attribution and preserved evidence, now Minor because a controlled, append-only rerun proves the current suite/compile isolation boundary without changing the formal tree.

## Closure table

| Gate item | Status | Closure basis |
|---|---|---|
| R2 anonymous list string leaves | **ADDRESSED** | `settings.py:157-217` now carries `allow_anonymous_string` and `inside_unknown_container` through both dict and list recursion. Unknown containers cannot gain string permission by adding list/dict levels; an anonymous string fails at lines 208-210. String lists are permitted only under a reviewed `_SAFE_BODY_STRING_KEYS` parent (the tested positive is `input`), while `custom_headers` remains scalar-string-only. Sensitive keys still accept only the three exact sentinel forms at lines 168-171. Direct, multilevel, mixed dict/list, placeholder-adjacent, header-list, approved-list, persistence scan, placeholder, and existing custom-HTTP compatibility evidence all pass. |
| Current process/evidence isolation | **ADDRESSED** | The successful append-only wrapper derives the workspace from `$PSScriptRoot`, creates a unique retained temporary data root, sets `WECHAT_ASSISTANT_DATA_DIR` before Python starts, snapshots formal AppData before/after pytest and after compileall, and stops on mismatch. Raw output proves pytest exit 0, compileall exit 0, 32/32/32 items, identical tree hashes, and zero mismatch. The wrapper and output preserve the full command boundary. |

## R2 code and test verification

### Production behavior

- `settings.py:92-94` defines reviewed body-container context (`messages`); `settings.py:72-91` defines reviewed business string fields.
- In a dict, an unknown string-bearing key or any string reached while already inside an unknown container is rejected (`settings.py:173-183`).
- A known business string parent passes string permission to its list descendants only when the path has not crossed an unknown container (`settings.py:187-197`).
- List recursion preserves both flags rather than dropping key/path context (`settings.py:199-207`).
- A leaf string without explicit permission is rejected (`settings.py:208-210`). This closes `{"outer":["literal-secret"]}`, arbitrary nesting, and mixed list/dict variants.
- `custom_headers` rejects non-string values, including lists (`settings.py:185-186`).
- Sensitive-key values must be a string exactly equal, after trimming, to the API-key sentinel, `Bearer` sentinel, or `Basic` sentinel (`settings.py:33-38,168-171`). Normal decoded strings are still scanned for literal Bearer/Basic credentials (`settings.py:214-217`).

### Preserved RED/GREEN and compatibility evidence

- RED `phase-01-final-gate-r2-anonymous-list-red.txt`: 5 failed, 2 passed; SHA-256 `acd07756e10c453f554e962c17ec786adaef6d14319ed24bca73444a01ca2a15`.
- GREEN `phase-01-final-gate-r2-anonymous-list-green.txt`: 7 passed; SHA-256 `b4c29ff019c42447cb3674c24ab6f14443dc70dae416a916c7b1ac59f0b43b7a`.
- Existing 42 plus new gate cases: 49 passed; SHA-256 `d84c20f9d13bef29e8e1cd58842e80a05389509d602cf532175287f04acff05f`. Its tail explicitly includes approved credential placeholders plus the custom HTTP request and settings round-trip tests.
- Settings regression: 145 passed plus 2 subtests; SHA-256 `463aeed236ab9b34afb113c7d9ead4e8c994ab3aa13f2671fd5c30427e025a0c`.
- `tests/test_phase1_final_fix.py:469-532` directly covers body direct/multilevel/mixed lists, a valid credential-placeholder sibling next to an invalid anonymous leaf, header lists, primary plus five backups, and a reviewed `input` string list.

The positive schema intentionally rejects new literal-string fields or list containers until their parent is explicitly reviewed and allowlisted. That compatibility tradeoff is fail-closed and matches the formal contract.

## Gate evidence verification

### Controlled wrapper

- Failed attempt 1 remains preserved as `phase-01-final-gate-wrapper.ps1` and `phase-01-final-gate-wrapper-output.txt`. Its output contains only the newly created temp root, explicit override, and preservation marker. The hard-coded Chinese workspace path failed before a Python process or gate-before manifest; it did not produce a passing claim.
- Successful attempt 2 is `phase-01-final-gate-wrapper-attempt-2.ps1`. It refuses to overwrite any of the five gate outputs, derives the workspace from `$PSScriptRoot`, creates `C:\Users\14913\AppData\Local\Temp\wechat-assistant-formal-gate-72c2965d4ee94a76bf36eb080a905755`, and assigns it to `WECHAT_ASSISTANT_DATA_DIR` before invoking Python. Both attempt temp roots still exist.
- Complete raw output `phase-01-final-gate-wrapper-output-attempt-2.txt` has independently verified SHA-256 `d8ac3fda337af2704e43af10eb6ef92ac49ad9324eb2b7bd358a939de701092b`. It records `PYTEST_EXIT=0`, `COMPILEALL_EXIT=0`, `AFTER_MISMATCH=0`, `CURRENT_MISMATCH=0`, and `GATE_COMPLETED=true`.

### Formal AppData manifests

- `phase-01-final-gate-before.json`: 32 items; tree SHA-256 `0c5f604f74bcb9e2b5cf0c4acf787c9b6eab2eaa031870e34884fdc7b19c58de`; artifact SHA-256 `cd4d7dd1b933d4b9d567bfb5241077a6149f79c7917b94d32d31821603d712ea`.
- `phase-01-final-gate-after.json`: 32 items; same tree hash; before/after path-size-hash-mtime difference 0; artifact SHA-256 `8e5d390af9f1d5e2f64a08b5b5fd67844ba7f3682dce60b26dc6e61eaf8adca5`.
- `phase-01-final-gate-current.json`: 32 items; same tree hash; before/current difference 0; artifact SHA-256 `8a5028df1e6af7eff3f239c7b62f559bb2b27596d24f363d22b4d381ca8be200`.
- This review's fresh PowerShell-only enumeration found 32 live formal files and zero path/size/hash/mtime differences from gate-current.

These are distinct, append-only artifacts. The wrapper captures before state before pytest, after state immediately after pytest, and current state after compileall. This is sufficient to close the prior blocker that the current full-suite isolation was unproved and its final evidence unreliable.

### Tests, compile, and source manifest

- Raw full-run output records 544 passed, 0 failed, 74 warnings, and 6 subtests in 35.15s.
- JUnit `phase-01-final-gate.xml`: tests 550, failures 0, errors 0, skipped 0, time 35.039; SHA-256 `4cf1e49f0323490f3929d9a891f4911ba6e0e3e2e02cf90ce1fc9dc06879afeb`.
- `phase-01-final-gate-compileall.txt`: `COMPILEALL_EXIT=0`; SHA-256 `cec02bf8b7c368ecc16eac1599bf062351f403f305ee24aaa8f9f6e02e0169ff`.
- `phase-01-final-gate-files.sha256.json`: 16 files; artifact SHA-256 `d4a7689c53976b44567fab44225ca103a28ecfbb7463f8ae7b1e2b442702c6c2`; independent size/hash mismatches 0.
- R1/R3/R4/R5 production hashes are unchanged from the prior 16-file manifest: `main.py` `f41d59...db30`, `file_transaction.py` `a60fa6...b2e1`, `atomic_json.py` `770952...17e6`, `secure_store.py` `3fc17e...9ace`, `send_ledger.py` `6f871b...f7c5`, and `float_ui.py` `22bd6b...913`. This gate did not reopen those addressed findings.

## Historical formal-AppData incident status

The historical fact remains: after a prior 32-item baseline, `logs/.runtime-salt` and empty `logs/runtime.log` appeared in formal AppData, producing a 34-item state; the original 34-item after/current manifests were later overwritten; creator PID/command was never captured; the two additions were recoverably moved to quarantine.

The evidence remains intact:

- `phase-01-final-fix2-formal-appdata-transient-additions.json` exists and retains SHA-256 `d9df0f83c584744a601d62fe8b0c1f0ed4cfa290ea5265247aee1a5a42c32a1c`.
- `phase-01-final-fix2-formal-appdata-additions-quarantine/` still contains both preserved files.
- The Gate Fix report explicitly states that the clean controlled rerun does not negate the event and does not claim formal AppData was never touched.

The controlled rerun closes the **current isolation/evidence blocking condition**, not the unknowable historical attribution. The incident is retained as a **Minor known process incident**: no user bytes were lost (the additions were new runtime-logger artifacts and are preserved), the formal tree is restored exactly, current controlled isolation is demonstrated, and no recurrence occurred. Future formal gates should keep unique append-only names and the wrapper boundary; any new formal-tree change must stop immediately with command/PID provenance and be re-escalated.

## Remaining Minors

- **M1:** `observe`/`controlled` remain compatibility labels pending the Phase 3 state machine. Only `full` enables sending; this gate adds no authority.
- **M5:** 3001-record dedupe metadata merging remains arrival-order-dependent, deferred to Phase 2.
- **Historical formal-AppData incident:** retained as described above; attribution remains unknown even though current controlled isolation is now proven.

No Critical or Important finding remains in this closure scope.
