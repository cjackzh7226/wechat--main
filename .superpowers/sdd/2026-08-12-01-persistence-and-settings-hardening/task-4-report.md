# Task 4 Report: Recoverable Memory and Vector Consistency

## Status

PASS. Task 4 is implemented in the authorized non-Git workspace. No commit was created. `main.py` and `settings.py` were not modified.

## Scope implemented

- `memory.json` now uses `atomic_json.load_json()` and `atomic_json.atomic_write_json(..., backup_count=5)`.
- Added public `validate_memory_document(value: object) -> None` with strict top-level, example-field and phrase-count type checks. Version 2 is the persisted schema; legacy version 1 documents remain readable.
- Added observable `StyleMemory.load_result: JsonLoadResult | None`.
- Removed persistence-time and reload-time `[-3000:]` slicing. A save and reload of 3,001 unique examples preserves all 3,001.
- Replaced the old ad-hoc `bak-auto-*` safeguard with the shared five-valid-backup atomic writer.
- Added public `StyleMemory.rebuild_vector_index() -> None`.
- Vector documents retain schema `version=1`, dimension `384`, and now include a SHA-256 `memory_hash`.
- Missing, corrupt, recovered-from-backup, wrong-schema, or stale-hash vector state is rebuilt from the validated memory document during `StyleMemory.load()`.
- Vector writes also use the shared validated atomic writer. Rebuild failures propagate to the caller and leave the authoritative memory bytes unchanged.
- `StyleMemory.content_hash()` uses UTF-8 JSON with `sort_keys=True` and compact separators, so physical key order and whitespace do not affect the hash.
- Legacy records without `created_at` normalize to a deterministic empty string instead of the current clock, preventing hash drift and repeated vector rebuilds.
- Existing `conversation_hash + reply` dedupe behavior remains; two records sharing a conversation hash but containing different legitimate answers are both preserved. No institution-specific or later knowledge-schema semantics were introduced.
- A missing memory file on a new installation returns empty in-memory state without creating either `memory.json` or `vector_memory.json`.

## Strict TDD evidence

### Primary RED required by the brief

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "3000 or corrupt_primary or vector_hash" -v
```

Actual result before production changes: exit code `1`; `6 failed, 329 deselected`.

- `test_save_does_not_truncate_more_than_3000_records`: persisted 3,000 instead of 3,001.
- `test_corrupt_primary_recovers_backup_without_empty_overwrite`: raised raw `json.JSONDecodeError` instead of recovering `.bak.1`.
- `test_vector_hash_and_other_invalid_states_rebuild_from_memory[missing]`: no vector file was created.
- `[corrupt]`: corrupt bytes remained and parsing failed.
- `[schema_mismatch]`: vector version `999` remained unchanged.
- `[hash_mismatch]`: no `content_hash()` existed and the stale vector was not rebuilt.

Supplementary RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "memory_validator or all_corrupt_memory or vector_write_failure or content_hash or dedupe_preserves or missing_memory" -v
```

Actual result before production changes: exit code `1`; `19 failed, 1 passed, 59 deselected`. The one pass was the characterization that different replies sharing a conversation hash were already retained.

Stable legacy hash RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "legacy_memory_without_created_at" -v
```

Actual result before the deterministic normalization fix: exit code `1`; `1 failed, 79 deselected`, with different SHA-256 values on two loads of identical legacy bytes.

### GREEN evidence

New Task 4 behavior tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "save_does_not_truncate or corrupt_primary or all_corrupt_memory or memory_validator or vector_hash or vector_write_failure or content_hash or dedupe_preserves or missing_memory" -v
```

Actual result: exit code `0`; `26 passed, 53 deselected`.

Stable-hash follow-up:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "legacy_memory_without_created_at or content_hash or vector_hash" -v
```

Actual result: exit code `0`; `6 passed, 74 deselected`.

Required memory/vector suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "MemoryTests or memory or vector" -v
```

The first compatibility run exposed one obsolete assertion requiring `memory.json.bak-auto-*`. The test was updated to assert the Task 2 managed backup `memory.json.bak.1` contains the overwritten 60-example snapshot. Final result: exit code `0`; `78 passed, 257 deselected`.

Full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -v
```

Actual result: exit code `0`; `336 passed, 6 subtests passed in 9.10s`.

Compile check:

```powershell
.\.venv\Scripts\python.exe -m py_compile memory.py tests\test_persistence.py tests\test_core.py
```

Actual result: exit code `0`, no output.

## Added/updated behavioral tests

- `test_save_does_not_truncate_more_than_3000_records`
- `test_corrupt_primary_recovers_backup_without_empty_overwrite`
- `test_all_corrupt_memory_candidates_raise_typed_error_without_byte_changes`
- `test_memory_validator_rejects_wrong_top_level_field_types`
- `test_memory_validator_rejects_non_string_example_fields`
- `test_memory_validator_requires_non_negative_integer_phrase_counts`
- `test_vector_hash_and_other_invalid_states_rebuild_from_memory`
- `test_vector_write_failure_is_observable_and_preserves_memory_hash`
- `test_content_hash_uses_stable_canonical_json`
- `test_legacy_memory_without_created_at_keeps_stable_hash`
- `test_dedupe_preserves_different_answers_sharing_conversation_hash`
- `test_missing_memory_returns_empty_without_creating_primary_or_vector`
- Updated `MemoryTests.test_save_keeps_previous_memory_in_managed_backup` to check the unified managed backup rather than the removed ad-hoc filename.

All new pytest-style persistence tests use `tmp_path` through `isolated_memory_root` and set a temporary `WECHAT_ASSISTANT_DATA_DIR` with `monkeypatch`.

## Changed files

- `memory.py`
- `tests/test_persistence.py`
- `tests/test_core.py`
- `artifacts/formal-hardening/2026-08-12/04-memory-vector.sha256.json`
- `.superpowers/sdd/2026-08-12-01-persistence-and-settings-hardening/task-4-report.md`

Not changed: `main.py`, `settings.py`, `atomic_json.py`, `app_paths.py`.

## Checkpoint

Command required by the brief was run:

```powershell
Get-FileHash memory.py,tests\test_persistence.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\04-memory-vector.sha256.json -Encoding UTF8
```

Recorded hashes:

- `memory.py`: `C71FA37E2E6C6CDE869F0BE248C146C044A9CCD6093C0977237772C5CCB723D9`
- `tests/test_persistence.py`: `B129CE7168A295919D2CDCD5015EC20C86EC98C7958404163BCFCD57FC8E6944`

## Self-review and concerns

- Confirmed no direct `read_text`/`write_text` persistence remains in `StyleMemory`; memory and vector JSON both use the shared atomic/recovery layer.
- Confirmed all-invalid memory candidates raise `JsonDataError` without changing any candidate bytes.
- Confirmed corrupt-primary recovery does not overwrite the corrupt primary with an empty document.
- Confirmed vector rebuild failure is observable as the underlying write exception and the memory primary SHA-256 remains unchanged.
- Confirmed `max_examples` is no longer applied by `StyleMemory.save()` or `StyleMemory.load()`. The field remains because existing import call sites use it as an upstream selection parameter; changing those import policies would modify files and ingestion semantics outside this Task 4 brief.
- Vector rebuilding is synchronous. A very large memory may add load/save latency, but failing closed and rebuilding from the authoritative body is the required consistency behavior; no benchmark or background migration was added in this phase.
- No real user memory file was opened or modified during implementation. New persistence tests were isolated under pytest temporary directories.

## Fix Round 1

### Review findings addressed

1. **Critical — persistent vector health and typed blocking error**
   - Added public `VectorIndexError`.
   - Added observable `StyleMemory.vector_ready: bool` and `StyleMemory.last_vector_error: str | None`.
   - Every rebuild starts fail-closed with `vector_ready=False`. Successful validation or atomic rebuild sets it to `True` and clears the error.
   - A rebuild exception remains observable as `VectorIndexError`, chains the underlying cause, keeps `vector_ready=False`, and stores only a sanitized reason consisting of the exception type plus a fixed message. Paths, customer text, and the raw exception message are not retained.
   - Added `StyleMemory.assert_ready_for_managed_reply() -> None`; it raises `VectorIndexError` whenever the vector is not ready.
   - Runtime `save()` failure coverage proves the newly persisted memory body remains valid, health checks reject, and a later successful `rebuild_vector_index()` restores readiness without changing the memory primary bytes.

2. **Important — missing primary now still examines backups**
   - `StyleMemory.load()` treats the installation as new only when `memory.json` and all five managed backup candidates are absent.
   - If any candidate exists, `load_json()` performs normal validated selection. A missing primary can therefore recover `.bak.1`; invalid-only backups raise `JsonDataError` without byte changes.

3. **Important — strict v1 and v2 candidate semantics**
   - Both versions now require `style_summary`, `examples`, and `common_phrases` to exist with the correct types.
   - `examples=[]` remains a valid explicit empty library.
   - Every non-empty example item must be an object with string `cue`, `reply`, `source`, and `conversation_hash`, and its reply must remain non-empty after the real normalization path.
   - Empty objects, missing top-level fields, whitespace-only replies, and partial legacy items can no longer preempt a valid backup.

4. **Important — strict vector values and authoritative semantic match**
   - Sparse vectors must be non-empty.
   - Bucket keys must be canonical base-10 strings in `0..383`.
   - Weights must be finite numeric values in `(0, 1]`; `NaN`, infinity, booleans, zero, negatives, and values above one are invalid.
   - After schema and `memory_hash` validation, vector `items` must exactly match the vectors regenerated from the in-memory authoritative examples, covering `conversation_hash`, `source`, `reply`, item count/order, and vector values.

### Fix Round 1 tests

Added or strengthened:

- `test_missing_primary_recovers_valid_backup`
- `test_missing_primary_with_only_invalid_backups_raises_without_byte_changes`
- `test_invalid_primary_candidate_cannot_preempt_valid_backup` with `empty-object`, `missing-core-field`, `empty-reply`, and `partial-v1-item` cases
- `test_vector_write_failure_is_observable_and_preserves_memory_hash` now requires `VectorIndexError` and a failing health check
- `test_runtime_save_vector_failure_blocks_until_successful_rebuild`
- `test_invalid_vector_payload_with_current_memory_hash_is_rebuilt` with `nan-weight`, `infinite-weight`, `weight-out-of-range`, `bucket-out-of-range`, `noncanonical-bucket`, `empty-vector`, and `record-mismatch` cases

### Fix Round 1 RED evidence

Primary RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "missing_primary or invalid_primary_candidate or vector_failure or invalid_vector_payload" -v
```

Actual result before the fix: exit code `1`; `14 failed, 80 deselected`. Both missing-primary cases short-circuited as empty; all four invalid primary candidates were selected instead of the backup; the health API did not exist; all seven malformed/current-hash vector payloads remained unchanged.

Typed-error RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "vector_write_failure_is_observable" -v
```

Actual result before the fix: exit code `1`; `1 failed, 93 deselected`, because `VectorIndexError` did not exist.

### Fix Round 1 GREEN and regression evidence

Focused GREEN command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "missing_primary or invalid_primary_candidate or vector_failure or invalid_vector_payload or vector_write_failure_is_observable" -v
```

Actual result: exit code `0`; `15 passed, 79 deselected`.

Memory/vector target command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "MemoryTests or memory or vector" -v
```

Actual result: exit code `0`; `87 passed, 263 deselected`.

Full suite and compile command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -v
.\.venv\Scripts\python.exe -m compileall -q memory.py tests\test_persistence.py tests\test_core.py
```

Actual result: exit code `0`; `350 passed, 6 subtests passed in 10.11s`; `compileall` produced no errors.

### Cross-task interface boundary

Task 4 now owns and tests the durable in-process health contract:

```python
memory.vector_ready
memory.last_vector_error
memory.assert_ready_for_managed_reply()
```

Task 5 or the later runtime integration must call `assert_ready_for_managed_reply()` at the automatic-send boundary and convert `VectorIndexError` into the user-visible blocking state. Task 4 intentionally does not edit `main.py` and does not decide UI/runtime policy.

### Updated checkpoint

The required checkpoint file was regenerated after Fix Round 1:

- `memory.py`: `37026BD2E18B33AF9667EC5DD2EA7D071D098C79BE882AC4C7C6947312949798`
- `tests/test_persistence.py`: `226245EDCE1468CE39F57F339FC9E12CD260E5D5CF329D3AD53208EAF8C09C2F`

The earlier checkpoint values in the base Task 4 section document the pre-review implementation and are superseded by these Fix Round 1 values.

### Remaining concern

- The automatic-send entry point does not consume the health guard in this task by explicit cross-task decision. Until Task 5/runtime integration calls `assert_ready_for_managed_reply()`, the state is available and tested but not yet enforced at that outer boundary.

## Fix Round 2

### Finding and root cause

- `_text_tokens()` only emits tokens of length two or greater, while `_text_vector()` previously emitted only 2-, 3-, and 4-character features.
- A valid authoritative example whose normalized text was a single character therefore produced `{}`. `_build_vector_items()` silently skipped it, allowing an empty `items` list to validate and incorrectly set `vector_ready=True`.
- `_text_vector()` now emits one deterministic fallback feature, `single:<normalized text>`, only when normalized text is non-empty and the normal feature set is empty. The same function is used for stored examples and queries, so one-character Chinese and ASCII replies remain mutually searchable. The vector validator was not relaxed.

### Tests added

- `test_single_character_examples_always_have_stable_vector_items[ascii]`
- `test_single_character_examples_always_have_stable_vector_items[chinese]`

Each case uses an empty cue and a one-character reply (`x` or `中`) and proves that the built item count equals the authoritative example count, the vector is non-empty, a rebuild finishes ready, and two subsequent loads do not trigger another vector rewrite.

### RED evidence

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "single_character_examples" -v
```

Actual result before the fix: exit code `1`; `2 failed, 94 deselected`. Both parameterized cases built zero vector items for one authoritative example.

### GREEN and regression evidence

Focused GREEN command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "single_character_examples" -v
```

Actual result: exit code `0`; `2 passed, 94 deselected`.

Memory/vector target command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "MemoryTests or memory or vector" -v
```

Actual result: exit code `0`; `89 passed, 263 deselected in 2.60s`.

Full suite and compile command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -v
.\.venv\Scripts\python.exe -m compileall -q memory.py tests\test_persistence.py tests\test_core.py
```

Actual result: exit code `0`; `352 passed, 6 subtests passed in 9.03s`; `compileall` produced no output or errors.

### Updated checkpoint

The required checkpoint was regenerated after Fix Round 2:

- `memory.py`: `3EB04AD35BE57B66A961013889CC20586E480F7DBC3FE99848B712DD7B7A4C6B`
- `tests/test_persistence.py`: `CE12A768DFB3469F7AC7572EC48C94D413D22860FE1FECA4D34D445B54A13E03`

These values supersede the earlier Task 4 and Fix Round 1 checkpoint values.

### Fix Round 2 self-review

- Every currently valid non-empty example produces a non-empty vector item, including one-character ASCII and Chinese replies.
- The fallback is stable and isolated to inputs that cannot produce existing 2-4 character features; vectors for ordinary longer text are unchanged.
- No vector-schema constraint was weakened, and `main.py` and later knowledge-mode behavior remain untouched.
- No new concern was introduced. The previously documented Task 5/runtime responsibility to consume `assert_ready_for_managed_reply()` remains unchanged.
