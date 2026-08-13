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

## Binding Global Constraints

- Runtime logs rotate at 2,000,000 bytes with five backups and never contain API keys, authorization values, full chat names, message text, reply text, or context.
- Settings/memory/vector typed startup failures force managed mode off and remain visible to the UI; they never create empty replacement files.
- Task 4 `StyleMemory.assert_ready_for_managed_reply()` must be consumed by the current managed automatic-send guard or a shared readiness helper used there; unhealthy vectors fail closed.
- Log initialization must be lazy and use the isolated/shared app path; importing `main.py` during tests must not create a real AppData log.
- Tests use an isolated data root and must leave formal AppData hashes/mtimes unchanged.
- Project is non-Git; do not initialize or commit.
- Strict TDD RED/GREEN evidence is required.
