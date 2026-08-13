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

Remove key fields from the JSON payload before `atomic_write_json`.

Remove free-generation and web-search UI choices; display the four managed modes and controlled-mode allowlist instead.

- [ ] **Step 4: Run settings, UI callback and credential round-trip tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or source_mode or credential or managed_mode" -v`

Expected: PASS; serialized settings contain neither `api_key`, `vision_api_key` nor `provider_api_keys`.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash secure_store.py,settings.py,float_ui.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\03-settings-credentials.sha256.json -Encoding UTF8`

## Binding Global Constraints

- Source and EXE use the shared AppData paths from Task 1.
- Important JSON writes use Task 2 atomic persistence and five valid backups.
- Formal settings expose only knowledge-based reply behavior; web search remains disabled.
- API keys are never serialized into settings JSON or logs.
- Legacy plaintext credentials are removed only after encrypted write and read-back succeed.
- Automatic sending must not be enabled by a legacy boolean migration; migrate it to observation mode.
- The repository is not Git-backed; do not initialize or commit.
- Follow strict TDD and include pristine RED/GREEN evidence.
