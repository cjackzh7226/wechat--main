# Task 4 Fix Round 2 Re-review

## Finding Verdict

**Resolved.** The one-character authoritative examples are now assigned a deterministic sparse-vector feature when ordinary token/n-gram extraction yields none (`memory.py:1101-1120`).  The stored-item builder uses that same function and hence produces one non-empty item for each currently valid non-empty normalized example (`memory.py:715-729`); the strict vector validator still requires every persisted item vector to be non-empty and leaves its bucket/weight constraints intact (`memory.py:178-205`).

The regression test covers the two reported authoritative shapes: `cue=""`, reply `"x"` and `"中"`; it asserts one item per example, a non-empty item vector, ready health, query-vector equivalence, and no vector backup after two loads (therefore no repeated atomic vector rewrite) (`tests/test_persistence.py:422-451`).

Evidence check: the documented RED command and outcome are `pytest tests\\test_persistence.py -k "single_character_examples" -v` -> exit 1, 2 failed/94 deselected before the change; the documented GREEN output is exit 0, 2 passed/94 deselected (`.superpowers/sdd/2026-08-12-01-persistence-and-settings-hardening/task-4-fix2-review-package.txt:2312-2325`). Fresh focused verification reproduced the GREEN result: `2 passed, 94 deselected in 0.16s`.

## New Breakage

None found within this fix's scope. The fallback runs only if normal feature extraction is empty and the normalized text is non-empty, so vectors already possessing token or 2--4-character features are unchanged (`memory.py:1104-1112`). The current checkpoint hashes match the Round 2 evidence package: `memory.py` `3EB04A...A4C6B` and `tests/test_persistence.py` `CE12A7...3E03` (`.superpowers/sdd/2026-08-12-01-persistence-and-settings-hardening/task-4-fix2-review-package.txt:2344-2351`).

## Out-of-Scope

Automatic-send boundary enforcement via `assert_ready_for_managed_reply()` remains a later Task 5/runtime integration responsibility; this round neither changes nor verifies that outer integration (`memory.py:748-750`; `.superpowers/sdd/2026-08-12-01-persistence-and-settings-hardening/task-4-fix2-review-package.txt:2353-2358`). No full test suite was run for this focused re-review.

## Verdict

**PASS.** The remaining Important finding is resolved, its strict-validation boundary is preserved, and the focused Round 2 regression evidence is current and accurate.
