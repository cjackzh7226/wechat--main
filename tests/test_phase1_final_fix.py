from __future__ import annotations

import json
import importlib.util
import logging.handlers
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import atomic_json
import app_paths
import file_transaction
import float_ui
import reply_engine
import runtime_logging
import main
import secure_store
import send_ledger as ledger_module
import single_instance
from memory import StyleMemory
from group_chat_handler import GroupChatHandler
from reply_engine import NO_DIRECT_ANSWER_REPLY, ReplyEngine
from managed_reply import ReplyDecision
from managed_runtime import ManagedReplyCoordinator, SendAttempt
from chat_identity import ChatIdentity, ChatObservation
from settings import AppSettings, SettingsValidationError
from send_ledger import LedgerBlockingError, SendLedger
from wechat_window import ChatPlatform, ChatWindow, WindowRect


EXACT_NO_ANSWER = "暂未查到对应答案"


class _MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def load(self) -> dict[str, str]:
        return dict(self.values)

    def save(self, values: dict[str, str]) -> None:
        self.values = dict(values)


def _empty_engine(tmp_path: Path) -> ReplyEngine:
    settings = AppSettings()
    assert settings.reply_source_mode == "knowledge_only"
    return ReplyEngine(settings, StyleMemory(tmp_path / "memory.json"))


@pytest.mark.parametrize(
    "customer_text",
    [
        "[对方] 嗯嗯",
        "[对方] 谢谢",
        "[对方] 工作量太大了",
        "[对方] 这个怎么办？",
    ],
    ids=["low-information", "thanks", "statement", "question"],
)
def test_knowledge_only_without_approved_hit_returns_exact_no_answer_and_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, customer_text: str
) -> None:
    engine = _empty_engine(tmp_path)

    def forbidden_fallback(_conversation_text: str) -> list[str]:
        raise AssertionError("knowledge_only must not call local fallback rules")

    monkeypatch.setattr(engine, "_fallback", forbidden_fallback)

    assert engine.generate(customer_text, "客户") == [EXACT_NO_ANSWER]


class _JsonResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


def _judgement_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object) -> ReplyEngine:
    settings = AppSettings()
    settings.api_provider = "deepseek"
    settings.api_key = "configured-for-isolated-test"
    settings.base_url = "https://example.invalid"
    settings.model = "isolated-model"
    engine = ReplyEngine(settings, StyleMemory(tmp_path / "memory.json"))
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(
        reply_engine,
        "requests",
        SimpleNamespace(post=lambda *_args, **_kwargs: _JsonResponse(content)),
    )
    return engine


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.called = threading.Event()

    def send(self, _window: ChatWindow, text: str, **_kwargs: object) -> SendAttempt:
        self.calls.append(text)
        self.called.set()
        return SendAttempt(True, "keypress")


class _InlineManagedWorker:
    def __init__(self, ledger: SendLedger) -> None:
        self.coordinator = ManagedReplyCoordinator(ledger)

    def submit(self, observation, adapters, callback=None) -> bool:
        outcome = self.coordinator.process(observation, adapters)
        if callback is not None:
            callback(outcome)
        return True


def _new_managed_app(engine: ReplyEngine, ledger: SendLedger, sender: object) -> tuple[object, ChatWindow]:
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = engine.settings
    app.settings.managed_mode = "full"
    app.settings.auto_reply_delay_seconds = 0
    app.engine = engine
    app.memory = SimpleNamespace(assert_ready_for_managed_reply=lambda: None)
    app.send_ledger = ledger
    app.queue = queue.Queue()
    app.sender = sender
    app.send_operation_lock = threading.Lock()
    app.managed_worker = _InlineManagedWorker(ledger)
    app.managed_coordinator = app.managed_worker.coordinator
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: True)
    app._managed_pending_count = 0
    app.chat_identity = SimpleNamespace(current_chat_key="isolated personal chat")
    app.group_handler = GroupChatHandler(app.settings)
    window = ChatWindow(
        hwnd=711,
        title="isolated personal chat",
        rect=WindowRect(10, 20, 970, 660),
        platform=ChatPlatform.for_key("wecom"),
    )
    app.current_window = window
    app.detector = SimpleNamespace(
        foreground_chat=lambda: window,
        any_chat_window=lambda: None,
        window_from_hwnd=lambda hwnd: window if hwnd == window.hwnd else None,
        foreground_hwnd=lambda: window.hwnd,
        root_hwnd=lambda hwnd: hwnd,
    )
    app.ui = SimpleNamespace(root=SimpleNamespace(winfo_id=lambda: 999))
    app.current_context = "[对方] 测试问题"

    def personal_observation(_window, chat_type=None):
        if chat_type not in {None, "personal"}:
            return None
        turn = tuple(main._current_unreplied_turn(app.current_context))
        if not turn:
            return None
        return ChatObservation(
            ChatIdentity(window.hwnd, window.title, "personal"),
            app.current_context,
            turn,
            captured_at=1.0,
            foreground=True,
        )

    app._capture_runtime_observation = personal_observation
    app._capture_fresh_runtime_observation = (
        lambda expected, _window, *, require_foreground=True: expected
    )
    app._verify_runtime_send = lambda *_args: "verified"
    app.managed_send_inflight = False
    return app, window


def _send_personal_reply(engine: ReplyEngine, context: str, replies: list[str]) -> list[str]:
    ledger = SendLedger(engine.memory.path.parent / "send_ledger.json").load()
    app, window = _new_managed_app(engine, ledger, _FakeSender())
    app.current_context = context
    app._managed_preflight_context = lambda _window: context

    app._maybe_managed_send(context, replies)
    assert app.sender.called.wait(1.0), "fake personal sender was not called"
    return app.sender.calls


def _send_group_reply(engine: ReplyEngine, question: str, replies: list[str]) -> list[str]:
    ledger = SendLedger(engine.memory.path.parent / "send_ledger.json").load()
    app, _personal_window = _new_managed_app(engine, ledger, _FakeSender())
    window = ChatWindow(
        hwnd=712,
        title="isolated group chat",
        rect=WindowRect(10, 20, 970, 660),
        platform=ChatPlatform.for_key("wecom"),
    )
    turn_message = f"@客服 {question}"
    app.chat_identity = SimpleNamespace(current_chat_key=window.title)
    app.detector = SimpleNamespace(
        foreground_chat=lambda: window,
        window_from_hwnd=lambda hwnd: window if hwnd == window.hwnd else None,
        foreground_hwnd=lambda: window.hwnd,
        root_hwnd=lambda hwnd: hwnd,
    )
    group_observation = ChatObservation(
        ChatIdentity(window.hwnd, window.title, "group"),
        f"[对方] 张三@微信\n[对方] {turn_message}",
        (question,),
        sender_name="张三",
        captured_at=1.0,
        foreground=True,
    )
    app._capture_runtime_observation = lambda _window, chat_type=None: group_observation

    app._send_group_reply(
        window,
        question,
        sender_name="张三",
        chat_identity=window.title,
    )
    assert app.sender.called.wait(1.0), "fake group sender was not called"
    return app.sender.calls


def test_personal_fake_sender_receives_only_exact_no_answer_for_knowledge_miss(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    context = "[对方] 这个怎么办？"

    assert _send_personal_reply(engine, context, engine.generate(context, "客户")) == [EXACT_NO_ANSWER]


def test_group_fake_sender_receives_only_exact_no_answer_for_knowledge_miss(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    question = "谢谢"

    assert _send_group_reply(engine, question, engine.generate(question, "群聊")) == [
        f"@张三 {EXACT_NO_ANSWER}"
    ]


def test_personal_managed_send_ignores_untyped_external_reply_and_redecides_in_worker(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    sender = _FakeSender()
    app, _window = _new_managed_app(
        engine, SendLedger(tmp_path / "send_ledger.json").load(), sender
    )
    context = "[对方] 这个怎么办？"
    app.current_context = context
    app._managed_preflight_context = lambda _window: context
    engine.last_managed_decision = None

    app._maybe_managed_send(context, ["任意旧候选文本"])

    assert sender.called.wait(1.0)
    assert sender.calls == [EXACT_NO_ANSWER]


def test_personal_managed_send_revalidates_typed_decision_before_sender(
    tmp_path: Path,
) -> None:
    engine = _empty_engine(tmp_path)
    sender = _FakeSender()
    app, _window = _new_managed_app(
        engine, SendLedger(tmp_path / "send_ledger.json").load(), sender
    )
    context = "[对方] 这个怎么办？"
    app.current_context = context
    app._managed_preflight_context = lambda _window: context
    decision = ReplyDecision.no_answer("no_eligible_candidate")
    engine.managed_decision_still_valid = lambda _decision: False

    app._maybe_managed_send(
        context,
        [decision.text],
        managed_decision=decision,
    )

    assert sender.called.wait(0.2) is False
    assert sender.calls == []


def test_personal_managed_send_preserves_multiline_answer_exactly(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    sender = _FakeSender()
    app, _window = _new_managed_app(
        engine, SendLedger(tmp_path / "send_ledger.json").load(), sender
    )
    context = "[对方] 这个怎么办？"
    app.current_context = context
    app._managed_preflight_context = lambda _window: context
    text = "第一行\n- 条件A\n- 条件B"
    decision = ReplyDecision(
        kind="answer",
        text=text,
        record_id="record-id",
        answer_hash="a" * 64,
        reason="approved_candidate_reloaded",
        score=100,
        org_scope="both",
    )
    engine.managed_decision_still_valid = lambda candidate: candidate == decision
    engine.decide_managed = lambda _context, _turn: decision

    app._maybe_managed_send(
        context,
        [text],
        managed_decision=decision,
    )

    assert sender.called.wait(1.0)
    assert sender.calls == [text]


def test_generation_worker_defers_typed_decision_to_managed_runtime() -> None:
    decision = ReplyDecision.no_answer("no_eligible_candidate")

    class TypedEngine:
        def __init__(self) -> None:
            self.calls = []

        def decide_managed(self, context, target_turn):
            self.calls.append((context, list(target_turn)))
            return decision

        def generate(self, *_args, **_kwargs):
            raise AssertionError("managed worker must not call legacy generate")

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="full")
    app.engine = TypedEngine()
    app.generation_lock = threading.Lock()
    app.pending_generation = None
    app.generation_inflight = True
    app.generation_active_turn = ["问题？"]
    app.queue = queue.Queue()
    window = ChatWindow(
        hwnd=900,
        title="typed decision chat",
        rect=WindowRect(10, 20, 970, 660),
        platform=ChatPlatform.for_key("wecom"),
    )
    context = "[对方] 问题？"
    app.current_context = context
    app.current_window = window
    app._reply_source_label = lambda: "知识库"
    app._generated_status = lambda count: f"generated:{count}"
    captured = []
    app._maybe_managed_send = lambda incoming_context, replies, *, managed_decision=None: captured.append(
        (incoming_context, replies, managed_decision)
    )

    app._generation_worker(context, window, ["问题？"])

    assert app.engine.calls == []
    assert captured == [(context, [], None)]


@pytest.mark.parametrize(
    "payload",
    [
        {"chosen": 1, "consistent": "false"},
        {"chosen": 1, "consistent": "true"},
        {"chosen": 1, "consistent": 0},
        {"chosen": 1, "consistent": 1},
        {"chosen": 1.0, "consistent": True},
        {"chosen": True, "consistent": True},
        {"chosen": "1", "consistent": True},
        {"chosen": 1, "consistent": True, "answer": "MODEL_AUTHORED_TEXT"},
        {"chosen": 1},
        {"consistent": True},
        {"chosen": 1, "consistent": False},
        "not-json",
    ],
    ids=[
        "consistent-string-false",
        "consistent-string-true",
        "consistent-zero",
        "consistent-one",
        "chosen-float",
        "chosen-bool",
        "chosen-string",
        "extra-key",
        "missing-consistent",
        "missing-chosen",
        "explicit-inconsistent",
        "parse-error",
    ],
)
def test_enterprise_judgement_is_strict_json_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    engine = _judgement_engine(tmp_path, monkeypatch, payload)
    candidates = [{"question": "如何办理？", "reply": "APPROVED_KB_ANSWER", "record_id": "r1"}]

    assert engine._enterprise_answer_via_judgement("如何办理？", candidates) == [EXACT_NO_ANSWER]


def test_enterprise_judgement_returns_only_selected_knowledge_answer_for_exact_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _judgement_engine(tmp_path, monkeypatch, {"chosen": 1, "consistent": True})
    candidates = [{"question": "如何办理？", "reply": "APPROVED_KB_ANSWER", "record_id": "r1"}]

    assert engine._enterprise_answer_via_judgement("如何办理？", candidates) == ["APPROVED_KB_ANSWER"]
    assert NO_DIRECT_ANSWER_REPLY != "APPROVED_KB_ANSWER"


def test_invalid_consistency_json_reaches_fake_sender_only_as_exact_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _judgement_engine(tmp_path, monkeypatch, {"chosen": 1, "consistent": "false"})
    candidates = [{"question": "如何办理？", "reply": "APPROVED_KB_ANSWER", "record_id": "r1"}]
    context = "[对方] 如何办理？"
    replies = engine._enterprise_answer_via_judgement("如何办理？", candidates)

    assert _send_personal_reply(engine, context, replies) == [EXACT_NO_ANSWER]


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("custom_headers", '{"X-\\u0041uth":"literal-unicode-secret"}'),
        ("custom_headers", '{"AUTHORIZATION":"literal-uppercase-secret"}'),
        ("custom_headers", '{"proxyAuthorization":"literal-camel-secret"}'),
        ("custom_body", '{"access-token":"literal-separated-secret"}'),
        ("custom_body", '{"outer":[{"clientSecret":"literal-nested-secret"}]}'),
        ("custom_body", '{"outer":{"items":[{"credential":"literal-list-secret"}]}}'),
        ("custom_headers", '{"Authorization":"Bearer lit\\u0065ral-escaped-secret"}'),
        ("custom_body", '{"password":"prefix-{api_key}"}'),
        ("custom_body", '{"token":"{api_key}-suffix"}'),
    ],
    ids=[
        "unicode-key",
        "uppercase-key",
        "camel-key",
        "separator-key",
        "nested-dict-list",
        "nested-list-dict",
        "escaped-literal-value",
        "literal-before-placeholder",
        "literal-after-placeholder",
    ],
)
def test_custom_json_templates_reject_semantic_literal_credentials(
    tmp_path: Path, field: str, template: str
) -> None:
    settings = AppSettings(
        config_path=tmp_path / "settings.json",
        credential_store=_MemoryCredentialStore(),
    )
    setattr(settings, field, template)

    with pytest.raises(SettingsValidationError):
        settings.save()


def test_custom_json_templates_allow_only_approved_api_key_placeholder_forms(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    credentials = _MemoryCredentialStore()
    settings = AppSettings(config_path=path, credential_store=credentials)
    settings.custom_headers = (
        '{"X-Auth":"{api_key}","Authorization":"Bearer {api_key}",'
        '"Proxy-Authorization":"Basic {api_key}","Content-Type":"application/json"}'
    )
    settings.custom_body = (
        '{"model":"{model}","outer":[{"clientSecret":"{api_key}"}],'
        '"messages":{messages_json}}'
    )

    settings.save()

    loaded = AppSettings(config_path=path, credential_store=credentials).load()
    assert loaded.custom_headers == settings.custom_headers
    assert loaded.custom_body == settings.custom_body


def test_unicode_escaped_sensitive_key_never_reaches_primary_or_five_backups(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(config_path=path, credential_store=_MemoryCredentialStore())
    for index in range(7):
        settings.model = f"safe-model-{index}"
        settings.save()

    settings.custom_headers = '{"X-\\u0041uth":"literal-secret-must-not-persist"}'
    with pytest.raises(SettingsValidationError):
        settings.save()
    candidates = [path, *[tmp_path / f"settings.json.bak.{index}" for index in range(1, 6)]]
    assert all(candidate.exists() for candidate in candidates)
    for candidate in candidates:
        assert "literal-secret-must-not-persist" not in candidate.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("custom_headers", '{"xauthorizationextra":"round2-literal-secret"}'),
        ("custom_body", '{"myapikeyvalue":"round2-literal-secret"}'),
        ("custom_headers", '{"authorization2":"round2-literal-secret"}'),
        ("custom_body", '{"outer":{"xauthorizationextra":"round2-literal-secret"}}'),
        ("custom_body", '{"outer":[{"myapikeyvalue":"round2-literal-secret"}]}'),
        ("custom_body", '{"outer":[{"authorization2":"round2-literal-secret"}]}'),
    ],
    ids=[
        "xauthorizationextra",
        "myapikeyvalue",
        "authorization2",
        "nested-xauthorizationextra",
        "list-myapikeyvalue",
        "list-authorization2",
    ],
)
def test_custom_json_rejects_bounded_sensitive_stems_inside_lowercase_compound_keys(
    tmp_path: Path, field: str, template: str
) -> None:
    settings = AppSettings(
        config_path=tmp_path / "settings.json",
        credential_store=_MemoryCredentialStore(),
    )
    setattr(settings, field, template)

    with pytest.raises(SettingsValidationError):
        settings.save()


def test_compound_sensitive_key_save_failure_leaves_primary_and_five_backups_literal_free(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(config_path=path, credential_store=_MemoryCredentialStore())
    for index in range(7):
        settings.model = f"round2-safe-{index}"
        settings.save()

    settings.custom_body = (
        '{"xauthorizationextra":"round2-secret-one",'
        '"nested":[{"myapikeyvalue":"round2-secret-two"},'
        '{"authorization2":"round2-secret-three"}]}'
    )
    with pytest.raises(SettingsValidationError):
        settings.save()

    candidates = [path, *[tmp_path / f"settings.json.bak.{index}" for index in range(1, 6)]]
    assert all(candidate.exists() for candidate in candidates)
    for candidate in candidates:
        persisted = candidate.read_text(encoding="utf-8")
        assert "round2-secret-one" not in persisted
        assert "round2-secret-two" not in persisted
        assert "round2-secret-three" not in persisted


def test_positive_schema_allows_business_strings_and_ordinary_non_string_fields(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    credentials = _MemoryCredentialStore()
    settings = AppSettings(config_path=path, credential_store=credentials)
    settings.custom_body = (
        '{"model":"{model}","prompt":"{prompt}",'
        '"tokenizerModel":42,"monkeyValue":false}'
    )

    settings.save()

    assert AppSettings(config_path=path, credential_store=credentials).load().custom_body == settings.custom_body


@pytest.mark.parametrize(
    "template",
    [
        '{"fooauthorizationpayload":"self-attack-secret"}',
        '{"outer":{"tenantapikeyblob":"self-attack-secret"}}',
        '{"outer":[{"vendorclientsecretdata":"self-attack-secret"}]}',
        '{"outer":[{"nested":{"bearertokenmaterial":"self-attack-secret"}}]}',
    ],
    ids=[
        "fooauthorizationpayload",
        "tenantapikeyblob-nested",
        "vendorclientsecretdata-list",
        "bearertokenmaterial-deep",
    ],
)
def test_custom_body_positive_schema_rejects_unknown_string_bearing_keys(
    tmp_path: Path, template: str
) -> None:
    settings = AppSettings(
        config_path=tmp_path / "settings.json",
        credential_store=_MemoryCredentialStore(),
    )
    settings.custom_body = template

    with pytest.raises(SettingsValidationError):
        settings.save()


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("custom_body", '{"outer":["anonymous-list-secret"]}'),
        ("custom_body", '{"outer":[[["anonymous-list-secret"]]]}'),
        ("custom_body", '{"outer":[{"items":[["anonymous-list-secret"]]}]}'),
        (
            "custom_body",
            '{"outer":[{"clientSecret":"{api_key}"},["anonymous-list-secret"]]}',
        ),
        ("custom_headers", '{"Content-Type":["anonymous-list-secret"]}'),
    ],
    ids=[
        "body-direct-list",
        "body-multilevel-list",
        "body-mixed-dict-list",
        "body-placeholder-and-anonymous-leaf",
        "header-list",
    ],
)
def test_custom_json_rejects_anonymous_string_leaves_outside_approved_schema_context(
    tmp_path: Path, field: str, template: str
) -> None:
    settings = AppSettings(
        config_path=tmp_path / "settings.json",
        credential_store=_MemoryCredentialStore(),
    )
    setattr(settings, field, template)

    with pytest.raises(SettingsValidationError):
        settings.save()


def test_anonymous_list_string_rejection_keeps_primary_and_five_backups_clean(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(config_path=path, credential_store=_MemoryCredentialStore())
    for index in range(7):
        settings.model = f"gate-safe-model-{index}"
        settings.save()

    settings.custom_body = '{"outer":[{"items":[["anonymous-list-secret"]]}]}'
    with pytest.raises(SettingsValidationError):
        settings.save()

    candidates = [path, *[tmp_path / f"settings.json.bak.{index}" for index in range(1, 6)]]
    assert all(candidate.exists() for candidate in candidates)
    for candidate in candidates:
        assert "anonymous-list-secret" not in candidate.read_text(encoding="utf-8")


def test_custom_body_allows_string_lists_only_beneath_approved_business_field(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    credentials = _MemoryCredentialStore()
    settings = AppSettings(config_path=path, credential_store=credentials)
    settings.custom_body = '{"model":"{model}","input":["first","second"],"messages":[]}'

    settings.save()

    loaded = AppSettings(config_path=path, credential_store=credentials).load()
    assert loaded.custom_body == settings.custom_body


def _require_version_object(value: object) -> None:
    if type(value) is not dict or type(value.get("version")) is not int:
        raise ValueError("version must be an integer")


def _candidate_paths(path: Path) -> list[Path]:
    return [path, *[path.with_name(f"{path.name}.bak.{index}") for index in range(1, 6)]]


def _candidate_bytes(path: Path) -> dict[str, bytes | None]:
    return {candidate.name: candidate.read_bytes() if candidate.exists() else None for candidate in _candidate_paths(path)}


def _seed_json_candidates(path: Path, *, valid_count: int = 6) -> None:
    for index, candidate in enumerate(_candidate_paths(path)):
        if index < valid_count:
            candidate.write_text(json.dumps({"version": index}), encoding="utf-8")
        else:
            candidate.write_bytes(f"broken-{index}".encode("ascii"))


def _fail_transaction_step(monkeypatch: pytest.MonkeyPatch, operation: str, target: str, error: BaseException) -> None:
    def hook(actual_operation: str, actual_target: str) -> None:
        if (actual_operation, actual_target) == (operation, target):
            raise error

    monkeypatch.setattr(atomic_json, "_TRANSACTION_FAULT_HOOK", hook, raising=False)


@pytest.mark.parametrize("operation", ["write", "fsync", "replace"])
@pytest.mark.parametrize("slot", range(1, 6))
def test_atomic_json_backup_fault_restores_every_old_candidate_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, slot: int
) -> None:
    path = tmp_path / "settings.json"
    _seed_json_candidates(path)
    before = _candidate_bytes(path)
    _fail_transaction_step(monkeypatch, operation, f"backup:{slot}", OSError(f"fault {operation} {slot}"))

    with pytest.raises(OSError, match="fault"):
        atomic_json.atomic_write_json(path, {"version": 99}, _require_version_object)

    assert _candidate_bytes(path) == before


@pytest.mark.parametrize("slot", range(1, 6))
def test_atomic_json_backup_delete_fault_restores_every_old_candidate_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot: int
) -> None:
    path = tmp_path / "settings.json"
    _seed_json_candidates(path, valid_count=slot - 1)
    before = _candidate_bytes(path)
    _fail_transaction_step(monkeypatch, "delete", f"backup:{slot}", OSError(f"fault delete {slot}"))

    with pytest.raises(OSError, match="fault"):
        atomic_json.atomic_write_json(path, {"version": 99}, _require_version_object)

    assert _candidate_bytes(path) == before


def test_atomic_json_final_primary_replace_fault_restores_every_old_candidate_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_json_candidates(path)
    before = _candidate_bytes(path)
    _fail_transaction_step(monkeypatch, "replace", "primary", OSError("fault primary replace"))

    with pytest.raises(OSError, match="fault primary replace"):
        atomic_json.atomic_write_json(path, {"version": 99}, _require_version_object)

    assert _candidate_bytes(path) == before


def test_atomic_json_real_backup_replace_failure_never_partially_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_json_candidates(path)
    before = _candidate_bytes(path)
    failed = False
    real_replace = file_transaction.os.replace

    def fail_third_backup_once(source: object, destination: object) -> None:
        nonlocal failed
        if not failed and Path(destination) == path.with_name(f"{path.name}.bak.3"):
            failed = True
            raise OSError("real backup 3 replace fault")
        real_replace(source, destination)

    monkeypatch.setattr(file_transaction.os, "replace", fail_third_backup_once)
    with pytest.raises(OSError, match="real backup 3"):
        atomic_json.atomic_write_json(path, {"version": 99}, _require_version_object)

    assert _candidate_bytes(path) == before


class _SimulatedCrash(BaseException):
    pass


def test_load_json_automatically_recovers_transaction_left_by_process_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_json_candidates(path)
    before = _candidate_bytes(path)
    _fail_transaction_step(monkeypatch, "replace", "primary", _SimulatedCrash("process terminated"))

    with pytest.raises(_SimulatedCrash):
        atomic_json.atomic_write_json(path, {"version": 99}, _require_version_object)

    monkeypatch.setattr(atomic_json, "_TRANSACTION_FAULT_HOOK", None, raising=False)
    result = atomic_json.load_json(path, _require_version_object)

    assert result.data == {"version": 0}
    assert _candidate_bytes(path) == before
    assert not path.with_name(f".{path.name}.transaction").exists()


def _protect_for_test(payload: bytes) -> bytes:
    return b"protected:" + payload


def _unprotect_for_test(blob: bytes) -> bytes:
    if not blob.startswith(b"protected:"):
        raise ValueError("invalid protected blob")
    return blob[len(b"protected:") :]


def test_secure_store_uses_same_transaction_and_restores_all_old_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.dat"
    store = secure_store.CredentialStore(path=path, protect=_protect_for_test, unprotect=_unprotect_for_test)
    for index in range(6):
        store.save({"provider": f"old-secret-{index}"})
    before = _candidate_bytes(path)
    _fail_transaction_step(monkeypatch, "replace", "backup:3", OSError("credential backup fault"))

    with pytest.raises(OSError, match="credential backup fault"):
        store.save({"provider": "new-secret"})

    assert _candidate_bytes(path) == before
    assert secure_store.CredentialStore(
        path=path,
        protect=_protect_for_test,
        unprotect=_unprotect_for_test,
    ).load() == {"provider": "old-secret-5"}


def test_secure_store_real_backup_replace_failure_never_partially_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.dat"
    store = secure_store.CredentialStore(path=path, protect=_protect_for_test, unprotect=_unprotect_for_test)
    for index in range(6):
        store.save({"provider": f"old-secret-{index}"})
    before = _candidate_bytes(path)
    failed = False
    real_replace = file_transaction.os.replace

    def fail_third_backup_once(source: object, destination: object) -> None:
        nonlocal failed
        if not failed and Path(destination) == path.with_name(f"{path.name}.bak.3"):
            failed = True
            raise OSError("real credential backup 3 replace fault")
        real_replace(source, destination)

    monkeypatch.setattr(file_transaction.os, "replace", fail_third_backup_once)
    with pytest.raises(OSError, match="real credential backup 3"):
        store.save({"provider": "new-secret"})

    assert _candidate_bytes(path) == before


def _read_json_version(path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert type(value) is dict and type(value.get("version")) is int
    return value["version"]


def test_atomic_json_two_writers_collect_snapshots_inside_one_path_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    atomic_json.atomic_write_json(path, {"version": 0}, _require_version_object)
    first_loader_entered = threading.Event()
    release_first_loader = threading.Event()
    second_started = threading.Event()
    second_loader_entered = threading.Event()
    errors: list[BaseException] = []
    original_loader = atomic_json._valid_snapshots

    def observed_loader(candidate_path: Path, validator: object) -> list[bytes]:
        name = threading.current_thread().name
        if name == "json-writer-1":
            first_loader_entered.set()
            assert release_first_loader.wait(2.0)
        elif name == "json-writer-2":
            second_loader_entered.set()
        return original_loader(candidate_path, validator)

    def write(version: int) -> None:
        try:
            if version == 2:
                second_started.set()
            atomic_json.atomic_write_json(path, {"version": version}, _require_version_object)
        except BaseException as error:  # surfaced in the test thread after join
            errors.append(error)

    monkeypatch.setattr(atomic_json, "_valid_snapshots", observed_loader)
    writer1 = threading.Thread(name="json-writer-1", target=write, args=(1,))
    writer2 = threading.Thread(name="json-writer-2", target=write, args=(2,))
    writer1.start()
    assert first_loader_entered.wait(1.0)
    writer2.start()
    assert second_started.wait(1.0)
    crossed_lock_boundary = second_loader_entered.wait(0.2)
    release_first_loader.set()
    writer1.join(2.0)
    writer2.join(2.0)

    assert not crossed_lock_boundary, "writer2 collected a stale snapshot before writer1 committed"
    assert not writer1.is_alive() and not writer2.is_alive()
    assert errors == []
    assert _read_json_version(path) == 2
    assert _read_json_version(path.with_name(f"{path.name}.bak.1")) == 1


def test_secure_store_two_writers_collect_snapshots_inside_one_path_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.dat"
    store = secure_store.CredentialStore(
        path=path,
        protect=_protect_for_test,
        unprotect=_unprotect_for_test,
    )
    store.save({"provider": "version-0"})
    first_loader_entered = threading.Event()
    release_first_loader = threading.Event()
    second_started = threading.Event()
    second_loader_entered = threading.Event()
    errors: list[BaseException] = []
    original_loader = store._valid_snapshots

    def observed_loader() -> list[bytes]:
        name = threading.current_thread().name
        if name == "secure-writer-1":
            first_loader_entered.set()
            assert release_first_loader.wait(2.0)
        elif name == "secure-writer-2":
            second_loader_entered.set()
        return original_loader()

    def write(version: int) -> None:
        try:
            if version == 2:
                second_started.set()
            store.save({"provider": f"version-{version}"})
        except BaseException as error:  # surfaced in the test thread after join
            errors.append(error)

    monkeypatch.setattr(store, "_valid_snapshots", observed_loader)
    writer1 = threading.Thread(name="secure-writer-1", target=write, args=(1,))
    writer2 = threading.Thread(name="secure-writer-2", target=write, args=(2,))
    writer1.start()
    assert first_loader_entered.wait(1.0)
    writer2.start()
    assert second_started.wait(1.0)
    crossed_lock_boundary = second_loader_entered.wait(0.2)
    release_first_loader.set()
    writer1.join(2.0)
    writer2.join(2.0)

    assert not crossed_lock_boundary, "writer2 collected a stale DPAPI snapshot before writer1 committed"
    assert not writer1.is_alive() and not writer2.is_alive()
    assert errors == []
    assert store.load() == {"provider": "version-2"}
    assert store._decode_blob(path.with_name(f"{path.name}.bak.1").read_bytes()) == {
        "provider": "version-1"
    }


def test_atomic_json_failed_second_writer_restores_first_writer_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    atomic_json.atomic_write_json(path, {"version": 0}, _require_version_object)
    first_loader_entered = threading.Event()
    release_first_loader = threading.Event()
    original_loader = atomic_json._valid_snapshots
    errors: list[tuple[str, BaseException]] = []

    def observed_loader(candidate_path: Path, validator: object) -> list[bytes]:
        if threading.current_thread().name == "json-commit-writer":
            first_loader_entered.set()
            assert release_first_loader.wait(2.0)
        return original_loader(candidate_path, validator)

    def fault(operation: str, target: str) -> None:
        if threading.current_thread().name == "json-failed-writer" and (operation, target) == (
            "replace",
            "backup:1",
        ):
            raise OSError("second JSON writer failed")

    def write(name: str, version: int) -> None:
        try:
            atomic_json.atomic_write_json(path, {"version": version}, _require_version_object)
        except BaseException as error:
            errors.append((name, error))

    monkeypatch.setattr(atomic_json, "_valid_snapshots", observed_loader)
    monkeypatch.setattr(atomic_json, "_TRANSACTION_FAULT_HOOK", fault)
    writer1 = threading.Thread(name="json-commit-writer", target=write, args=("writer1", 1))
    writer2 = threading.Thread(name="json-failed-writer", target=write, args=("writer2", 2))
    writer1.start()
    assert first_loader_entered.wait(1.0)
    writer2.start()
    release_first_loader.set()
    writer1.join(2.0)
    writer2.join(2.0)

    assert not writer1.is_alive() and not writer2.is_alive()
    assert len(errors) == 1 and errors[0][0] == "writer2"
    assert isinstance(errors[0][1], OSError)
    assert _read_json_version(path) == 1


def test_secure_store_failed_second_writer_restores_first_writer_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.dat"
    store = secure_store.CredentialStore(
        path=path,
        protect=_protect_for_test,
        unprotect=_unprotect_for_test,
    )
    store.save({"provider": "version-0"})
    first_loader_entered = threading.Event()
    release_first_loader = threading.Event()
    original_loader = store._valid_snapshots
    errors: list[tuple[str, BaseException]] = []

    def observed_loader() -> list[bytes]:
        if threading.current_thread().name == "secure-commit-writer":
            first_loader_entered.set()
            assert release_first_loader.wait(2.0)
        return original_loader()

    def fault(operation: str, target: str) -> None:
        if threading.current_thread().name == "secure-failed-writer" and (operation, target) == (
            "replace",
            "backup:1",
        ):
            raise OSError("second secure writer failed")

    def write(name: str, version: int) -> None:
        try:
            store.save({"provider": f"version-{version}"})
        except BaseException as error:
            errors.append((name, error))

    monkeypatch.setattr(store, "_valid_snapshots", observed_loader)
    monkeypatch.setattr(atomic_json, "_TRANSACTION_FAULT_HOOK", fault)
    writer1 = threading.Thread(name="secure-commit-writer", target=write, args=("writer1", 1))
    writer2 = threading.Thread(name="secure-failed-writer", target=write, args=("writer2", 2))
    writer1.start()
    assert first_loader_entered.wait(1.0)
    writer2.start()
    release_first_loader.set()
    writer1.join(2.0)
    writer2.join(2.0)

    assert not writer1.is_alive() and not writer2.is_alive()
    assert len(errors) == 1 and errors[0][0] == "writer2"
    assert isinstance(errors[0][1], OSError)
    assert store.load() == {"provider": "version-1"}


def test_backup_count_is_strictly_fixed_to_five(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    with pytest.raises(ValueError, match="exactly 5"):
        atomic_json.atomic_write_json(path, {"version": 1}, _require_version_object, backup_count=4)
    with pytest.raises(ValueError, match="exactly 5"):
        secure_store.CredentialStore(
            path=tmp_path / "credentials.dat",
            protect=_protect_for_test,
            unprotect=_unprotect_for_test,
            backup_count=4,
        )


def test_send_ledger_module_exists_for_persistent_idempotency() -> None:
    assert importlib.util.find_spec("send_ledger") is not None


def test_single_instance_module_exists_for_process_lifetime_guard() -> None:
    assert importlib.util.find_spec("single_instance") is not None


def test_send_ledger_uncertain_turn_survives_restart_and_persists_only_hashes(tmp_path: Path) -> None:
    path = tmp_path / "send_ledger.json"
    first = SendLedger(path).load()
    assert first.prepare(
        "raw-turn-customer-question",
        identity_key="raw-chat-customer-name",
        record_id="raw-record-id",
        answer_hash="raw-answer-body",
    ) is True
    first.mark_keypress("raw-turn-customer-question")
    first.mark_uncertain("raw-turn-customer-question", "bubble_verifier_unavailable")

    second = SendLedger(path).load()
    assert second.contains("raw-turn-customer-question") is True
    assert second.status("raw-turn-customer-question") == "uncertain"
    assert second.may_retry("raw-turn-customer-question") is False
    raw = path.read_text(encoding="utf-8")
    for private_text in (
        "raw-turn-customer-question",
        "raw-chat-customer-name",
        "raw-record-id",
        "raw-answer-body",
    ):
        assert private_text not in raw


def test_send_ledger_always_hashes_raw_values_even_when_they_look_like_sha256(tmp_path: Path) -> None:
    path = tmp_path / "send_ledger.json"
    raw_turn = "a" * 64
    raw_identity = "b" * 64
    raw_record_source = "c" * 64
    raw_answer = "d" * 64
    ledger = SendLedger(path).load()

    assert ledger.prepare(
        raw_turn,
        identity_key=raw_identity,
        record_id=raw_record_source,
        answer_hash=raw_answer,
    ) is True
    ledger.mark_keypress(raw_turn)
    ledger.mark_uncertain(raw_turn, "bubble_verifier_unavailable")

    candidates = [candidate for candidate in _candidate_paths(path) if candidate.exists()]
    assert len(candidates) >= 2
    for candidate in candidates:
        persisted = candidate.read_text(encoding="utf-8")
        for raw_value in (raw_turn, raw_identity, raw_record_source, raw_answer):
            assert raw_value not in persisted

    restarted = SendLedger(path).load()
    assert restarted.contains(raw_turn) is True
    assert restarted.status(raw_turn) == "uncertain"
    assert restarted.prepare(
        raw_turn,
        identity_key=raw_identity,
        record_id=raw_record_source,
        answer_hash=raw_answer,
    ) is False


def test_send_ledger_failed_turn_can_be_prepared_for_retry(tmp_path: Path) -> None:
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    assert ledger.prepare("turn", identity_key="chat", record_id="r1", answer_hash="answer") is True
    ledger.mark_failed("turn", "sender_rejected")

    assert ledger.contains("turn") is False
    assert ledger.may_retry("turn") is True
    assert ledger.prepare("turn", identity_key="chat", record_id="r1", answer_hash="answer") is True
    assert ledger.status("turn") == "prepared"


def test_send_ledger_corruption_and_write_failure_are_typed_blocking_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrupt_path = tmp_path / "corrupt-ledger.json"
    corrupt_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LedgerBlockingError):
        SendLedger(corrupt_path).load()

    ledger = SendLedger(tmp_path / "write-fail-ledger.json").load()
    monkeypatch.setattr(
        ledger_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ledger disk unavailable")),
    )
    with pytest.raises(LedgerBlockingError, match="write"):
        ledger.prepare("turn", identity_key="chat", record_id="r1", answer_hash="answer")
    assert ledger.status("turn") is None


class _FakeMutexApi:
    def __init__(self) -> None:
        self.active_names: set[str] = set()
        self.handles: dict[object, str] = {}

    def create(self, name: str) -> tuple[object, bool]:
        handle = object()
        already_exists = name in self.active_names
        if not already_exists:
            self.active_names.add(name)
        self.handles[handle] = name
        return handle, already_exists

    def close(self, handle: object) -> None:
        name = self.handles.pop(handle)
        if not any(active_name == name for active_name in self.handles.values()):
            self.active_names.discard(name)


def test_two_single_instance_guards_allow_only_one_runtime() -> None:
    api = _FakeMutexApi()
    first = single_instance.SingleInstance(single_instance.MUTEX_NAME, api=api)
    second = single_instance.SingleInstance(single_instance.MUTEX_NAME, api=api)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_non_windows_mutex_adapter_does_not_release_the_owner_on_duplicate_close() -> None:
    api = single_instance._ProcessMutexApi()
    first = single_instance.SingleInstance("isolated-adapter-name", api=api)
    duplicate = single_instance.SingleInstance("isolated-adapter-name", api=api)
    third = single_instance.SingleInstance("isolated-adapter-name", api=api)

    assert first.acquire() is True
    assert duplicate.acquire() is False
    assert third.acquire() is False
    first.release()
    assert third.acquire() is True
    third.release()


def test_main_duplicate_instance_exits_before_app_creation_and_shows_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class DuplicateGuard:
        def acquire(self) -> bool:
            events.append("acquire")
            return False

        def release(self) -> None:
            events.append("release")

    def forbidden_app_factory() -> object:
        raise AssertionError("duplicate process must exit before app/UI construction")

    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    main.main(
        instance_factory=DuplicateGuard,
        app_factory=forbidden_app_factory,
        duplicate_notifier=lambda: events.append("notice"),
    )

    assert events == ["acquire", "notice"]


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        if predicate():
            return True
        __import__("time").sleep(0.01)
    return predicate()


def test_personal_verified_ledger_prevents_resend_after_restart(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    context = "[对方] 这个怎么办？"
    replies = engine.generate(context, "客户")
    ledger_path = tmp_path / "send_ledger.json"

    first_sender = _FakeSender()
    first_app, _window = _new_managed_app(engine, SendLedger(ledger_path).load(), first_sender)
    first_app.current_context = context
    first_app._managed_preflight_context = lambda _window: context
    first_app._maybe_managed_send(context, replies)
    assert first_sender.called.wait(1.0)
    turn_key = ChatObservation(
        ChatIdentity(711, "isolated personal chat", "personal"),
        context,
        ("这个怎么办？",),
        captured_at=1.0,
        foreground=True,
    ).turn_key
    assert _wait_until(
        lambda: SendLedger(ledger_path).load().status(turn_key) == "verified"
    )

    second_sender = _FakeSender()
    second_app, _window = _new_managed_app(engine, SendLedger(ledger_path).load(), second_sender)
    second_app.current_context = context
    second_app._managed_preflight_context = lambda _window: context
    second_app._maybe_managed_send(context, replies)
    assert second_sender.called.wait(0.2) is False
    assert second_sender.calls == []


def test_corrupt_or_unwritable_ledger_blocks_personal_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _empty_engine(tmp_path)
    context = "[对方] 这个怎么办？"
    replies = engine.generate(context, "客户")

    corrupt_path = tmp_path / "corrupt-send-ledger.json"
    corrupt_path.write_text("{broken", encoding="utf-8")
    corrupt_sender = _FakeSender()
    corrupt_app, _window = _new_managed_app(engine, SendLedger(corrupt_path), corrupt_sender)
    corrupt_app.current_context = context
    corrupt_app._managed_preflight_context = lambda _window: context
    corrupt_app._maybe_managed_send(context, replies)
    assert corrupt_sender.called.wait(0.2) is False
    assert corrupt_app.settings.managed_mode == "off"

    writable_ledger = SendLedger(tmp_path / "write-fail-ledger.json").load()
    write_sender = _FakeSender()
    write_app, _window = _new_managed_app(engine, writable_ledger, write_sender)
    write_app.settings.managed_mode = "full"
    write_app.current_context = context
    write_app._managed_preflight_context = lambda _window: context
    monkeypatch.setattr(
        ledger_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ledger write unavailable")),
    )
    write_app._maybe_managed_send(context, replies)
    assert write_sender.called.wait(0.2) is False
    assert write_app.settings.managed_mode == "off"


def test_startup_baseline_suppresses_visible_old_turn(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    context = "[对方] 启动前旧问题？"
    sender = _FakeSender()
    app, window = _new_managed_app(engine, SendLedger(tmp_path / "send_ledger.json").load(), sender)
    app.current_context = context
    observation = ChatObservation(
        ChatIdentity(window.hwnd, window.title, "personal"),
        context,
        ("启动前旧问题？",),
        captured_at=1.0,
        foreground=True,
    )
    app._capture_runtime_observation = lambda *_args, **_kwargs: observation

    assert app._establish_startup_baseline() is True
    app._maybe_managed_send(context, engine.generate(context, "客户"))

    assert sender.called.wait(0.2) is False
    assert app.send_ledger.status(observation.turn_key) == "baseline"


def test_unreadable_visible_startup_turn_defers_sending_but_keeps_monitoring(tmp_path: Path) -> None:
    engine = _empty_engine(tmp_path)
    sender = _FakeSender()
    app, window = _new_managed_app(engine, SendLedger(tmp_path / "send_ledger.json").load(), sender)
    app.startup_blocking_error = ""
    app.detector = SimpleNamespace(foreground_chat=lambda: window)
    app._capture_runtime_observation = lambda *_args, **_kwargs: None
    original_auto = app.settings.auto_recognize

    assert app._establish_startup_baseline() is False
    assert app.settings.managed_mode == "full"
    assert app.settings.auto_recognize is original_auto
    assert app._startup_baseline_pending is True
    assert app.startup_blocking_error == ""
    assert sender.calls == []


def test_managed_mode_type_error_is_normalized_to_settings_validation_error() -> None:
    settings = AppSettings()
    settings.managed_mode = []  # type: ignore[assignment]

    with pytest.raises(SettingsValidationError, match="managed_mode"):
        settings.validate()


def test_formal_source_help_states_knowledge_only_contract() -> None:
    source_help = float_ui.TEXT["reply_source_tip"]
    usage_help = float_ui.TEXT["usage_body"]

    for text in (source_help, usage_help):
        assert "仅使用知识库" in text
        assert "模型仅用于候选排序与一致性判断" in text
        assert "暂未查到对应答案" in text
    assert "自动用大模型兜底" not in source_help
    assert "交给大模型生成" not in usage_help


def test_float_ui_managed_tip_matches_knowledge_only_contract() -> None:
    tip = float_ui.TEXT["managed_tip"]

    assert "仅使用知识库" in tip
    assert "模型仅用于候选排序与一致性判断" in tip
    assert "暂未查到对应答案" in tip
    assert "生成3条候选" not in tip


def test_runtime_logger_handler_marker_uses_normalized_casefold_path_key(tmp_path: Path) -> None:
    log_dir = tmp_path / "MixedCaseLogs"
    logger = runtime_logging.build_runtime_logger(log_dir)
    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1
    expected_key = runtime_logging._normalized_path_key(log_dir / "runtime.log")
    assert handlers[0]._wechat_runtime_path == expected_key

    handlers[0]._wechat_runtime_path = str(log_dir / "RUNTIME.LOG").replace("\\", "/")
    runtime_logging.build_runtime_logger(log_dir)
    owned = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]
    assert len(owned) == 1


def test_runtime_salt_creation_uses_shared_atomic_fsynced_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real_atomic_write = runtime_logging.atomic_write_bytes

    def recording_atomic_write(path: Path, payload: bytes, **kwargs: object) -> None:
        calls.append(Path(path))
        real_atomic_write(path, payload, **kwargs)

    monkeypatch.setattr(runtime_logging, "atomic_write_bytes", recording_atomic_write)
    salt = runtime_logging._load_or_create_salt(tmp_path)

    assert calls == [tmp_path / ".runtime-salt"]
    assert len(salt) == 32
    assert (tmp_path / ".runtime-salt").read_bytes() == salt


def test_corrupt_runtime_salt_raises_typed_logger_unavailable(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".runtime-salt").write_bytes(b"broken")

    with pytest.raises(runtime_logging.RuntimeLoggerUnavailableError, match="salt"):
        runtime_logging.build_runtime_logger(tmp_path)


def test_corrupt_runtime_salt_visibly_blocks_managed_send() -> None:
    log_dir = app_paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / ".runtime-salt").write_bytes(b"broken")
    main.DIAG_LOG = None

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = AppSettings()
    app.settings.managed_mode = "full"
    app.startup_blocking_error = ""
    app.memory = SimpleNamespace(assert_ready_for_managed_reply=lambda: None)
    app.queue = queue.Queue()

    assert app._managed_reply_readiness_allows_send() is False
    assert app.settings.managed_mode == "off"
    status_messages = [item[1] for item in list(app.queue.queue) if item[0] == "status"]
    assert any("诊断日志" in str(message) and "关闭" in str(message) for message in status_messages)
