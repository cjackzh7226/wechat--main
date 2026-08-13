### Task 4: Recoverable Memory and Vector Consistency

**Files:**
- Modify: `memory.py:109-166, 215-238, 547-602`
- Test: `tests/test_persistence.py`
- Modify: `tests/test_core.py:254-1447`

**Interfaces:**
- Produces: `validate_memory_document(value: object) -> None`, `StyleMemory.rebuild_vector_index() -> None`, `StyleMemory.load_result: JsonLoadResult | None`.
- Consumes: `atomic_json.load_json`, `atomic_json.atomic_write_json`, `app_paths.memory_path`, `app_paths.vector_path`.

- [ ] **Step 1: Write failing no-truncation, corruption and vector-rebuild tests**

```python
def test_save_does_not_truncate_more_than_3000_records(tmp_path):
    memory = StyleMemory(path=tmp_path / "memory.json")
    memory.examples = [valid_example(i) for i in range(3001)]
    memory.save()
    assert len(json.loads(memory.path.read_text(encoding="utf-8"))["examples"]) == 3001

def test_corrupt_primary_recovers_backup_without_empty_overwrite(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.1").write_text(json.dumps(valid_memory([valid_example(1)])), encoding="utf-8")
    memory = StyleMemory(path=path).load()
    assert len(memory.examples) == 1
    assert memory.load_result.recovered is True

def test_vector_hash_mismatch_rebuilds_index(tmp_path):
    memory = StyleMemory(path=tmp_path / "memory.json")
    memory.examples = [valid_example(1)]
    memory.vector_path.write_text('{"memory_hash":"wrong","items":[]}', encoding="utf-8")
    memory.load()
    assert json.loads(memory.vector_path.read_text(encoding="utf-8"))["memory_hash"] == memory.content_hash()
```

- [ ] **Step 2: Run and confirm current truncation/corruption behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "3000 or corrupt_primary or vector_hash" -v`

Expected: FAIL because current save slices to `[-max_examples:]`, corrupt load raises raw JSON errors and vector files have no authoritative memory hash.

- [ ] **Step 3: Replace direct JSON access and silent truncation**

```python
def save(self) -> None:
    document = {
        "schema_version": 2,
        "style_summary": self.style_summary,
        "examples": _dedupe_memory_examples(self.examples),
        "common_phrases": dict(self.common_phrases.most_common(120)),
    }
    atomic_write_json(self.path, document, validate_memory_document, backup_count=5)
    self.examples = list(document["examples"])
    self.rebuild_vector_index()
```

Add a SHA-256 `memory_hash` to the vector document. On load, rebuild if the vector is missing, corrupt, has a different schema version or has a different `memory_hash`. A failed rebuild blocks automatic sending but leaves the validated memory file intact.

- [ ] **Step 4: Run memory and vector suites**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "MemoryTests or memory or vector" -v`

Expected: PASS with 3,001 records preserved and recovery status observable.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash memory.py,tests\test_persistence.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\04-memory-vector.sha256.json -Encoding UTF8`

## Binding Global Constraints

- Source and EXE use the shared AppData path from Task 1.
- Memory JSON uses Task 2 validated atomic replacement and five valid backups.
- Corrupt memory recovers from a valid backup or raises a typed blocking error; it never creates an empty replacement.
- No hard 3,000-record truncation is allowed.
- The validated memory document is authoritative; missing/corrupt/stale vectors are rebuilt from it.
- A vector rebuild failure must be observable and must not corrupt or rewrite the validated memory document.
- Project is non-Git; do not initialize or commit.
- Strict TDD RED/GREEN evidence is required.
