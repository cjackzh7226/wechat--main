# Task 4 Fix Round 1 Re-review

## Finding Verdicts

1. **ADDRESSED — persistent vector health and typed blocking error.** `VectorIndexError` is public at `memory.py:44-45`; `StyleMemory` retains queryable `vector_ready` and `last_vector_error` state at `memory.py:224-225`. Rebuild starts fail-closed, records a sanitized typed failure, and becomes ready only after the atomic vector write succeeds at `memory.py:744-759`; callers can fail closed through `assert_ready_for_managed_reply()` at `memory.py:761-763`. The load-time and runtime-save failure/recovery paths are exercised at `tests/test_persistence.py:296-361`. Consumption by the automatic-send boundary remains explicitly assigned to Task 5/runtime and is not a Task 4 defect.

2. **ADDRESSED — a missing primary still scans all five managed backups.** `StyleMemory.load()` returns the new-install empty state only when the primary and every `bak.1..5` candidate are absent; otherwise it calls the shared loader at `memory.py:235-240`. The shared loader scans primary followed by all five backups and raises `JsonDataError` only after every candidate fails at `atomic_json.py:41-55`. Valid-backup recovery and invalid-only byte preservation are covered at `tests/test_persistence.py:115-143`.

3. **ADDRESSED — strict v1/v2 candidate semantics.** `validate_memory_document()` requires the three core top-level fields for both versions, validates their types, requires each example to contain `cue`, `reply`, `source`, and `conversation_hash`, and rejects an example whose actual normalization yields no reply at `memory.py:138-167`. Because the item loop is vacuous for an empty list, an explicit `examples=[]` document remains legal. The malformed-primary/valid-backup cases are present at `tests/test_persistence.py:146-189`. There is no dedicated positive regression named for the legal empty-library case, but the implementation behavior is unambiguous.

4. **NOT ADDRESSED — strict scalar validation is fixed, but authoritative-memory alignment is still incomplete.** The validator correctly rejects empty per-item vectors, noncanonical/out-of-range buckets, non-finite weights, and weights outside `(0, 1]` at `memory.py:178-213`; loaded items are compared with regenerated items at `memory.py:701-721`. However, `memory.py:728-733` silently omits any authoritative example for which `_text_vector()` returns `{}`, while the vector schema permits the overall `items` list to be empty at `memory.py:188-191`. `_text_vector()` returns `{}` when no feature of length 2-4 exists at `memory.py:1120-1129`. A fresh pure-memory probe used a validator-accepted example `{cue: "", reply: "x", source: "manual_edit", conversation_hash: "h"}` and produced `example_count=1` but `vector_item_count=0`; the resulting current-hash vector document with `items=[]` passed `_validate_vector_document()`. A rebuild can therefore mark `vector_ready=True` at `memory.py:744-759` while omitting a valid authoritative memory record. The implementation must either reject such memory examples before candidate selection/save or make every valid example produce a non-empty vector; silently skipping them does not satisfy semantic alignment.

## Fix Round 1 Evidence Check

All Fix Round 1 test names listed in `task-4-report.md:179-188` exist in the current test file at `tests/test_persistence.py:115-189` and `tests/test_persistence.py:296-419`, including the stated parameter cases.

The RED commands and claimed failures are recorded at `task-4-report.md:190-206`. Their selection counts are internally consistent: the primary selector covers 14 cases (2 missing-primary, 4 invalid-primary, 1 runtime vector-failure, and 7 invalid-vector cases), while the separate typed-error selector covers the strengthened vector-write-failure test. The supplied review package repeats those report statements but does not contain raw pre-fix pytest output, so the historical RED execution cannot be independently replayed from the package without changing the current read-only workspace.

Fresh scoped GREEN verification (pytest cache disabled to avoid project writes):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "missing_primary or invalid_primary_candidate or vector_failure or invalid_vector_payload or vector_write_failure_is_observable" -v -p no:cacheprovider
```

Current result: `15 passed, 79 deselected in 0.49s`, exactly matching `task-4-report.md:208-216`. Current SHA-256 values also match the updated checkpoint at `task-4-report.md:247-254`: `memory.py` is `37026BD2E18B33AF9667EC5DD2EA7D071D098C79BE882AC4C7C6947312949798`; `tests/test_persistence.py` is `226245EDCE1468CE39F57F339FC9E12CD260E5D5CF329D3AD53208EAF8C09C2F`. Per the re-review instruction, the 87-test target and full suite were not rerun.

## New Breakage

None found beyond the residual semantic-alignment defect described under Finding 4. The focused fixes for health state, memory candidate recovery, semantic memory validation, and malformed vector values did not reveal another regression in their changed scope.

## Out-of-Scope

- Task 5/runtime consumption of `assert_ready_for_managed_reply()` and user-visible blocking behavior.
- Previously recorded minor deduplication/documentation concerns.
- Broader reply ranking, UI behavior, packaging, and full-suite regressions.
- No full test suite was run and no project file other than this re-review report was intentionally written.

## Verdict

**NEEDS FIXES.** Findings 1-3 are addressed and the declared focused GREEN output is accurate, but Finding 4 remains open because a validator-accepted authoritative memory example can be omitted from a vector document that is nevertheless accepted and marked ready.
