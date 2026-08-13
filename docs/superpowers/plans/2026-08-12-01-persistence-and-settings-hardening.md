# Persistence and Settings Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one recoverable AppData persistence layer, strict formal-use configuration, DPAPI key storage, and privacy-safe rotating logs.

**Architecture:** Add focused path, JSON persistence, credential, and logging modules; then adapt `settings.py` and `memory.py` to consume them. All components accept an environment-controlled isolated data root so automated tests never read or write the real user profile.

**Tech Stack:** Python 3.11.9, pathlib, dataclasses, json, tempfile/os.replace, pywin32 `win32crypt`, `logging.handlers.RotatingFileHandler`, unittest/pytest.

## Global Constraints

- Source and EXE runs must use `%APPDATA%\WeChatStyleReplyAssistant\`.
- `WECHAT_ASSISTANT_DATA_DIR` is allowed only as an explicit test/diagnostic override and is set by the isolated test harness.
- Important JSON writes use validated atomic replacement and retain the five newest valid backups.
- Corrupt data is recovered from a valid backup or raises a blocking error; it is never replaced with empty data.
- Long text is not truncated by persistence.
- Formal settings expose only knowledge-based reply behavior; web search defaults to and remains disabled.
- API keys are not serialized into settings JSON.
- The repository is not Git-backed; each task ends with tests and a SHA-256 checkpoint instead of a commit.

---

## File Structure

- Create `app_paths.py`: the sole resolver for config, memory, vector, credentials, ledger and log paths.
- Create `atomic_json.py`: validated JSON load/recovery and atomic writes with five backups.
- Create `secure_store.py`: DPAPI-protected provider-key persistence.
- Create `runtime_logging.py`: rotating redacted operational logger.
- Modify `settings.py`: versioned strict schema, bounded values, DPAPI integration, knowledge-only mode.
- Modify `memory.py`: unified path, recoverable load/save, no 3,000-record truncation, vector rebuild on mismatch.
- Modify `main.py`: surface blocking configuration/data errors and initialize redacted logging after paths resolve.
- Modify `float_ui.py`: remove free-generation/web-search choices from formal settings and expose `off/observe/controlled/full` managed mode.
- Create `tests/test_persistence.py`: focused path, JSON, credential, settings and recovery tests.
- Modify `tests/test_core.py`: update legacy settings/memory expectations to the formal contract.

### Task 1: Centralize AppData Paths and Test Isolation

**Files:**
- Create: `app_paths.py`
- Test: `tests/test_persistence.py`
- Modify: `settings.py:13-20`
- Modify: `memory.py:109-115`

**Interfaces:**
- Produces: `data_dir() -> Path`, `config_path() -> Path`, `memory_path() -> Path`, `vector_path() -> Path`, `credential_path() -> Path`, `ledger_path() -> Path`, `logs_dir() -> Path`.
- Consumes: environment variable `WECHAT_ASSISTANT_DATA_DIR` for isolated test runs.

- [ ] **Step 1: Write failing path tests**

```python
def test_source_and_frozen_use_same_explicit_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(tmp_path))
    assert app_paths.config_path() == tmp_path / "settings.json"
    assert app_paths.memory_path() == tmp_path / "memory.json"
    assert app_paths.vector_path() == tmp_path / "vector_memory.json"

def test_default_root_is_appdata_even_when_frozen(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_ASSISTANT_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_paths.data_dir() == tmp_path / "WeChatStyleReplyAssistant"
```

- [ ] **Step 2: Run the tests and confirm the old frozen-path behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "data_root" -v`

Expected: FAIL because `app_paths` does not exist and frozen mode currently points beside the EXE.

- [ ] **Step 3: Add the path resolver**

```python
APP_NAME = "WeChatStyleReplyAssistant"
TEST_OVERRIDE = "WECHAT_ASSISTANT_DATA_DIR"

def data_dir() -> Path:
    override = os.environ.get(TEST_OVERRIDE, "").strip()
    if override:
        return Path(override).resolve()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".wechat_style_reply_assistant"

def config_path() -> Path:
    return data_dir() / "settings.json"
```

Implement the remaining path functions as direct children of `data_dir()`. Replace both legacy `default_*_path()` implementations with calls to this module.

- [ ] **Step 4: Run focused and existing path tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "path or frozen or config" -v`

Expected: PASS, with all writes under the test override.

- [ ] **Step 5: Record the non-Git checkpoint**

Run: `Get-FileHash app_paths.py,settings.py,memory.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\01-paths.sha256.json -Encoding UTF8`

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

### Task 3: Strict Versioned Settings and DPAPI Credentials

**Files:**
- Create: `secure_store.py`
- Modify: `settings.py:23-160`
- Modify: `float_ui.py` settings controls and callbacks
- Test: `tests/test_persistence.py`
- Modify: `tests/test_core.py:2990-3090`

**Interfaces:**
- Produces: `CredentialStore.load() -> dict[str, str]`, `CredentialStore.save(keys: Mapping[str, str]) -> None`, `SettingsValidationError`, `AppSettings.validate() -> None`.
- Produces settings fields: `config_version=2`, `reply_source_mode="knowledge_only"`, `managed_mode: Literal["off","observe","controlled","full"]`, `managed_allowed_chats: list[str]`, `enable_web_search=False`.
- Consumes: `atomic_write_json`, `load_json`, `app_paths.config_path`, `app_paths.credential_path`.

- [ ] **Step 1: Write failing strict-type, bounds, key-redaction and mode tests**

```python
@pytest.mark.parametrize("field", ["managed_auto_reply", "auto_recognize", "training_mode"])
def test_string_false_is_rejected(field, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"config_version": 2, field: "false"}), encoding="utf-8")
    with pytest.raises(SettingsValidationError):
        AppSettings(config_path=path).load()

def test_save_excludes_plaintext_keys(tmp_path, fake_credential_store):
    settings = AppSettings(config_path=tmp_path / "settings.json", credential_store=fake_credential_store)
    settings.set_provider_api_key("deepseek", "secret-value")
    settings.save()
    saved = settings.config_path.read_text(encoding="utf-8")
    assert "secret-value" not in saved
    assert "provider_api_keys" not in saved

def test_formal_settings_forbid_generation_and_search():
    settings = AppSettings(reply_source_mode="model", enable_web_search=True)
    with pytest.raises(SettingsValidationError):
        settings.validate()
```

- [ ] **Step 2: Run and confirm unsafe coercion/plaintext behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or credential or string_false" -v`

Expected: FAIL on current `bool("false")`, plaintext key serialization, unbounded fields and free-generation settings.

- [ ] **Step 3: Implement DPAPI and strict settings schema**

```python
CONFIG_VERSION = 2
MANAGED_MODES = {"off", "observe", "controlled", "full"}

def require_bool(data: dict[str, object], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise SettingsValidationError(f"{key} 必须为布尔值")
    return value

def bounded_number(value: object, key: str, low: float, high: float) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise SettingsValidationError(f"{key} 必须为数字")
    number = float(value)
    if not low <= number <= high:
        raise SettingsValidationError(f"{key} 超出范围 {low}..{high}")
    return number
```

Use bounds: poll intervals `250..10000` ms, delay `0.5..10` seconds, cooldown `0.5..60` seconds, search results `1..6`, chat-area coordinates `0..1` with left < right and top < bottom. Migrate legacy `managed_auto_reply=true` to `managed_mode="observe"`, never directly to sending. Force `reply_source_mode="knowledge_only"` and `enable_web_search=False`.

`CredentialStore` stores one DPAPI blob in `credentials.dat` using `win32crypt.CryptProtectData` and `CryptUnprotectData`; tests inject reversible fake protect/unprotect callables. Its binary write also uses a same-directory temporary file, flush, `fsync`, `os.replace` and five-version backup rotation.

For a version-1 settings file, read legacy `provider_api_keys`, `api_key` and `vision_api_key` into memory, successfully write and read back the DPAPI credential blob, then atomically save the version-2 settings payload without those fields. If encryption or read-back fails, retain the original settings file unchanged and force `managed_mode="off"`. Build the persisted settings document from an explicit field allowlist rather than `asdict(self)`, so runtime-only `config_path` and `credential_store` objects cannot leak into JSON.

Remove free-generation and web-search UI choices; display the four managed modes and controlled-mode allowlist instead.

- [ ] **Step 4: Run settings, UI callback and credential round-trip tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or source_mode or credential or managed_mode" -v`

Expected: PASS; serialized settings contain neither `api_key`, `vision_api_key` nor `provider_api_keys`.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash secure_store.py,settings.py,float_ui.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\03-settings-credentials.sha256.json -Encoding UTF8`

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

### Task 5: Privacy-Safe Rotating Runtime Logs and Blocking Startup Errors

**Files:**
- Create: `runtime_logging.py`
- Modify: `main.py:53-78, 123-214, 1258-1270`
- Test: `tests/test_persistence.py`

**Interfaces:**
- Produces: `build_runtime_logger(log_dir: Path) -> logging.Logger`, `redact_event(event: Mapping[str, object]) -> dict[str, object]`.
- Consumes: `app_paths.logs_dir`, `SettingsValidationError`, `JsonDataError`.

- [ ] **Step 1: Write failing redaction, rotation and fail-closed startup tests**

```python
def test_runtime_event_redacts_private_values(tmp_path):
    logger = build_runtime_logger(tmp_path)
    logger.info("event", extra={"event_data": {"chat_name": "张三", "message": "身份证", "api_key": "secret", "record_id": "r1"}})
    text = next(tmp_path.glob("runtime.log*")).read_text(encoding="utf-8")
    assert "张三" not in text and "身份证" not in text and "secret" not in text
    assert "r1" in text

def test_corrupt_settings_leave_managed_mode_off(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text("broken", encoding="utf-8")
    result = load_startup_state_for_test()
    assert result.managed_mode == "off"
    assert "配置" in result.blocking_error
```

- [ ] **Step 2: Run and verify failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "runtime_event or managed_mode_off" -v`

Expected: FAIL because current `managed_send.log` records raw fragments, has no rotation and startup propagates unhandled JSON errors.

- [ ] **Step 3: Implement JSON-lines redaction and startup blocking state**

Use `RotatingFileHandler(maxBytes=2_000_000, backupCount=5, encoding="utf-8")`. Hash chat names and message content with an installation-local salt; retain event type, state, record ID, score, organization scope, error category and timestamps. Explicitly drop keys matching `api_key`, `authorization`, `message`, `reply`, `chat_name`, `context` unless one-session debug mode is enabled.

Wrap startup settings/memory load in typed exception handling. Put the UI in `managed_mode="off"`, display the recovery/blocking reason and continue only with manual diagnostics; do not create empty replacements.

- [ ] **Step 4: Run the persistence plan suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -v`

Expected: PASS and no files appear under the real `%APPDATA%\WeChatStyleReplyAssistant` during the test.

- [ ] **Step 5: Write phase evidence**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py --junitxml=artifacts\formal-hardening\2026-08-12\phase-01-persistence.xml`

Expected: exit code 0. Record hashes for `app_paths.py`, `atomic_json.py`, `secure_store.py`, `runtime_logging.py`, `settings.py`, `memory.py`, `float_ui.py`, and `main.py` in `phase-01-files.sha256.json`.
