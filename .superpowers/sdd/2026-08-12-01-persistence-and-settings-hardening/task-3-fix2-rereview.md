# Task 3 Fix Round 2 Re-review

## Finding Verdicts

1. **ADDRESSED — compound custom-credential field bypass.** `settings.py:75-100` normalizes camelCase and separators into tokens, classifies `auth`, `authorization`, `token`, `secret`, `password`, `credential`, and related tokens as sensitive, permits only `{api_key}`, `Bearer {api_key}`, or `Basic {api_key}`, and rejects any other Bearer token. Both runtime validation and raw-document validation call this guard at `settings.py:196-203` and `settings.py:249-252`. The five literal-rejection and five placeholder-acceptance parameter cases are present at `tests/test_persistence.py:427-480`; the rejection test also verifies that the primary plus five backups contain none of the tested literal secret at `tests/test_persistence.py:447-459`.

2. **ADDRESSED — malformed v1 credential fields must yield to a valid backup.** Before `load_json()` selects a candidate, `settings.py:243-269` requires v1 `api_key` and `vision_api_key` to be strings and `provider_api_keys` to be `dict[str, str]`; `settings.py:316-325` supplies that validator to candidate selection. The three malformed-primary/valid-`bak.1` cases are covered at `tests/test_persistence.py:503-530` and assert both the backup provider and its distinct model are loaded.

## Fix Round 2 Evidence Check

`task-3-report.md:177-249` records all three new test names (`:186-188`), the focused RED command and actual failure output (`:195-201`, `5 failed, 8 passed, 40 deselected`), and the focused GREEN output (`:208`, `13 passed, 40 deselected`). The supplied review package carries the same entries at `task-3-fix2-review-package.txt:1200-1231`.

Fresh, scoped verification (no full-suite rerun):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "compound_credential_fields or v1_malformed_credential_field" -v
```

Current result: `13 passed, 40 deselected in 0.76s`.

## New Breakage

None found within this round's changed guard and v1 candidate-selection scope. The focused tests cover every requested literal/placeholder variant and all three malformed v1 credential variants.

## Out-of-Scope

Previously recorded minors, broader runtime sending/allowlist integration, UI visual acceptance, and any full-suite regressions were not re-reviewed. No full test suite was run.

## Verdict

**PASS.** Both Fix Round 2 open findings are addressed, the report contains the required RED/GREEN evidence, and the fresh scoped verification matches the declared GREEN count.
