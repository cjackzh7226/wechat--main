### Task 2: Atomic JSON and Five-Version Recovery

**Files:**
- Create: `atomic_json.py`
- Test: `tests/test_persistence.py`

**Interfaces:**
- Produces: `JsonDataError`, `JsonLoadResult`, `load_json(path, validator) -> JsonLoadResult`, `atomic_write_json(path, data, validator, backup_count=5) -> None`.
- Consumes: validators with signature `Callable[[object], None]` that raise `ValueError` on invalid data.

- [ ] **Step 1: Write failing corruption and atomicity tests**

```python
def test_load_recovers_newest_valid_backup(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.1").write_text('{"examples": [1]}', encoding="utf-8")
    result = load_json(path, lambda value: None if isinstance(value, dict) else (_ for _ in ()).throw(ValueError()))
    assert result.data == {"examples": [1]}
    assert result.recovered is True

def test_failed_validation_does_not_replace_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 2}', encoding="utf-8")
    with pytest.raises(ValueError):
        atomic_write_json(path, {"config_version": "bad"}, reject_bad_version)
    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 2}
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "backup or validation" -v`

Expected: FAIL because atomic recovery helpers are absent.

- [ ] **Step 3: Implement validated load and atomic replace**

```python
@dataclass(frozen=True)
class JsonLoadResult:
    data: dict[str, object]
    source: Path
    recovered: bool

def atomic_write_json(path: Path, data: dict[str, object], validator, backup_count: int = 5) -> None:
    validator(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _rotate_backups(path, backup_count)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
```

`load_json` must try the primary, then `.bak.1` through `.bak.5`, validate every candidate, and raise `JsonDataError` containing only paths/reasons when none are valid.

- [ ] **Step 4: Run recovery, simulated replace-failure and backup-rotation tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "json or backup or atomic" -v`

Expected: PASS; a patched `os.replace` failure leaves the original primary readable.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash atomic_json.py,tests\test_persistence.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\02-atomic-json.sha256.json -Encoding UTF8`

## Binding Global Constraints

- Important JSON writes use validated atomic replacement and retain the five newest valid backups.
- Corrupt data is recovered from a valid backup or raises a blocking error; it is never replaced with empty data.
- The repository is not Git-backed; do not initialize Git and do not commit. Use test evidence and SHA-256 checkpoint files.
- Follow strict TDD: add failing behavior tests, capture expected RED, implement minimal production code, then capture GREEN.
