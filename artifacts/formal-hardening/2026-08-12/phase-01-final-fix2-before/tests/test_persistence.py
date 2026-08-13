from collections import Counter
import hashlib
import importlib.util
import json
import logging.handlers
import math
import os
import subprocess
import sys
import threading
import time
import ast
from datetime import datetime
from pathlib import Path

import pytest

import app_paths
from atomic_json import JsonDataError, atomic_write_json, load_json
import memory as memory_module
from memory import StyleMemory
import settings as settings_module


def test_entire_suite_uses_session_data_root_isolation(request):
    assert "isolate_entire_test_suite_data_root" in request.fixturenames
    assert os.environ.get("WECHAT_ASSISTANT_DATA_DIR")


def test_full_data_directory_snapshot_includes_credentials_ledger_logs_salt_and_backups(tmp_path):
    from conftest import snapshot_data_directory

    expected_paths = {
        "credentials.dat",
        "credentials.dat.bak.1",
        "send_ledger.json",
        "logs/.runtime-salt",
        "logs/runtime.log",
        "settings.json",
        "memory.json",
        "vector_memory.json",
    }
    for relative in expected_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))

    manifest = snapshot_data_directory(tmp_path)

    assert {item["relative_path"] for item in manifest["files"]} == expected_paths
    assert all(set(item) == {"relative_path", "size", "sha256", "mtime_ns"} for item in manifest["files"])


class FakeCredentialStore:
    def __init__(self, initial=None, *, save_error=None, load_error=None):
        self.values = dict(initial or {})
        self.save_error = save_error
        self.load_error = load_error

    def load(self):
        if self.load_error is not None:
            raise self.load_error
        return dict(self.values)

    def save(self, keys):
        if self.save_error is not None:
            raise self.save_error
        self.values = dict(keys)


def test_runtime_event_redacts_private_values_and_keeps_audit_fields(tmp_path):
    assert importlib.util.find_spec("runtime_logging") is not None
    from runtime_logging import build_runtime_logger

    logger = build_runtime_logger(tmp_path)
    logger.info(
        "managed-send",
        extra={
            "event_data": {
                "chat_name": "张三",
                "message": "身份证号码",
                "reply": "私密回复",
                "context": "完整上下文",
                "api_key": "secret-key",
                "Authorization": "Bearer secret-token",
                "nested": {
                    "API_KEY": "nested-secret",
                    "authorization": "nested-token",
                },
                "record_id": "r1",
                "score": 91,
                "org_scope": "municipal",
                "state": "blocked",
                "error_category": "vector",
                "event": "managed-send",
                "timestamp": "2026-08-12T00:00:00+00:00",
            }
        },
    )
    line = (tmp_path / "runtime.log").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)

    for private_value in (
        "张三",
        "身份证号码",
        "私密回复",
        "完整上下文",
        "secret-key",
        "secret-token",
        "nested-secret",
        "nested-token",
    ):
        assert private_value not in line
    assert parsed | {"record_id": None, "timestamp": None} == {
        "error_category": "vector",
        "event": "managed-send",
        "org_scope": "municipal",
        "record_id": None,
        "score": 91,
        "state": "blocked",
        "timestamp": None,
    }
    assert parsed["record_id"].startswith("sha256:") and "r1" not in parsed["record_id"]
    assert parsed["timestamp"] != "2026-08-12T00:00:00+00:00"
    datetime.fromisoformat(parsed["timestamp"])


def test_runtime_event_drops_nested_private_and_credential_key_variants(tmp_path):
    from runtime_logging import build_runtime_logger

    logger = build_runtime_logger(tmp_path)
    logger.info(
        "nested-redaction",
        extra={
            "event_data": {
                "event": "nested-redaction",
                "state": {
                    "safe": "kept",
                    "ApiKey": "secret-one",
                    "provider_api_keys": "secret-two",
                    "proxy-Authorization": "secret-three",
                    "message": "private-message",
                    "message_text": "private-message-alias",
                    "customer_chat_name": "private-name-alias",
                    "reply_content": "private-reply-alias",
                    "request_context": "private-context-alias",
                    "nested": [
                        "unkeyed-private",
                        {
                            "AUTHORIZATION": "secret-four",
                            "chat_name": "private-name",
                            "safe_id": "kept-id",
                        }
                    ],
                },
            }
        },
    )
    text = (tmp_path / "runtime.log").read_text(encoding="utf-8")
    parsed = json.loads(text)

    for forbidden in (
        "secret-one",
        "secret-two",
        "secret-three",
        "secret-four",
        "private-message",
        "private-message-alias",
        "private-name",
        "private-name-alias",
        "private-reply-alias",
        "private-context-alias",
        "unkeyed-private",
        "apikey",
        "authorization",
    ):
        assert forbidden not in text.casefold()
    assert parsed == {"event": "nested-redaction", "timestamp": parsed["timestamp"]}


def test_runtime_event_uses_positive_schema_and_rejects_aliases_nested_values_and_field_smuggling(tmp_path):
    from runtime_logging import build_runtime_logger

    logger = build_runtime_logger(tmp_path)
    secrets = {
        "chat_title": "FULL-CHAT-NAME",
        "text": "FULL-MESSAGE",
        "answer": "FULL-REPLY",
        "access_token": "API-SECRET",
        "prompt": "FULL-PROMPT",
        "content": "FULL-CONTENT",
        "customer": "FULL-CUSTOMER",
        "partner": "FULL-PARTNER",
    }
    logger.info(
        "schema-probe",
        extra={
            "event_data": {
                **secrets,
                "record_id": {"text": "RECORD-ID-SECRET"},
                "state": ["STATE-SECRET", {"authorization": "Bearer STATE-TOKEN"}],
                "event": "Bearer EVENT-SECRET",
                "org_scope": {"customer": "ORG-SECRET"},
                "error_category": "ERROR CATEGORY SECRET",
                "score": 91.5,
                "timestamp": "2026-08-12T00:00:00+00:00",
            }
        },
    )
    line = (tmp_path / "runtime.log").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)

    for private_value in (*secrets.values(), "RECORD-ID-SECRET", "STATE-SECRET", "STATE-TOKEN", "EVENT-SECRET", "ORG-SECRET", "ERROR CATEGORY SECRET"):
        assert private_value not in line
    assert parsed["score"] == 91.5
    assert set(parsed) == {"score", "timestamp"}
    assert parsed["timestamp"] != "2026-08-12T00:00:00+00:00"
    datetime.fromisoformat(parsed["timestamp"])


@pytest.mark.parametrize("debug", [False, True])
def test_runtime_schema_rejects_legal_shape_smuggling_hashes_record_id_and_ignores_caller_timestamp(debug):
    from runtime_logging import redact_event

    salt = b"installation-local-test-salt-32b"
    event = {
        "event": "sk-secret-token",
        "state": "customer-message",
        "error_category": "access-token",
        "org_scope": "john.smith",
        "record_id": "123e4567-e89b-12d3-a456-426614174000",
        "score": 101,
        "timestamp": "2026-08-12T00:00:00+00:00",
        "api_key": "credential-secret",
        "Authorization": "Bearer credential-token",
        "nested": {"content": "private-content", "access_token": "nested-token"},
    }

    redacted = redact_event(event, salt=salt, debug=debug)
    serialized = json.dumps(redacted, ensure_ascii=False)

    for forbidden in (
        "sk-secret-token",
        "customer-message",
        "access-token",
        "john.smith",
        "123e4567-e89b-12d3-a456-426614174000",
        "2026-08-12T00:00:00+00:00",
        "credential-secret",
        "credential-token",
        "private-content",
        "nested-token",
    ):
        assert forbidden not in serialized
    assert redacted["record_id"].startswith("sha256:")
    assert len(redacted["record_id"]) == len("sha256:") + 64
    assert "score" not in redacted
    assert "timestamp" not in redacted


def test_runtime_logger_rotates_at_exact_limit_and_writes_parseable_jsonl(tmp_path):
    from runtime_logging import build_runtime_logger

    logger = build_runtime_logger(tmp_path)
    handlers = [handler for handler in logger.handlers if isinstance(handler, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler.maxBytes == 2_000_000
    assert handler.backupCount == 5

    for index in range(16_000):
        logger.info(
            "rotation-check",
            extra={
                "event_data": {
                    "event": "rotation-check",
                    "record_id": f"r{index}",
                    "state": "x" * 64,
                }
            },
        )
    handler.flush()

    log_files = sorted(tmp_path.glob("runtime.log*"))
    assert 2 <= len(log_files) <= 6
    parsed_lines = []
    for path in log_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed_lines.append(json.loads(line))
    hashed_record_ids = {item["record_id"] for item in parsed_lines}
    assert len(hashed_record_ids) == 16_000
    assert all(value.startswith("sha256:") and len(value) == len("sha256:") + 64 for value in hashed_record_ids)
    assert all(item.get("timestamp") for item in parsed_lines)


def test_runtime_logger_does_not_duplicate_handler_for_same_log_dir(tmp_path):
    from runtime_logging import build_runtime_logger

    first = build_runtime_logger(tmp_path)
    second = build_runtime_logger(tmp_path)

    assert first is second
    handlers = [handler for handler in first.handlers if isinstance(handler, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1


def test_runtime_logger_first_initialization_is_thread_safe_and_writes_event_once(tmp_path, monkeypatch):
    import runtime_logging as runtime_logging_module

    real_handler = runtime_logging_module.RotatingFileHandler

    class SlowHandler(real_handler):
        def __init__(self, *args, **kwargs):
            time.sleep(0.05)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(runtime_logging_module, "RotatingFileHandler", SlowHandler)
    barrier = threading.Barrier(8)
    results = []

    def build():
        barrier.wait()
        results.append(runtime_logging_module.build_runtime_logger(tmp_path))

    workers = [threading.Thread(target=build) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert all(not worker.is_alive() for worker in workers)
    logger = results[0]
    owned = [handler for handler in logger.handlers if getattr(handler, "_wechat_runtime_path", None)]
    assert len(owned) == 1
    assert logger.propagate is False

    logger.info("once", extra={"event_data": {"event": "concurrency-check", "record_id": "r1"}})
    owned[0].flush()
    lines = (tmp_path / "runtime.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_diag_logger_lazy_initialization_is_thread_safe(monkeypatch):
    import main as main_module

    calls = []
    events = []
    fake_logger = type("FakeLogger", (), {"info": lambda self, *args, **kwargs: events.append((args, kwargs))})()

    def slow_build(log_dir):
        time.sleep(0.05)
        calls.append(log_dir)
        return fake_logger

    main_module.DIAG_LOG = None
    monkeypatch.setattr(main_module, "build_runtime_logger", slow_build)
    barrier = threading.Barrier(8)

    def emit():
        barrier.wait()
        main_module._diag("concurrency-check")

    workers = [threading.Thread(target=emit) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert len(calls) == 1
    assert len(events) == 8


def test_all_production_diag_calls_use_explicit_enum_codes_and_remain_distinguishable(tmp_path, monkeypatch):
    import main as main_module
    from runtime_logging import build_runtime_logger

    expected_codes = {
        "vector_index_not_ready",
        "managed_preconditions_not_met",
        "api_failure",
        "latest_message_not_partner",
        "ledger_blocked",
        "ledger_turn_already_handled",
        "no_unreplied_message",
        "turn_already_handled",
        "send_already_pending",
        "no_candidate_selected",
        "send_queued",
        "preflight_checked",
        "preflight_rejected",
        "send_result",
    }
    tree = ast.parse(Path(main_module.__file__).read_text(encoding="utf-8"))
    diag_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_diag"
    ]
    assert diag_calls
    assert all(
        len(call.args) == 1 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
        for call in diag_calls
    )
    assert {call.args[0].value for call in diag_calls} == expected_codes

    logger = build_runtime_logger(tmp_path)
    main_module.DIAG_LOG = None
    monkeypatch.setattr(main_module, "_setup_diag_log", lambda: logger)
    for code in sorted(expected_codes):
        main_module._diag(code)
    for handler in logger.handlers:
        handler.flush()
    lines = [json.loads(line) for line in (tmp_path / "runtime.log").read_text(encoding="utf-8").splitlines()]

    assert {line["state"] for line in lines} == expected_codes
    assert all(line["event"] == "managed-send-diagnostic" and line["timestamp"] for line in lines)


def test_importing_main_does_not_create_runtime_log(tmp_path):
    isolated_root = tmp_path / "subprocess-app-data"
    isolated_appdata = tmp_path / "subprocess-formal-appdata"
    environment = os.environ.copy()
    environment["WECHAT_ASSISTANT_DATA_DIR"] = str(isolated_root)
    environment["APPDATA"] = str(isolated_appdata)

    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not (isolated_root / "managed_send.log").exists()
    assert not (isolated_root / "logs" / "runtime.log").exists()
    assert not (isolated_appdata / app_paths.APP_NAME / "managed_send.log").exists()


def test_one_session_debug_hashes_private_content_and_never_logs_credentials(tmp_path):
    from runtime_logging import build_runtime_logger, enable_session_debug

    enable_session_debug()
    try:
        logger = build_runtime_logger(tmp_path)
        logger.info(
            "debug-check",
            extra={
                "event_data": {
                    "event": "debug-check",
                    "chat_name": "张三",
                    "message": "原消息",
                    "reply": "原回复",
                    "context": "原上下文",
                    "ApiKey": "top-secret",
                    "nested": {"AUTHORIZATION": "Bearer nested-secret"},
                }
            },
        )
    finally:
        enable_session_debug(False)

    first = json.loads((tmp_path / "runtime.log").read_text(encoding="utf-8").splitlines()[0])
    for field in ("chat_name", "message", "reply", "context"):
        assert first[field].startswith("sha256:")
        assert len(first[field]) == len("sha256:") + 64
    serialized = json.dumps(first, ensure_ascii=False).casefold()
    for forbidden in ("张三", "原消息", "原回复", "原上下文", "top-secret", "nested-secret", "apikey", "authorization"):
        assert forbidden.casefold() not in serialized
    assert (tmp_path / ".runtime-salt").read_bytes()

    logger.info(
        "debug-disabled",
        extra={"event_data": {"event": "debug-disabled", "message": "second-private"}},
    )
    second = json.loads((tmp_path / "runtime.log").read_text(encoding="utf-8").splitlines()[1])
    assert second["event"] == "debug-disabled"
    assert second["timestamp"]
    assert set(second) == {"event", "timestamp"}
    assert not (tmp_path / "session-debug-enabled").exists()


@pytest.mark.parametrize(
    ("settings_payload", "expected_error_type"),
    [
        (b"broken-json", JsonDataError),
        (json.dumps({"config_version": 2, "managed_mode": "invalid"}).encode("utf-8"), settings_module.SettingsValidationError),
    ],
)
def test_settings_typed_startup_error_returns_visible_managed_off_state_without_replacement(
    tmp_path,
    monkeypatch,
    settings_payload,
    expected_error_type,
):
    from main import load_startup_state_for_test

    data_root = tmp_path / "startup-settings"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    settings_path = data_root / "settings.json"
    settings_path.write_bytes(settings_payload)

    state = load_startup_state_for_test()

    assert state.managed_mode == "off"
    assert state.settings.managed_mode == "off"
    assert state.settings.managed_auto_reply is False
    assert "配置" in state.blocking_error
    assert "手动诊断" in state.blocking_error
    assert state.error_type is expected_error_type
    assert settings_path.read_bytes() == settings_payload
    assert not (data_root / "memory.json").exists()
    assert not (data_root / "vector_memory.json").exists()


@pytest.mark.parametrize("version", [1, 2])
def test_corrupt_credentials_in_v1_or_v2_produce_visible_typed_startup_block_without_byte_changes(
    tmp_path,
    monkeypatch,
    version,
):
    from main import load_startup_state_for_test

    data_root = tmp_path / f"credential-v{version}"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    if version == 1:
        settings_payload = {"config_version": 1, "managed_auto_reply": True, "api_key": "legacy-secret"}
    else:
        settings_payload = {"config_version": 2, "managed_mode": "full"}
    settings_bytes = json.dumps(settings_payload).encode("utf-8")
    credential_bytes = b"not-a-valid-dpapi-credential"
    settings_path = data_root / "settings.json"
    credential_path = data_root / "credentials.dat"
    settings_path.write_bytes(settings_bytes)
    credential_path.write_bytes(credential_bytes)

    state = load_startup_state_for_test()

    assert state.managed_mode == "off"
    assert state.error_type is not None
    assert state.error_type.__name__ == "SettingsPersistenceError"
    assert "凭据" in state.blocking_error and "手动诊断" in state.blocking_error
    assert settings_path.read_bytes() == settings_bytes
    assert credential_path.read_bytes() == credential_bytes


def test_illegal_utf8_settings_produce_visible_typed_startup_block_without_replacement(tmp_path, monkeypatch):
    from main import load_startup_state_for_test

    data_root = tmp_path / "invalid-utf8"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    settings_path = data_root / "settings.json"
    original = b"\xff\xfe\xfa"
    settings_path.write_bytes(original)

    state = load_startup_state_for_test()

    assert state.error_type is not None
    assert state.error_type.__name__ == "SettingsPersistenceError"
    assert "配置" in state.blocking_error and "手动诊断" in state.blocking_error
    assert settings_path.read_bytes() == original


def test_settings_read_oserror_produces_visible_typed_startup_block(tmp_path, monkeypatch):
    from main import load_startup_state_for_test

    data_root = tmp_path / "settings-io"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    settings_path = data_root / "settings.json"
    original = json.dumps({"config_version": 2, "managed_mode": "full"}).encode("utf-8")
    settings_path.write_bytes(original)

    def denied_read(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(settings_module, "load_json", denied_read)
    state = load_startup_state_for_test()

    assert state.error_type is not None
    assert state.error_type.__name__ == "SettingsPersistenceError"
    assert "配置" in state.blocking_error and "手动诊断" in state.blocking_error
    assert settings_path.read_bytes() == original


def test_memory_json_startup_error_returns_visible_managed_off_state_without_replacement(tmp_path, monkeypatch):
    from main import load_startup_state_for_test

    data_root = tmp_path / "startup-memory"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    settings_payload = {"config_version": 2, "managed_mode": "full"}
    (data_root / "settings.json").write_text(json.dumps(settings_payload), encoding="utf-8")
    memory_path = data_root / "memory.json"
    memory_payload = b"broken-memory"
    memory_path.write_bytes(memory_payload)

    state = load_startup_state_for_test()

    assert state.managed_mode == "off"
    assert state.settings.managed_mode == "off"
    assert state.settings.managed_auto_reply is False
    assert "记忆" in state.blocking_error
    assert "手动诊断" in state.blocking_error
    assert state.error_type is JsonDataError
    assert memory_path.read_bytes() == memory_payload
    assert not (data_root / "vector_memory.json").exists()


def test_vector_startup_error_returns_visible_managed_off_state_without_replacement(tmp_path, monkeypatch):
    from main import load_startup_state_for_test
    from memory import VectorIndexError

    data_root = tmp_path / "startup-vector"
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(data_root))
    data_root.mkdir(parents=True)
    (data_root / "settings.json").write_text(
        json.dumps({"config_version": 2, "managed_mode": "full"}),
        encoding="utf-8",
    )
    memory_path = data_root / "memory.json"
    memory_payload = json.dumps(_valid_memory([_valid_example(1)]), ensure_ascii=False).encode("utf-8")
    memory_path.write_bytes(memory_payload)
    real_atomic_write_json = memory_module.atomic_write_json

    def fail_vector_write(destination, data, validator, backup_count=5):
        if Path(destination) == data_root / "vector_memory.json":
            raise OSError("simulated vector failure")
        return real_atomic_write_json(destination, data, validator, backup_count)

    monkeypatch.setattr(memory_module, "atomic_write_json", fail_vector_write)

    state = load_startup_state_for_test()

    assert state.managed_mode == "off"
    assert state.settings.managed_mode == "off"
    assert state.settings.managed_auto_reply is False
    assert "向量" in state.blocking_error
    assert "手动诊断" in state.blocking_error
    assert state.error_type is VectorIndexError
    assert memory_path.read_bytes() == memory_payload
    assert not (data_root / "vector_memory.json").exists()


def test_blocking_startup_enters_manual_ui_without_starting_background_monitor(monkeypatch):
    import main as main_module

    class ManualDiagnosticUI:
        def __init__(self):
            self.statuses = []
            self.mainloop_calls = 0

        def set_status(self, text):
            self.statuses.append(text)

        def mainloop(self):
            self.mainloop_calls += 1

    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings_module.AppSettings()
    app.startup_blocking_error = "配置读取失败，已关闭托管模式；请使用手动诊断。"
    app.ui = ManualDiagnosticUI()
    app.running = True

    def fail_if_background_monitor_starts(*args, **kwargs):
        raise AssertionError("blocking startup must not start the background monitor")

    monkeypatch.setattr(main_module.threading, "Thread", fail_if_background_monitor_starts)

    app.start()

    assert app.ui.statuses == [app.startup_blocking_error]
    assert app.ui.mainloop_calls == 1
    assert app.running is False


def test_managed_send_blocks_before_sender_when_vector_index_is_not_ready(tmp_path, monkeypatch):
    import main as main_module

    class RecordingQueue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    class RecordingSender:
        def __init__(self):
            self.calls = []

        def send(self, window, text):
            self.calls.append((window, text))
            return True, "sent"

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    settings = settings_module.AppSettings()
    settings.managed_mode = "full"
    settings.auto_reply_delay_seconds = 0
    memory = StyleMemory(path=tmp_path / "memory.json")
    memory.vector_ready = False
    memory.last_vector_error = "vector index is invalid"
    window = type("Window", (), {"title": "customer"})()
    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings
    app.memory = memory
    app.current_window = window
    app.current_context = "[对方] 请问如何办理"
    app.engine = type("Engine", (), {"last_error": "", "last_source": "knowledge"})()
    app.sender = RecordingSender()
    app.queue = RecordingQueue()
    app.detector = type("Detector", (), {"foreground_chat": lambda self: window})()
    app._managed_preflight_context = lambda current_window: app.current_context
    app.last_auto_reply_signature = ""
    app.pending_auto_reply_signature = ""
    app.handled_auto_reply_signatures = set()
    app.managed_send_inflight = False
    app.managed_waiting_for_own_echo = False
    app.managed_last_handled_turn_messages = []
    app.managed_last_handled_turn_key = ""
    app.managed_last_handled_partner = ""
    monkeypatch.setattr(main_module.threading, "Thread", InlineThread)

    app._maybe_managed_send(app.current_context, ["请到服务窗口办理"])

    assert app.sender.calls == []
    assert app.settings.managed_mode == "off"
    assert app.settings.managed_auto_reply is False
    status_messages = [str(item[1]) for item in app.queue.items if item[0] == "status"]
    assert any("向量" in message and "关闭" in message for message in status_messages)


def test_group_managed_send_blocks_before_sender_when_vector_index_is_not_ready(tmp_path, monkeypatch):
    import main as main_module

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    settings = settings_module.AppSettings()
    settings.managed_mode = "full"
    memory = StyleMemory(path=tmp_path / "memory.json")
    memory.vector_ready = False
    memory.last_vector_error = "vector index is invalid"
    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings
    app.memory = memory
    app.startup_blocking_error = ""
    app.sender = type(
        "Sender",
        (),
        {
            "calls": [],
            "send": lambda self, window, text: self.calls.append((window, text)) or (True, "sent"),
        },
    )()
    app.queue = type("Queue", (), {"items": [], "put": lambda self, item: self.items.append(item)})()
    monkeypatch.setattr(main_module.threading, "Thread", InlineThread)

    app._send_group_reply(type("Window", (), {})(), "群聊回复", "群聊问题")

    assert app.sender.calls == []
    assert app.settings.managed_mode == "off"
    status_messages = [str(item[1]) for item in app.queue.items if item[0] == "status"]
    assert any("向量" in message and "关闭" in message for message in status_messages)


@pytest.mark.parametrize("path", ["personal", "group"])
@pytest.mark.parametrize("transition", ["off", "vector_invalid", "memory_missing"])
def test_queued_managed_workers_recheck_full_mode_memory_and_vector_immediately_before_send(
    tmp_path,
    monkeypatch,
    path,
    transition,
):
    import main as main_module

    pending = []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            pending.append(self.target)

    class RecordingSender:
        def __init__(self):
            self.calls = []

        def send(self, window, text):
            self.calls.append((window, text))
            return True, "sent"

    window = type("Window", (), {"title": "customer"})()
    settings = settings_module.AppSettings()
    settings.managed_mode = "full"
    settings.auto_reply_delay_seconds = 0
    memory = StyleMemory(path=tmp_path / "memory.json")
    memory.vector_ready = True
    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings
    app.memory = memory
    app.startup_blocking_error = ""
    app.send_ledger = main_module.SendLedger(tmp_path / "send_ledger.json").load()
    app.sender = RecordingSender()
    app.queue = type("Queue", (), {"items": [], "put": lambda self, item: self.items.append(item)})()
    app.current_window = window
    app.current_context = "[对方] 请问如何办理"
    app.engine = type("Engine", (), {"last_error": "", "last_source": "knowledge"})()
    app.detector = type("Detector", (), {})()
    app._managed_preflight_context = lambda current_window: app.current_context
    app.last_auto_reply_signature = ""
    app.pending_auto_reply_signature = ""
    app.handled_auto_reply_signatures = set()
    app.managed_send_inflight = False
    app.managed_waiting_for_own_echo = False
    app.managed_last_handled_turn_messages = []
    app.managed_last_handled_turn_key = ""
    app.managed_last_handled_partner = ""
    monkeypatch.setattr(main_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(main_module, "_managed_live_send_window", lambda detector, expected: expected)

    if path == "personal":
        app._maybe_managed_send(app.current_context, ["请到服务窗口办理"])
    else:
        app._send_group_reply(window, "群聊回复", "群聊问题")
    assert len(pending) == 1

    if transition == "off":
        settings.managed_mode = "off"
    elif transition == "vector_invalid":
        memory.vector_ready = False
        memory.last_vector_error = "vector invalid after queue"
    else:
        app.memory = None
    pending.pop()()

    assert app.sender.calls == []


def test_blocking_startup_rejects_managed_callback_without_overwriting_corrupt_settings_or_starting_thread(
    tmp_path,
    monkeypatch,
):
    import main as main_module

    path = tmp_path / "settings.json"
    original = b"broken-settings"
    path.write_bytes(original)
    settings = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore())
    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings
    app.startup_blocking_error = "配置读取失败，已关闭托管模式；请使用手动诊断。"
    app.ui = type("UI", (), {"statuses": [], "set_status": lambda self, text: self.statuses.append(text)})()
    app._target_wechat_window = lambda: (_ for _ in ()).throw(
        AssertionError("blocking startup must not inspect a send target")
    )

    def fail_if_thread_starts(*args, **kwargs):
        raise AssertionError("blocking startup must not start a managed thread")

    monkeypatch.setattr(main_module.threading, "Thread", fail_if_thread_starts)

    app._managed_changed(True)

    assert path.read_bytes() == original
    assert settings.managed_mode == "off"
    assert settings.managed_auto_reply is False
    assert app.ui.statuses == [app.startup_blocking_error]


@pytest.mark.parametrize("callback_name", ["_auto_changed", "_training_changed"])
def test_blocking_startup_rejects_auto_and_training_ui_save_before_callback(
    tmp_path,
    monkeypatch,
    callback_name,
):
    import main as main_module

    path = tmp_path / "settings.json"
    original = b"broken-settings"
    path.write_bytes(original)
    settings = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore())
    app = main_module.WeChatStyleReplyAssistant.__new__(main_module.WeChatStyleReplyAssistant)
    app.settings = settings
    app.startup_blocking_error = "配置读取失败，已关闭托管模式；请使用手动诊断。"
    app.ui = type(
        "UI",
        (),
        {
            "statuses": [],
            "set_status": lambda self, text: self.statuses.append(text),
            "auto_var": type("Var", (), {"set": lambda self, value: setattr(self, "value", value)})(),
            "training_var": type("Var", (), {"set": lambda self, value: setattr(self, "value", value)})(),
        },
    )()
    app._target_wechat_window = lambda: (_ for _ in ()).throw(
        AssertionError("blocking startup must not inspect a send target")
    )

    def fail_if_thread_starts(*args, **kwargs):
        raise AssertionError("blocking startup must not start an automatic thread")

    monkeypatch.setattr(main_module.threading, "Thread", fail_if_thread_starts)
    app._install_blocking_startup_guards()

    if callback_name == "_auto_changed":
        settings.auto_recognize = True
    else:
        settings.training_mode = True
    settings.save()
    getattr(app, callback_name)(True)

    assert path.read_bytes() == original
    assert settings.managed_mode == "off"
    assert settings.auto_recognize is False
    assert settings.training_mode is False
    assert app.ui.statuses == [app.startup_blocking_error]


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


def _require_examples_dict(value):
    if not isinstance(value, dict) or "examples" not in value:
        raise ValueError("examples must be a dictionary field")


def _require_numeric_config_version(value):
    if not isinstance(value, dict) or not isinstance(value.get("config_version"), int):
        raise ValueError("config_version must be an integer")


@pytest.fixture
def isolated_memory_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(tmp_path))
    return tmp_path


def _valid_example(index, *, reply=None, organization_type="association"):
    return {
        "partner": f"partner-{index}",
        "cue": f"[other] question-{index}",
        "reply": reply or f"answer-{index}",
        "source": "manual_edit",
        "created_at": "2026-08-12T00:00:00+00:00",
        "conversation_hash": f"hash-{index}",
        "organization_type": organization_type,
    }


def _valid_memory(examples):
    return {
        "schema_version": 2,
        "style_summary": "short and direct",
        "examples": list(examples),
        "common_phrases": {"received": 2},
    }


def test_save_does_not_truncate_more_than_3000_records(isolated_memory_root):
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    memory.examples = [_valid_example(index) for index in range(3001)]

    memory.save()
    loaded = StyleMemory(path=memory.path).load()

    assert len(json.loads(memory.path.read_text(encoding="utf-8"))["examples"]) == 3001
    assert len(loaded.examples) == 3001


def test_corrupt_primary_recovers_backup_without_empty_overwrite(isolated_memory_root):
    path = isolated_memory_root / "memory.json"
    corrupt_primary = b"broken-primary"
    path.write_bytes(corrupt_primary)
    (isolated_memory_root / "memory.json.bak.1").write_text(
        json.dumps(_valid_memory([_valid_example(1)])),
        encoding="utf-8",
    )

    memory = StyleMemory(path=path).load()

    assert len(memory.examples) == 1
    assert memory.load_result is not None
    assert memory.load_result.recovered is True
    assert memory.load_result.source == isolated_memory_root / "memory.json.bak.1"
    assert path.read_bytes() == corrupt_primary


def test_missing_primary_recovers_valid_backup(isolated_memory_root):
    path = isolated_memory_root / "memory.json"
    backup_path = isolated_memory_root / "memory.json.bak.1"
    backup_path.write_text(
        json.dumps(_valid_memory([_valid_example(7)])),
        encoding="utf-8",
    )

    memory = StyleMemory(path=path).load()

    assert memory.load_result is not None
    assert memory.load_result.recovered is True
    assert memory.load_result.source == backup_path
    assert [item["conversation_hash"] for item in memory.examples] == ["hash-7"]
    assert not path.exists()


def test_missing_primary_with_only_invalid_backups_raises_without_byte_changes(isolated_memory_root):
    path = isolated_memory_root / "memory.json"
    backups = [isolated_memory_root / f"memory.json.bak.{index}" for index in range(1, 6)]
    for index, backup in enumerate(backups, start=1):
        backup.write_bytes(f"broken-backup-{index}".encode("utf-8"))
    before = {backup: backup.read_bytes() for backup in backups}

    with pytest.raises(JsonDataError):
        StyleMemory(path=path).load()

    assert not path.exists()
    assert {backup: backup.read_bytes() for backup in backups} == before


@pytest.mark.parametrize(
    "invalid_primary",
    [
        {},
        {"style_summary": "incomplete", "examples": []},
        {
            "schema_version": 2,
            "style_summary": "invalid empty reply",
            "examples": [
                {
                    "cue": "[other] question",
                    "reply": "   ",
                    "source": "manual_edit",
                    "conversation_hash": "empty-reply",
                }
            ],
            "common_phrases": {},
        },
        {
            "style_summary": "legacy partial item",
            "examples": [{"cue": "[other] question", "reply": "answer"}],
            "common_phrases": {},
        },
    ],
    ids=["empty-object", "missing-core-field", "empty-reply", "partial-v1-item"],
)
def test_invalid_primary_candidate_cannot_preempt_valid_backup(
    invalid_primary,
    isolated_memory_root,
):
    path = isolated_memory_root / "memory.json"
    path.write_text(json.dumps(invalid_primary), encoding="utf-8")
    backup_path = isolated_memory_root / "memory.json.bak.1"
    backup_path.write_text(
        json.dumps(_valid_memory([_valid_example(9)])),
        encoding="utf-8",
    )

    memory = StyleMemory(path=path).load()

    assert memory.load_result is not None
    assert memory.load_result.recovered is True
    assert memory.load_result.source == backup_path
    assert [item["conversation_hash"] for item in memory.examples] == ["hash-9"]


def test_all_corrupt_memory_candidates_raise_typed_error_without_byte_changes(isolated_memory_root):
    path = isolated_memory_root / "memory.json"
    candidates = [path, *(isolated_memory_root / f"memory.json.bak.{index}" for index in range(1, 6))]
    payloads = [
        b"{broken-primary",
        b"[]",
        b'{"schema_version":"2","examples":[]}',
        b'{"schema_version":2,"style_summary":[],"examples":[],"common_phrases":{}}',
        b'{"schema_version":2,"style_summary":"ok","examples":{},"common_phrases":{}}',
        b'{"schema_version":2,"style_summary":"ok","examples":[],"common_phrases":[]}',
    ]
    for candidate, payload in zip(candidates, payloads):
        candidate.write_bytes(payload)
    before = {candidate: candidate.read_bytes() for candidate in candidates}

    with pytest.raises(JsonDataError):
        StyleMemory(path=path).load()

    assert {candidate: candidate.read_bytes() for candidate in candidates} == before


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", "2"),
        ("style_summary", []),
        ("examples", {}),
        ("common_phrases", []),
    ],
)
def test_memory_validator_rejects_wrong_top_level_field_types(field, invalid_value):
    document = _valid_memory([_valid_example(1)])
    document[field] = invalid_value

    with pytest.raises(ValueError):
        memory_module.validate_memory_document(document)


@pytest.mark.parametrize(
    "field",
    [
        "partner",
        "cue",
        "reply",
        "source",
        "created_at",
        "conversation_hash",
        "organization_type",
    ],
)
def test_memory_validator_rejects_non_string_example_fields(field):
    example = _valid_example(1)
    example[field] = 123

    with pytest.raises(ValueError):
        memory_module.validate_memory_document(_valid_memory([example]))


@pytest.mark.parametrize(
    "phrases",
    [
        {1: 2},
        {"received": "2"},
        {"received": True},
        {"received": -1},
    ],
)
def test_memory_validator_requires_non_negative_integer_phrase_counts(phrases):
    document = _valid_memory([_valid_example(1)])
    document["common_phrases"] = phrases

    with pytest.raises(ValueError):
        memory_module.validate_memory_document(document)


@pytest.mark.parametrize("state", ["missing", "corrupt", "schema_mismatch", "hash_mismatch"])
def test_vector_hash_and_other_invalid_states_rebuild_from_memory(
    state,
    isolated_memory_root,
):
    path = isolated_memory_root / "memory.json"
    path.write_text(json.dumps(_valid_memory([_valid_example(1)])), encoding="utf-8")
    vector_path = isolated_memory_root / "vector_memory.json"
    if state == "corrupt":
        vector_path.write_text("{broken-vector", encoding="utf-8")
    elif state == "schema_mismatch":
        vector_path.write_text(
            json.dumps({"version": 999, "dim": 384, "memory_hash": "0" * 64, "items": []}),
            encoding="utf-8",
        )
    elif state == "hash_mismatch":
        vector_path.write_text(
            json.dumps({"version": 1, "dim": 384, "memory_hash": "0" * 64, "items": []}),
            encoding="utf-8",
        )

    memory = StyleMemory(path=path).load()
    rebuilt = json.loads(vector_path.read_text(encoding="utf-8"))

    assert rebuilt["version"] == 1
    assert rebuilt["memory_hash"] == memory.content_hash()
    assert len(rebuilt["items"]) == 1


def test_vector_write_failure_is_observable_and_preserves_memory_hash(
    isolated_memory_root,
    monkeypatch,
):
    path = isolated_memory_root / "memory.json"
    path.write_text(json.dumps(_valid_memory([_valid_example(1)])), encoding="utf-8")
    vector_path = isolated_memory_root / "vector_memory.json"
    vector_path.write_text("{broken-vector", encoding="utf-8")
    memory_hash_before = hashlib.sha256(path.read_bytes()).hexdigest()
    real_atomic_write_json = atomic_write_json

    def fail_vector_write(destination, data, validator, backup_count=5):
        if Path(destination) == vector_path:
            raise OSError("simulated vector write failure")
        return real_atomic_write_json(destination, data, validator, backup_count)

    monkeypatch.setattr(memory_module, "atomic_write_json", fail_vector_write, raising=False)

    memory = StyleMemory(path=path)
    with pytest.raises(memory_module.VectorIndexError) as error:
        memory.load()

    assert hashlib.sha256(path.read_bytes()).hexdigest() == memory_hash_before
    assert isinstance(error.value.__cause__, OSError)
    assert memory.vector_ready is False
    assert memory.last_vector_error
    with pytest.raises(memory_module.VectorIndexError):
        memory.assert_ready_for_managed_reply()


def test_runtime_save_vector_failure_blocks_until_successful_rebuild(
    isolated_memory_root,
    monkeypatch,
):
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    memory.examples = [_valid_example(1)]
    memory.save()
    memory.examples.append(_valid_example(2))
    real_atomic_write_json = memory_module.atomic_write_json

    def fail_vector_write(destination, data, validator, backup_count=5):
        if Path(destination) == memory.vector_path:
            raise OSError("sensitive customer payload at C:\\private\\memory")
        return real_atomic_write_json(destination, data, validator, backup_count)

    with monkeypatch.context() as scoped:
        scoped.setattr(memory_module, "atomic_write_json", fail_vector_write)
        with pytest.raises(memory_module.VectorIndexError):
            memory.save()

    persisted_after_failure = memory.path.read_bytes()
    assert len(json.loads(persisted_after_failure)["examples"]) == 2
    assert memory.vector_ready is False
    assert memory.last_vector_error is not None
    assert "OSError" in memory.last_vector_error
    assert "customer" not in memory.last_vector_error
    assert "private" not in memory.last_vector_error
    with pytest.raises(memory_module.VectorIndexError):
        memory.assert_ready_for_managed_reply()

    memory.rebuild_vector_index()

    assert memory.path.read_bytes() == persisted_after_failure
    assert memory.vector_ready is True
    assert memory.last_vector_error is None
    memory.assert_ready_for_managed_reply()


@pytest.mark.parametrize(
    "corruption",
    [
        "nan-weight",
        "infinite-weight",
        "weight-out-of-range",
        "bucket-out-of-range",
        "noncanonical-bucket",
        "empty-vector",
        "record-mismatch",
    ],
)
def test_invalid_vector_payload_with_current_memory_hash_is_rebuilt(
    corruption,
    isolated_memory_root,
):
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    memory.examples = [_valid_example(1)]
    memory.save()
    vector_path = memory.vector_path
    vector_document = json.loads(vector_path.read_text(encoding="utf-8"))
    expected_items = json.loads(json.dumps(vector_document["items"]))
    item = vector_document["items"][0]
    bucket = next(iter(item["vector"]))
    if corruption == "nan-weight":
        item["vector"][bucket] = float("nan")
    elif corruption == "infinite-weight":
        item["vector"][bucket] = float("inf")
    elif corruption == "weight-out-of-range":
        item["vector"][bucket] = 1.5
    elif corruption == "bucket-out-of-range":
        item["vector"] = {"384": 0.5}
    elif corruption == "noncanonical-bucket":
        item["vector"] = {"01": 0.5}
    elif corruption == "empty-vector":
        item["vector"] = {}
    else:
        item["reply"] = "tampered reply"
    vector_path.write_text(json.dumps(vector_document), encoding="utf-8")

    loaded = StyleMemory(path=memory.path).load()
    rebuilt = json.loads(vector_path.read_text(encoding="utf-8"))

    assert rebuilt["items"] == expected_items
    assert loaded.vector_ready is True
    assert loaded.last_vector_error is None
    for rebuilt_item in rebuilt["items"]:
        assert rebuilt_item["vector"]
        assert all(
            bucket_key == str(int(bucket_key)) and 0 <= int(bucket_key) < 384
            for bucket_key in rebuilt_item["vector"]
        )
        assert all(
            math.isfinite(weight) and 0 < weight <= 1
            for weight in rebuilt_item["vector"].values()
        )


@pytest.mark.parametrize("reply", ["x", "中"], ids=["ascii", "chinese"])
def test_single_character_examples_always_have_stable_vector_items(
    reply,
    isolated_memory_root,
):
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    example = _valid_example(1, reply=reply)
    example["cue"] = ""
    example["conversation_hash"] = f"single-{ord(reply)}"
    memory.examples = [example]

    memory.save()
    built_items = memory._build_vector_items()
    vector_document = json.loads(memory.vector_path.read_text(encoding="utf-8"))

    assert len(built_items) == len(memory.examples) == 1
    assert built_items[0]["vector"]
    assert vector_document["items"] == built_items
    assert memory_module._text_vector(reply) == built_items[0]["vector"]
    assert memory.vector_ready is True
    memory.assert_ready_for_managed_reply()

    vector_backup = memory.vector_path.with_name(f"{memory.vector_path.name}.bak.1")
    assert not vector_backup.exists()
    first_reload = StyleMemory(path=memory.path).load()
    second_reload = StyleMemory(path=memory.path).load()

    assert first_reload.vector_ready is True
    assert second_reload.vector_ready is True
    assert not vector_backup.exists()


def test_content_hash_uses_stable_canonical_json(isolated_memory_root):
    example = _valid_example(1)
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    memory.style_summary = "stable summary"
    memory.examples = [dict(reversed(list(example.items())))]
    memory.common_phrases = Counter({"second": 1, "first": 2})
    canonical_document = {
        "schema_version": 2,
        "style_summary": "stable summary",
        "examples": [example],
        "common_phrases": {"first": 2, "second": 1},
    }
    expected = hashlib.sha256(
        json.dumps(
            canonical_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert memory.content_hash() == expected


def test_legacy_memory_without_created_at_keeps_stable_hash(isolated_memory_root, monkeypatch):
    path = isolated_memory_root / "memory.json"
    legacy_example = _valid_example(1)
    legacy_example.pop("created_at")
    legacy_document = _valid_memory([legacy_example])
    legacy_document.pop("schema_version")
    path.write_text(json.dumps(legacy_document), encoding="utf-8")
    ticks = iter(f"generated-{index}" for index in range(20))
    monkeypatch.setattr(memory_module, "_utc_now", lambda: next(ticks))

    first_hash = StyleMemory(path=path).load().content_hash()
    second_hash = StyleMemory(path=path).load().content_hash()

    assert first_hash == second_hash


def test_dedupe_preserves_different_answers_sharing_conversation_hash(isolated_memory_root):
    first = _valid_example(1, reply="answer for associations", organization_type="association")
    second = _valid_example(2, reply="answer for foundations", organization_type="foundation")
    first["conversation_hash"] = second["conversation_hash"] = "shared-conversation"
    memory = StyleMemory(path=isolated_memory_root / "memory.json")
    memory.examples = [first, second]

    memory.save()

    assert [item["reply"] for item in StyleMemory(path=memory.path).load().examples] == [
        "answer for associations",
        "answer for foundations",
    ]


def test_missing_memory_returns_empty_without_creating_primary_or_vector(isolated_memory_root):
    memory = StyleMemory(path=isolated_memory_root / "memory.json").load()

    assert memory.examples == []
    assert memory.load_result is None
    assert not memory.path.exists()
    assert not memory.vector_path.exists()


def test_load_recovers_newest_valid_backup(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.1").write_text("{broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.2").write_text('{"examples": [1]}', encoding="utf-8")
    (tmp_path / "memory.json.bak.3").write_text('{"examples": [2]}', encoding="utf-8")

    result = load_json(path, _require_examples_dict)

    assert result.data == {"examples": [1]}
    assert result.source == tmp_path / "memory.json.bak.2"
    assert result.recovered is True


def test_atomic_write_failed_validation_does_not_replace_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 2}', encoding="utf-8")

    with pytest.raises(ValueError):
        atomic_write_json(path, {"config_version": "bad"}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 2}


def test_atomic_write_rotates_five_backups(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 0}', encoding="utf-8")
    for index in range(1, 6):
        (tmp_path / f"settings.json.bak.{index}").write_text(
            json.dumps({"config_version": index}), encoding="utf-8"
        )

    atomic_write_json(path, {"config_version": 6}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 6}
    assert [
        json.loads((tmp_path / f"settings.json.bak.{index}").read_text(encoding="utf-8"))
        for index in range(1, 6)
    ] == [{"config_version": value} for value in range(0, 5)]


def test_atomic_write_retains_older_valid_backup_when_newer_slots_are_corrupt(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 0}', encoding="utf-8")
    (tmp_path / "settings.json.bak.1").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.2").write_text('{"config_version": 2}', encoding="utf-8")
    (tmp_path / "settings.json.bak.3").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.4").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.5").write_text('{"config_version": 5}', encoding="utf-8")

    atomic_write_json(path, {"config_version": 6}, _require_numeric_config_version)

    assert [
        json.loads((tmp_path / f"settings.json.bak.{index}").read_text(encoding="utf-8"))
        for index in range(1, 4)
    ] == [
        {"config_version": 0},
        {"config_version": 2},
        {"config_version": 5},
    ]
    assert not (tmp_path / "settings.json.bak.4").exists()
    assert not (tmp_path / "settings.json.bak.5").exists()


def test_atomic_replace_failure_leaves_original_primary_readable(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 2}', encoding="utf-8")

    import file_transaction

    original_replace = file_transaction.os.replace
    failed = False

    def fail_primary_replace(source, destination):
        nonlocal failed
        if not failed and destination == path:
            failed = True
            raise OSError("simulated replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(file_transaction.os, "replace", fail_primary_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_json(path, {"config_version": 3}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 2}


def test_load_json_all_invalid_candidates_raises_without_creating_file(tmp_path):
    path = tmp_path / "memory.json"
    (tmp_path / "memory.json.bak.1").write_text("[]", encoding="utf-8")

    with pytest.raises(JsonDataError) as error:
        load_json(path, _require_examples_dict)

    assert not path.exists()
    assert "memory.json" in str(error.value)


@pytest.mark.parametrize("field", ["managed_auto_reply", "auto_recognize", "training_mode"])
def test_string_false_is_rejected(field, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"config_version": 2, field: "false"}), encoding="utf-8")

    with pytest.raises(settings_module.SettingsValidationError):
        settings_module.AppSettings(
            config_path=path,
            credential_store=FakeCredentialStore(),
        ).load()


@pytest.mark.parametrize(
    ("field", "low", "high"),
    [
        ("poll_interval_ms", 250, 10000),
        ("group_poll_interval_ms", 250, 10000),
        ("auto_reply_delay_seconds", 0.5, 10),
        ("capture_cooldown_seconds", 0.5, 60),
        ("search_max_results", 1, 6),
    ],
)
def test_settings_numeric_bounds_are_inclusive_and_reject_outside_values(field, low, high, tmp_path):
    for accepted in (low, high):
        path = tmp_path / f"{field}-{accepted}.json"
        path.write_text(json.dumps({"config_version": 2, field: accepted}), encoding="utf-8")
        loaded = settings_module.AppSettings(
            config_path=path,
            credential_store=FakeCredentialStore(),
        ).load()
        assert getattr(loaded, field) == accepted

    for rejected in (low - 0.01, high + 0.01, "1", True):
        path = tmp_path / f"{field}-invalid-{rejected}.json"
        path.write_text(json.dumps({"config_version": 2, field: rejected}), encoding="utf-8")
        with pytest.raises(settings_module.SettingsValidationError):
            settings_module.AppSettings(
                config_path=path,
                credential_store=FakeCredentialStore(),
            ).load()


@pytest.mark.parametrize(
    "area",
    [
        {"x_left": -0.01, "x_right": 0.9, "y_top": 0.1, "y_bottom": 0.9},
        {"x_left": 0.1, "x_right": 1.01, "y_top": 0.1, "y_bottom": 0.9},
        {"x_left": 0.4, "x_right": 0.4, "y_top": 0.1, "y_bottom": 0.9},
        {"x_left": 0.7, "x_right": 0.4, "y_top": 0.1, "y_bottom": 0.9},
        {"x_left": 0.1, "x_right": 0.9, "y_top": 0.9, "y_bottom": 0.9},
        {"x_left": 0.1, "x_right": 0.9, "y_top": 0.95, "y_bottom": 0.9},
        {"x_left": "0.1", "x_right": 0.9, "y_top": 0.1, "y_bottom": 0.9},
    ],
)
def test_chat_area_requires_numeric_unit_coordinates_in_visual_order(area, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"config_version": 2, "chat_area": area}), encoding="utf-8")

    with pytest.raises(settings_module.SettingsValidationError):
        settings_module.AppSettings(
            config_path=path,
            credential_store=FakeCredentialStore(),
        ).load()


def test_formal_settings_forbid_generation_and_search():
    with pytest.raises(settings_module.SettingsValidationError):
        settings_module.AppSettings(reply_source_mode="model").validate()
    with pytest.raises(settings_module.SettingsValidationError):
        settings_module.AppSettings(enable_web_search=True).validate()


def test_legacy_managed_true_migrates_only_to_observe(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"managed_auto_reply": True}), encoding="utf-8")

    loaded = settings_module.AppSettings(
        config_path=path,
        credential_store=FakeCredentialStore(),
    ).load()

    assert loaded.managed_mode == "observe"
    assert loaded.managed_auto_reply is False
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["config_version"] == 2
    assert persisted["managed_mode"] == "observe"
    assert "managed_auto_reply" not in persisted


def test_credential_store_round_trip_uses_injected_protection(tmp_path):
    from secure_store import CredentialStore

    def protect(payload):
        return b"protected:" + payload[::-1]

    def unprotect(blob):
        assert blob.startswith(b"protected:")
        return blob[len(b"protected:") :][::-1]

    path = tmp_path / "credentials.dat"
    store = CredentialStore(path=path, protect=protect, unprotect=unprotect)
    store.save({"deepseek": "secret-value", "vision:zhipu": "vision-secret"})

    assert store.load() == {"deepseek": "secret-value", "vision:zhipu": "vision-secret"}
    assert b"secret-value" not in path.read_bytes()


def test_credential_store_binary_write_fsyncs_replaces_and_keeps_five_valid_backups(tmp_path, monkeypatch):
    import file_transaction
    import secure_store

    path = tmp_path / "credentials.dat"
    store = secure_store.CredentialStore(
        path=path,
        protect=lambda payload: b"sealed:" + payload,
        unprotect=lambda blob: blob.removeprefix(b"sealed:"),
    )
    fsync_calls = []
    replace_destinations = []
    real_fsync = file_transaction.os.fsync
    real_replace = file_transaction.os.replace

    def track_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    def track_replace(source, destination):
        assert Path(source).parent == Path(destination).parent == tmp_path
        replace_destinations.append(Path(destination).name)
        return real_replace(source, destination)

    monkeypatch.setattr(file_transaction.os, "fsync", track_fsync)
    monkeypatch.setattr(file_transaction.os, "replace", track_replace)
    for value in range(7):
        store.save({"deepseek": f"secret-{value}"})

    assert fsync_calls
    assert "credentials.dat" in replace_destinations
    assert [
        secure_store.CredentialStore(
            path=tmp_path / f"credentials.dat.bak.{index}",
            protect=lambda payload: b"sealed:" + payload,
            unprotect=lambda blob: blob.removeprefix(b"sealed:"),
        ).load()["deepseek"]
        for index in range(1, 6)
    ] == ["secret-5", "secret-4", "secret-3", "secret-2", "secret-1"]


def test_credential_store_skips_undecryptable_primary_and_loads_valid_backup(tmp_path):
    from secure_store import CredentialStore

    def unprotect(blob):
        if not blob.startswith(b"sealed:"):
            raise RuntimeError("simulated DPAPI rejection")
        return blob.removeprefix(b"sealed:")

    path = tmp_path / "credentials.dat"
    store = CredentialStore(
        path=path,
        protect=lambda payload: b"sealed:" + payload,
        unprotect=unprotect,
    )
    store.save({"deepseek": "older"})
    store.save({"deepseek": "current"})
    path.write_bytes(b"not-a-dpapi-blob")

    assert store.load() == {"deepseek": "older"}


def test_legacy_plaintext_keys_are_removed_only_after_encrypted_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "api_provider": "deepseek",
                "api_key": "legacy-text",
                "provider_api_keys": {"zhipu": "legacy-zhipu"},
                "vision_provider": "zhipu",
                "vision_api_key": "legacy-vision",
            }
        ),
        encoding="utf-8",
    )
    credential_store = FakeCredentialStore()

    loaded = settings_module.AppSettings(
        config_path=path,
        credential_store=credential_store,
    ).load()

    assert loaded.get_provider_api_key("deepseek") == "legacy-text"
    assert loaded.get_provider_api_key("zhipu") == "legacy-zhipu"
    assert loaded.vision_api_key == "legacy-vision"
    assert credential_store.values == {
        "deepseek": "legacy-text",
        "zhipu": "legacy-zhipu",
        "vision:zhipu": "legacy-vision",
    }
    persisted = path.read_text(encoding="utf-8")
    assert "legacy-text" not in persisted
    assert "legacy-zhipu" not in persisted
    assert "legacy-vision" not in persisted
    persisted_fields = json.loads(persisted)
    assert "api_key" not in persisted_fields
    assert "provider_api_keys" not in persisted_fields
    assert "vision_api_key" not in persisted_fields


def test_legacy_migration_purges_plaintext_keys_from_primary_and_all_settings_backups(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"api_provider": "deepseek", "api_key": "primary-secret"}),
        encoding="utf-8",
    )
    (tmp_path / "settings.json.bak.1").write_text(
        json.dumps({"provider_api_keys": {"zhipu": "backup-provider-secret"}}),
        encoding="utf-8",
    )
    (tmp_path / "settings.json.bak.2").write_text(
        json.dumps({"vision_api_key": "backup-vision-secret"}),
        encoding="utf-8",
    )

    settings_module.AppSettings(
        config_path=path,
        credential_store=FakeCredentialStore(),
    ).load()

    candidates = [path, *sorted(tmp_path.glob("settings.json.bak.*"))]
    assert candidates
    for candidate in candidates:
        raw = candidate.read_text(encoding="utf-8")
        document = json.loads(raw)
        assert not {"api_key", "vision_api_key", "provider_api_keys"} & document.keys()
        assert "primary-secret" not in raw
        assert "backup-provider-secret" not in raw
        assert "backup-vision-secret" not in raw


@pytest.mark.parametrize(
    ("field", "literal_value"),
    [
        ("custom_headers", '{"Authorization":"Bearer sk-live-literal"}'),
        ("custom_headers", '{"X-Token":"literal-header-token"}'),
        ("custom_body", '{"token":"literal-body-token","prompt":"{prompt}"}'),
        ("custom_body", '{"auth":"literal-body-secret","prompt":"{prompt}"}'),
        ("custom_body", '{"api_key":"literal-body-key","prompt":"{prompt}"}'),
    ],
)
def test_settings_reject_literal_credentials_in_custom_request_templates(field, literal_value, tmp_path):
    settings = settings_module.AppSettings(
        config_path=tmp_path / "settings.json",
        credential_store=FakeCredentialStore(),
    )
    setattr(settings, field, literal_value)

    with pytest.raises(settings_module.SettingsValidationError):
        settings.save()

    assert not settings.config_path.exists()


def test_settings_allow_api_key_placeholder_in_custom_request_templates(tmp_path):
    path = tmp_path / "settings.json"
    settings = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore())
    settings.custom_headers = '{"Authorization":"Bearer {api_key}","X-Key":"{api_key}"}'
    settings.custom_body = '{"model":"{model}","prompt":"{prompt}","credential":"{api_key}"}'

    settings.save()
    loaded = settings_module.AppSettings(
        config_path=path,
        credential_store=FakeCredentialStore(),
    ).load()

    assert loaded.custom_headers == settings.custom_headers
    assert loaded.custom_body == settings.custom_body


@pytest.mark.parametrize(
    ("field", "literal_template", "secret"),
    [
        ("custom_headers", '{"X-Auth":"x-auth-literal-secret"}', "x-auth-literal-secret"),
        (
            "custom_headers",
            '{"Proxy-Authorization":"Basic proxy-literal-secret"}',
            "proxy-literal-secret",
        ),
        ("custom_body", '{"access_token":"access-literal-secret"}', "access-literal-secret"),
        ("custom_body", '{"client_secret":"client-literal-secret"}', "client-literal-secret"),
        ("custom_body", '{"password":"password-literal-secret"}', "password-literal-secret"),
    ],
)
def test_compound_credential_fields_reject_literals_and_keep_all_settings_snapshots_clean(
    field,
    literal_template,
    secret,
    tmp_path,
):
    path = tmp_path / "settings.json"
    settings = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore())
    for _index in range(7):
        settings.save()

    setattr(settings, field, literal_template)
    with pytest.raises(settings_module.SettingsValidationError):
        settings.save()

    candidates = [path, *sorted(tmp_path.glob("settings.json.bak.*"))]
    assert len(candidates) == 6
    for candidate in candidates:
        assert secret not in candidate.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "placeholder_template"),
    [
        ("custom_headers", '{"X-Auth":"{api_key}"}'),
        ("custom_headers", '{"Proxy-Authorization":"Basic {api_key}"}'),
        ("custom_body", '{"access_token":"{api_key}"}'),
        ("custom_body", '{"client_secret":"{api_key}"}'),
        ("custom_body", '{"password":"{api_key}"}'),
    ],
)
def test_compound_credential_fields_allow_api_key_placeholder(field, placeholder_template, tmp_path):
    path = tmp_path / "settings.json"
    settings = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore())
    setattr(settings, field, placeholder_template)

    settings.save()
    loaded = settings_module.AppSettings(config_path=path, credential_store=FakeCredentialStore()).load()

    assert getattr(loaded, field) == placeholder_template


def test_settings_load_uses_valid_backup_when_primary_persisted_string_type_is_corrupt(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"config_version": 2, "api_provider": ["not", "a", "string"]}),
        encoding="utf-8",
    )
    (tmp_path / "settings.json.bak.1").write_text(
        json.dumps({"config_version": 2, "api_provider": "deepseek", "model": "backup-model"}),
        encoding="utf-8",
    )

    loaded = settings_module.AppSettings(
        config_path=path,
        credential_store=FakeCredentialStore(),
    ).load()

    assert loaded.api_provider == "deepseek"
    assert loaded.model == "backup-model"


@pytest.mark.parametrize(
    ("malformed_field", "malformed_value", "backup_model"),
    [
        ("api_key", ["not", "a", "string"], "backup-api-key"),
        ("vision_api_key", 123, "backup-vision-key"),
        ("provider_api_keys", {"deepseek": ["not", "a", "string"]}, "backup-provider-keys"),
    ],
)
def test_v1_malformed_credential_field_is_rejected_before_valid_backup_selection(
    malformed_field,
    malformed_value,
    backup_model,
    tmp_path,
):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({malformed_field: malformed_value}), encoding="utf-8")
    (tmp_path / "settings.json.bak.1").write_text(
        json.dumps({"config_version": 2, "api_provider": "deepseek", "model": backup_model}),
        encoding="utf-8",
    )

    loaded = settings_module.AppSettings(
        config_path=path,
        credential_store=FakeCredentialStore(),
    ).load()

    assert loaded.api_provider == "deepseek"
    assert loaded.model == backup_model


@pytest.mark.parametrize("failure_mode", ["save", "load"])
def test_failed_legacy_credential_migration_preserves_original_bytes_and_raises_typed_error(
    failure_mode, tmp_path
):
    path = tmp_path / "settings.json"
    original = b'{\n  "api_key": "legacy-secret",\n  "managed_auto_reply": true\n}\n'
    path.write_bytes(original)
    credential_store = FakeCredentialStore(
        save_error=RuntimeError("encrypt failed") if failure_mode == "save" else None,
        load_error=RuntimeError("read-back failed") if failure_mode == "load" else None,
    )

    settings = settings_module.AppSettings(
        config_path=path,
        credential_store=credential_store,
    )
    with pytest.raises(settings_module.SettingsPersistenceError):
        settings.load()

    assert path.read_bytes() == original
    assert settings.managed_mode == "off"
    assert settings.managed_auto_reply is False


def test_save_uses_explicit_allowlist_without_runtime_objects_or_key_fields(tmp_path):
    path = tmp_path / "settings.json"
    credential_store = FakeCredentialStore()
    settings = settings_module.AppSettings(config_path=path, credential_store=credential_store)
    settings.set_provider_api_key("deepseek", "secret-value")
    settings.vision_api_key = "vision-value"
    settings.runtime_socket = object()
    settings.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config_version"] == 2
    assert "config_path" not in saved
    assert "credential_store" not in saved
    assert "runtime_socket" not in saved
    assert not any("key" in field.casefold() for field in saved)
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert "vision-value" not in path.read_text(encoding="utf-8")
