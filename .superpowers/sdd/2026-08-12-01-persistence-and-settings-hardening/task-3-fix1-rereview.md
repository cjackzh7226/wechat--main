# Task 3 Fix Round 1 Re-review

## Finding Verdicts

1. **Critical — ADDRESSED** (`settings.py:288-294`, `settings.py:435-442`; regression at `tests/test_persistence.py:357-385`)
   - `_persist_settings()` now passes `_validate_redacted_document()` into `atomic_write_json()`. That validator rejects all three legacy top-level credential fields for both v1 and v2 candidates before backup rotation, so credential-bearing primary/backup snapshots are skipped and the five managed backup slots are rewritten or removed.
   - The added `test_legacy_migration_purges_plaintext_keys_from_primary_and_all_settings_backups` failed in the preserved RED log and is included in the current focused GREEN run.

2. **Important — NOT ADDRESSED** (`settings.py:75-90`; current coverage at `tests/test_persistence.py:388-424`)
   - The new validator rejects literal Bearer values anywhere and literal values under a limited set of recognized field names, but its field-name classifier is still bypassable. `X-Auth` tokenizes to `x, auth` and `Proxy-Authorization` to `proxy, authorization`; neither `auth` nor `authorization` is included in the per-part sensitive set, and the compact names are not exact matches. A focused in-memory probe confirmed both `{"X-Auth":"literal-secret"}` and `{"Proxy-Authorization":"Basic literal-secret"}` are accepted.
   - Therefore literal credentials can still be persisted into `custom_headers`/`custom_body` and rotated into settings backups, contrary to the finding's requirement that credential-bearing template values use placeholders or be stored separately.
   - The report does provide the added parameterized test name, the first RED command/result (`11 failed, 2 passed`) plus the supplemental `auth` RED (`1 failed, 4 passed`), and GREEN outputs. However, the regression cases cover only `Authorization`, `X-Token`, `token`, `auth`, and `api_key`; they do not cover common compound auth/authorization field names or scan persisted primary/backups for these bypasses.

3. **Important — ADDRESSED** (`float_ui.py:598-604`; regressions at `tests/test_core.py:3015-3072`)
   - `_set_source_mode()` fixes both UI variables and the settings object to `knowledge_only`, persists it, and no longer invokes the legacy `source_mode_changed` callback.
   - Both the callback-is-not-called test and the integration test wired to the real legacy callback were present in RED/GREEN evidence; the latter reloads the value from disk.

4. **Important — ADDRESSED** (`float_ui.py:573-592`, `float_ui.py:1481-1485`; regressions at `tests/test_core.py:3074-3145`)
   - `_set_managed_mode()` is now the shared transition path. After the legacy bool callback returns, it restores the selected mode and performs the final save; the settings dialog uses the same method with `persist=False` and then saves once after the rest of the dialog fields are applied.
   - The real legacy callback regressions reload both `observe` and `controlled` from disk, and the dialog-path regression also verifies disk state. The preserved RED log records both mode subfailures and the dialog failure; the current focused run passes them.

5. **Important — NOT ADDRESSED** (`settings.py:233-286`, `settings.py:398-412`; current regression at `tests/test_persistence.py:427-444`)
   - The raw-candidate validator now checks the ordinary persisted string fields before candidate selection, so the added v2 `api_provider` corruption example is fixed. It still does not validate the v1 persisted credential fields `api_key`, `vision_api_key`, and `provider_api_keys`; their type checks remain deferred to `_legacy_credentials()` after `load_json()` has already selected the primary.
   - A focused temporary-directory probe with primary `{"api_key":["corrupt"],"model":"bad-primary-model"}` and a valid v2 `bak.1` returned the primary's `bad-primary-model` with mode forced to `off`, instead of loading `good-backup-model`. Thus a malformed primary still blocks a valid backup for a supported legacy document shape.
   - The report supplies a test name, RED command/failure, and GREEN output, but that test exercises only one v2 ordinary string field and does not close the full candidate-validation contract stated by the finding.

## New Breakage

No additional Critical or Important regression attributable to these fixes was found. The two issues above are unresolved portions of the original Important findings, not newly introduced breakage.

## Evidence Check

- Current checkpoint hashes match the fix report/package for `secure_store.py`, `settings.py`, and `float_ui.py`.
- Fresh allowed focused run: `12 passed, 284 deselected, 2 subtests passed in 1.00s`.
- Preserved RED log: `11 failed, 2 passed, 284 deselected in 2.07s`; it contains the named failures/subfailures for Findings 1, 3, 4, and the tested examples for Findings 2 and 5.
- Preserved GREEN logs match the report exactly: focused `12 passed, 284 deselected, 2 subtests passed in 1.03s`; target `114 passed, 182 deselected, 2 subtests passed in 1.91s`; persistence `40 passed in 0.61s`; full `344 passed, 74 warnings, 6 subtests passed in 5.43s`. The full suite was not rerun in this re-review.

## Out-of-Scope

- The three original Minor findings were not re-reviewed.
- Four-mode runtime semantics/allowlist enforcement in `main.py`, UI visual layout, unrelated persistence/DPAPI behavior, and unrelated tests were not re-audited.
- No complete test suite was run, per the re-review constraint.

## Verdict

**NEEDS FIXES.** Fix Round 1 resolves the original Critical and two UI Important findings, but the custom-template credential guard and raw backup-candidate validation remain incomplete. Final count: **3 ADDRESSED, 2 NOT ADDRESSED; no new Critical/Important breakage found.**
