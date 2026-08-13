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

## Binding Global Constraints

- Source and EXE runs must use `%APPDATA%\WeChatStyleReplyAssistant\`.
- `WECHAT_ASSISTANT_DATA_DIR` is allowed only as an explicit test/diagnostic override and is set by the isolated test harness.
- Existing formal data must not be read or written by tests.
- The repository is not Git-backed; do not initialize Git and do not commit. Use test evidence and SHA-256 checkpoint files.
- Follow strict TDD: add the test first, run it and capture the expected RED failure, then add minimal production code and capture GREEN.
