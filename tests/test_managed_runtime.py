from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import importlib
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from queue import Queue

import pytest

from conversation_list import ConversationTypeDetector
from managed_reply import NO_MATCH_REPLY, ReplyDecision
from managed_runtime import SendAttempt
import send_ledger as ledger_module
from send_ledger import SendLedger
import sender as sender_module
from sender import WeChatSender


def _identity_api():
    try:
        module = importlib.import_module("chat_identity")
    except ModuleNotFoundError:
        pytest.fail("chat_identity module is required for the Phase 3 runtime")
    return (
        module.ChatIdentity,
        module.ChatObservation,
        module.StableIdentityTracker,
        module.normalize_chat_title,
        module.turn_fingerprint,
    )


def _runtime_api():
    try:
        module = importlib.import_module("managed_runtime")
    except ModuleNotFoundError:
        pytest.fail("managed_runtime module is required for the Phase 3 runtime")
    return module.ManagedReplyCoordinator, module.RuntimeAdapters


class _SendConcurrencyProbe:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()


class _RuntimeScenario:
    def __init__(
        self,
        observation,
        *,
        decision: ReplyDecision | None = None,
        fresh=None,
        verifications: tuple[str, ...] = ("verified",),
        send_performed: tuple[bool, ...] = (True,),
        send_reasons: tuple[str, ...] = ("keypress",),
        retry_safe: tuple[bool, ...] = (False,),
        send_delay: float = 0.0,
        concurrency_probe: _SendConcurrencyProbe | None = None,
    ) -> None:
        self.observation = observation
        self.decision = decision or ReplyDecision.no_answer("test_no_match")
        self.fresh = observation if fresh is None else fresh
        self.verifications = list(verifications)
        self.send_performed = list(send_performed)
        self.send_reasons = list(send_reasons)
        self.retry_safe = list(retry_safe)
        self.send_delay = send_delay
        self.concurrency_probe = concurrency_probe or _SendConcurrencyProbe()
        self.send_calls = 0
        self.verify_calls = 0

    def decide(self, question: str, context: str) -> ReplyDecision:
        assert question == self.observation.customer_turn[-1]
        assert context == self.observation.context
        return self.decision

    @staticmethod
    def validate_for_send(decision: ReplyDecision) -> bool:
        return isinstance(decision, ReplyDecision) and decision.kind != "technical_failure"

    def fresh_observation(self, _expected):
        return self.fresh

    def send(self, _identity, _text):
        module = importlib.import_module("managed_runtime")
        with self.concurrency_probe.lock:
            self.concurrency_probe.active += 1
            self.concurrency_probe.maximum = max(
                self.concurrency_probe.maximum, self.concurrency_probe.active
            )
        try:
            if self.send_delay:
                time.sleep(self.send_delay)
            performed = self.send_performed[min(self.send_calls, len(self.send_performed) - 1)]
            reason = self.send_reasons[min(self.send_calls, len(self.send_reasons) - 1)]
            safe = self.retry_safe[min(self.send_calls, len(self.retry_safe) - 1)]
            self.send_calls += 1
            return module.SendAttempt(performed, reason, retry_safe=safe)
        finally:
            with self.concurrency_probe.lock:
                self.concurrency_probe.active -= 1

    def verify(self, _expected, _text):
        value = self.verifications[min(self.verify_calls, len(self.verifications) - 1)]
        self.verify_calls += 1
        return value


def _personal_observation(title: str = "- @微信", question: str = "怎么申报？"):
    ChatIdentity, ChatObservation, _Tracker, _normalize, _fingerprint = _identity_api()
    return ChatObservation(
        identity=ChatIdentity(100, title, "personal"),
        context=f"[对方] {question}",
        customer_turn=(question,),
        captured_at=10.0,
        foreground=True,
    )


def _install_sender_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    foreground_values: tuple[int, ...],
):
    calls: list[tuple | str] = []
    clipboard = {"value": "old clipboard"}
    values = list(foreground_values)

    def foreground() -> int:
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    fake_pyautogui = SimpleNamespace(
        click=lambda *args: calls.append(("click", args)),
        hotkey=lambda *args: calls.append(("hotkey", args)),
        press=lambda *args: calls.append(("press", args)),
    )

    def copy(text: str) -> None:
        clipboard["value"] = text
        calls.append(("copy", text))

    fake_pyperclip = SimpleNamespace(paste=lambda: clipboard["value"], copy=copy)
    fake_win32gui = SimpleNamespace(
        IsIconic=lambda _hwnd: False,
        ShowWindow=lambda *_args: calls.append(("show",)),
        SetForegroundWindow=lambda hwnd: calls.append(("activate", hwnd)),
        GetForegroundWindow=foreground,
        GetWindowRect=lambda _hwnd: (0, 0, 1000, 700),
    )
    monkeypatch.setattr(sender_module, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(sender_module, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(sender_module, "win32gui", fake_win32gui)
    monkeypatch.setattr(sender_module.time, "sleep", lambda _seconds: None)
    return SimpleNamespace(hwnd=9, rect=None, platform=None), calls, clipboard


def _verify_own_bubble(before: str, after: str, expected: str) -> str:
    module = importlib.import_module("capture_ocr")
    verifier = getattr(module, "verify_own_bubble", None)
    if not callable(verifier):
        pytest.fail("capture_ocr.verify_own_bubble is required")
    return verifier(before, after, expected)


def test_same_hwnd_with_different_normalized_top_title_is_a_different_chat() -> None:
    ChatIdentity, _Observation, _Tracker, _normalize, turn_fingerprint = _identity_api()
    left = ChatIdentity(100, "张三 @微信", "personal")
    right = ChatIdentity(100, "李四 @微信", "personal")

    assert left != right
    assert left.key != right.key
    assert turn_fingerprint(left, ("同一个问题",)) != turn_fingerprint(right, ("同一个问题",))


def test_chat_identity_normalizes_title_but_rejects_ineligible_chat_types() -> None:
    ChatIdentity, _Observation, _Tracker, normalize_chat_title, _fingerprint = _identity_api()

    assert normalize_chat_title("  Ａ公司\u3000@微信  ") == "A公司 @微信"
    assert ChatIdentity(100, "  Ａ公司\u3000@微信  ", "personal").top_title == "A公司 @微信"
    with pytest.raises(ValueError, match="chat_type"):
        ChatIdentity(100, "客户联系", "system")
    with pytest.raises(ValueError, match="chat_type"):
        ChatIdentity(100, "无法识别", "unknown")
    with pytest.raises(ValueError, match="hwnd"):
        ChatIdentity(0, "张三", "personal")
    with pytest.raises(ValueError, match="top_title"):
        ChatIdentity(100, "  ", "personal")


def test_group_turn_fingerprint_includes_sender_and_chat_type() -> None:
    ChatIdentity, ChatObservation, _Tracker, _normalize, turn_fingerprint = _identity_api()
    group = ChatIdentity(100, "测试群", "group")
    personal = ChatIdentity(100, "测试群", "personal")

    zhang = turn_fingerprint(group, ("同一个问题",), sender_name="张三")
    li = turn_fingerprint(group, ("同一个问题",), sender_name="李四")
    direct = turn_fingerprint(personal, ("同一个问题",))
    assert len({zhang, li, direct}) == 3

    observation = ChatObservation(
        identity=group,
        context="[对方] 张三\n[对方] 同一个问题",
        customer_turn=("同一个问题",),
        sender_name="张三",
        captured_at=10.0,
        foreground=True,
    )
    assert observation.turn_key == zhang
    assert replace(observation, sender_name="李四").turn_key == li


def test_stability_requires_matching_identity_and_context_after_delay() -> None:
    ChatIdentity, _Observation, StableIdentityTracker, _normalize, _fingerprint = _identity_api()
    tracker = StableIdentityTracker(required_seconds=1.2)
    identity = ChatIdentity(100, "- @微信", "personal")

    assert tracker.observe(identity, "context-hash-1", now=10.0) is False
    assert tracker.observe(identity, "context-hash-1", now=11.1) is False
    assert tracker.observe(identity, "context-hash-1", now=11.2) is True
    assert tracker.observe(identity, "context-hash-2", now=12.5) is False
    assert tracker.observe(ChatIdentity(100, "其他人", "personal"), "context-hash-2", now=14.0) is False


def test_system_detection_does_not_skip_ordinary_contact_containing_broad_keywords() -> None:
    detector = ConversationTypeDetector(
        {
            "system_whitelist": ["客户联系", "管理企业"],
            "system_keywords": ["服务", "管理", "联系"],
        }
    )

    assert detector.detect("客户联系", list_avatar_type="personal") == "system"
    assert detector.detect("张三服务管理工作室", list_avatar_type="personal") == "personal"


def test_ledger_allows_only_one_retry_after_explicit_failure(tmp_path: Path) -> None:
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    assert ledger.prepare("turn", "chat", "record", "answer") is True
    ledger.mark_failed("turn", "not_sent")
    assert ledger.may_retry("turn") is True
    assert ledger.prepare("turn", "chat", "record", "answer") is True
    ledger.mark_failed("turn", "not_sent_again")

    assert ledger.may_retry("turn") is False
    assert ledger.prepare("turn", "chat", "record", "answer") is False
    assert ledger.status("turn") == "failed"


def test_ledger_prunes_only_old_terminal_entries_and_never_during_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "send_ledger.json"
    monkeypatch.setattr(ledger_module, "_utc_now", lambda: "2025-01-01T00:00:00+00:00")
    ledger = SendLedger(path).load()
    assert ledger.prepare("old-verified", "chat", "record", "answer") is True
    ledger.mark_keypress("old-verified")
    ledger.mark_verified("old-verified")

    monkeypatch.setattr(ledger_module, "_utc_now", lambda: "2026-04-01T00:00:00+00:00")
    assert ledger.prepare("in-flight", "chat", "record", "answer") is True
    now = datetime(2026, 4, 5, tzinfo=timezone.utc)
    assert ledger.prune_expired(now=now) == 0
    assert ledger.status("old-verified") == "verified"

    ledger.mark_failed("in-flight", "shutdown")
    assert ledger.prune_expired(now=now) == 1
    assert ledger.status("old-verified") is None
    assert ledger.status("in-flight") == "failed"


def test_coordinator_records_success_only_after_verified_own_bubble(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    scenario = _RuntimeScenario(observation)
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    coordinator = ManagedReplyCoordinator(ledger)

    outcome = coordinator.process(observation, RuntimeAdapters.from_object(scenario))

    assert outcome.phase == "recorded"
    assert outcome.sent is True
    assert outcome.reason == "verified"
    assert outcome.phase_trace == (
        "waiting",
        "stable",
        "matching",
        "judging",
        "preflight",
        "sending",
        "verifying",
        "recorded",
    )
    assert ledger.status(observation.turn_key) == "verified"


def test_coordinator_requires_fresh_matching_chat_identity_and_turn(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    changed = _personal_observation(title="其他人 @微信")
    scenario = _RuntimeScenario(observation, fresh=changed)
    ledger = SendLedger(tmp_path / "send_ledger.json").load()

    outcome = ManagedReplyCoordinator(ledger).process(
        observation, RuntimeAdapters.from_object(scenario)
    )

    assert outcome.sent is False
    assert outcome.reason == "chat_identity_changed"
    assert scenario.send_calls == 0
    assert ledger.status(observation.turn_key) is None


def test_coordinator_retries_explicit_not_sent_once_with_fresh_preflight(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    scenario = _RuntimeScenario(
        observation,
        verifications=("not_sent", "verified"),
        send_performed=(True, True),
    )
    ledger = SendLedger(tmp_path / "send_ledger.json").load()

    outcome = ManagedReplyCoordinator(ledger).process(
        observation, RuntimeAdapters.from_object(scenario)
    )

    assert outcome.sent is True
    assert outcome.attempt_count == 2
    assert scenario.send_calls == 2
    assert scenario.verify_calls == 2
    assert ledger.status(observation.turn_key) == "verified"


def test_coordinator_does_not_retry_a_non_retry_safe_send_attempt(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    scenario = _RuntimeScenario(
        observation,
        send_performed=(False,),
        send_reasons=("focus_lost_after_paste",),
        retry_safe=(False,),
    )
    ledger = SendLedger(tmp_path / "send_ledger.json").load()

    outcome = ManagedReplyCoordinator(ledger).process(
        observation, RuntimeAdapters.from_object(scenario)
    )

    assert outcome.sent is False
    assert outcome.reason == "send_uncertain"
    assert outcome.attempt_count == 1
    assert scenario.send_calls == 1
    assert scenario.verify_calls == 0
    assert ledger.status(observation.turn_key) == "uncertain"


def test_coordinator_never_retries_uncertain_send(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    scenario = _RuntimeScenario(observation, verifications=("uncertain", "verified"))
    ledger = SendLedger(tmp_path / "send_ledger.json").load()

    outcome = ManagedReplyCoordinator(ledger).process(
        observation, RuntimeAdapters.from_object(scenario)
    )

    assert outcome.sent is False
    assert outcome.reason == "send_uncertain"
    assert outcome.attempt_count == 1
    assert scenario.send_calls == 1
    assert scenario.verify_calls == 1
    assert ledger.status(observation.turn_key) == "uncertain"


def test_observe_mode_runs_decision_and_fresh_preflight_without_sending(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation()
    scenario = _RuntimeScenario(observation)
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    adapters = RuntimeAdapters.from_object(scenario, mode="observe")

    outcome = ManagedReplyCoordinator(ledger).process(observation, adapters)

    assert outcome.sent is False
    assert outcome.reason == "observe_only"
    assert outcome.decision is scenario.decision
    assert scenario.send_calls == 0
    assert scenario.verify_calls == 0
    assert ledger.status(observation.turn_key) is None


def test_parallel_process_calls_never_overlap_send_operations(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    first_observation = _personal_observation(title="张三 @微信", question="问题一")
    second_observation = _personal_observation(title="李四 @微信", question="问题二")
    probe = _SendConcurrencyProbe()
    first = _RuntimeScenario(first_observation, send_delay=0.05, concurrency_probe=probe)
    second = _RuntimeScenario(second_observation, send_delay=0.05, concurrency_probe=probe)
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    coordinator = ManagedReplyCoordinator(ledger)
    outcomes = []

    threads = [
        threading.Thread(
            target=lambda observation=observation, scenario=scenario: outcomes.append(
                coordinator.process(observation, RuntimeAdapters.from_object(scenario))
            )
        )
        for observation, scenario in (
            (first_observation, first),
            (second_observation, second),
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert all(outcome.sent for outcome in outcomes)
    assert probe.maximum == 1


def test_no_answer_decision_sends_only_the_exact_fixed_reply(tmp_path: Path) -> None:
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    observation = _personal_observation(question="知识库没有的问题")
    scenario = _RuntimeScenario(
        observation,
        decision=ReplyDecision.no_answer("no_eligible_candidate"),
    )
    ledger = SendLedger(tmp_path / "send_ledger.json").load()

    outcome = ManagedReplyCoordinator(ledger).process(
        observation, RuntimeAdapters.from_object(scenario)
    )

    assert outcome.sent is True
    assert outcome.decision is not None
    assert outcome.decision.text == NO_MATCH_REPLY


def test_automatic_sender_never_activates_a_background_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, calls, _clipboard = _install_sender_fakes(
        monkeypatch, foreground_values=(88,)
    )

    attempt = WeChatSender().send(target, "答案", allow_activate=False)

    assert isinstance(attempt, SendAttempt)
    assert attempt.keypress_performed is False
    assert not any(call[0] == "activate" for call in calls if isinstance(call, tuple))
    assert not any(call[0] in {"click", "hotkey", "press"} for call in calls if isinstance(call, tuple))


def test_automatic_sender_fails_closed_when_foreground_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sender_module, "win32gui", None)
    monkeypatch.setattr(
        sender_module,
        "pyautogui",
        SimpleNamespace(
            click=lambda *_args: pytest.fail("input click attempted"),
            hotkey=lambda *_args: pytest.fail("paste attempted"),
            press=lambda *_args: pytest.fail("send attempted"),
        ),
    )
    monkeypatch.setattr(
        sender_module,
        "pyperclip",
        SimpleNamespace(paste=lambda: "old", copy=lambda _text: None),
    )

    attempt = WeChatSender().send(SimpleNamespace(hwnd=9), "答案")

    assert attempt.keypress_performed is False
    assert attempt.retry_safe is True


def test_sender_rechecks_foreground_immediately_before_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, calls, _clipboard = _install_sender_fakes(
        monkeypatch, foreground_values=(9, 9, 88)
    )

    attempt = WeChatSender().send(target, "答案", allow_activate=False)

    assert attempt.keypress_performed is False
    assert not any(call[0] == "press" for call in calls if isinstance(call, tuple))


def test_sender_preserves_exact_multiline_knowledge_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, calls, clipboard = _install_sender_fakes(
        monkeypatch, foreground_values=(9,)
    )
    exact = "  第一行\n\n第二行  "

    attempt = WeChatSender().send(target, exact, allow_activate=False)

    assert attempt.keypress_performed is True
    assert ("copy", exact) in calls
    assert clipboard["value"] == "old clipboard"


def test_scroll_to_bottom_is_a_noop_for_background_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, calls, _clipboard = _install_sender_fakes(
        monkeypatch, foreground_values=(88,)
    )

    WeChatSender.scroll_chat_to_bottom(target)

    assert not any(call[0] in {"activate", "show", "press"} for call in calls if isinstance(call, tuple))


def test_managed_capture_never_activates_or_uses_screen_fallback() -> None:
    import main

    calls = []

    class OCR:
        def capture(self, _target, allow_screen_fallback=True):
            calls.append(("capture", allow_screen_fallback))
            return None

    sender = SimpleNamespace(_activate=lambda _target: calls.append(("activate",)))
    ui_queue = SimpleNamespace(put=lambda item: calls.append(("queue", item[0])))

    image = main._capture_chat_image_without_overlay(
        OCR(),
        ui_queue,
        sender,
        object(),
        allow_activate=False,
    )

    assert image is None
    assert calls == [("capture", False)]


@pytest.mark.parametrize(
    ("before", "after", "expected", "result"),
    (
        ("[对方] 问题", "[对方] 问题", "答案", "not_sent"),
        ("[对方] 问题", "[对方] 问题\n[我] 答案", "答案", "verified"),
        ("[对方] 问题", "[对方] 问题\n[我] 多 行 答案", "多行答案", "verified"),
        ("[对方] 问题", "无法识别的新区域", "答案", "uncertain"),
        ("[对方] 问题", "[对方] 问题\n[对方] 追问", "答案", "uncertain"),
        ("[对方] 问题\n[我] 答案", "[对方] 问题\n[我] 答案", "答案", "not_sent"),
    ),
)
def test_own_bubble_verification_requires_a_new_latest_own_message(
    before: str,
    after: str,
    expected: str,
    result: str,
) -> None:
    assert _verify_own_bubble(before, after, expected) == result


def test_main_bubble_verifier_polls_before_classifying_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    expected = _personal_observation()
    window = SimpleNamespace(
        hwnd=expected.identity.hwnd,
        platform=SimpleNamespace(key="wecom"),
    )
    contexts = [
        expected.context,
        expected.context,
        f"{expected.context}\n[我] 答案",
    ]
    captures = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = SimpleNamespace(foreground_chat=lambda: window)

    def source(_window):
        context = contexts.pop(0)
        captures.append(context)
        return expected.identity.top_title, "personal", context

    app._capture_runtime_source = source
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    assert app._verify_runtime_send(expected, "答案", window) == "verified"
    assert len(captures) == 3


def test_managed_runtime_worker_is_single_non_daemon_and_drains_before_shutdown(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("managed_runtime")
    observation = _personal_observation()
    scenario = _RuntimeScenario(observation, send_delay=0.02)
    ledger = SendLedger(tmp_path / "send_ledger.json").load()
    worker = module.ManagedRuntimeWorker(module.ManagedReplyCoordinator(ledger))
    outcomes = []

    worker.start()
    worker.submit(
        observation,
        module.RuntimeAdapters.from_object(scenario),
        outcomes.append,
    )
    worker.shutdown(timeout=2.0)

    assert worker.thread is not None
    assert worker.thread.daemon is False
    assert worker.thread.is_alive() is False
    assert len(outcomes) == 1
    assert outcomes[0].sent is True
    assert ledger.has_transient_entries() is False


def test_managed_runtime_worker_rejects_new_submissions_after_shutdown(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("managed_runtime")
    observation = _personal_observation()
    scenario = _RuntimeScenario(observation)
    worker = module.ManagedRuntimeWorker(
        module.ManagedReplyCoordinator(SendLedger(tmp_path / "ledger.json").load())
    )
    worker.start()
    worker.shutdown(timeout=2.0)

    assert worker.submit(
        observation,
        module.RuntimeAdapters.from_object(scenario),
    ) is False


def test_managed_runtime_shutdown_timeout_is_bounded_when_queue_is_full(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("managed_runtime")
    observation = _personal_observation()
    entered = threading.Event()
    release = threading.Event()
    scenario = _RuntimeScenario(observation)

    def blocking_decide(question: str, context: str):
        entered.set()
        release.wait(2.0)
        return scenario.decide(question, context)

    scenario.decide = blocking_decide
    worker = module.ManagedRuntimeWorker(
        module.ManagedReplyCoordinator(SendLedger(tmp_path / "ledger.json").load()),
        max_pending=1,
    )
    adapters = module.RuntimeAdapters.from_object(scenario)
    worker.start()
    assert worker.submit(observation, adapters) is True
    assert entered.wait(1.0)
    assert worker.submit(observation, adapters) is True
    shutdown_error: list[BaseException] = []

    def stop() -> None:
        try:
            worker.shutdown(timeout=0.05)
        except BaseException as error:
            shutdown_error.append(error)

    stopper = threading.Thread(target=stop)
    stopper.start()
    stopper.join(0.25)
    try:
        assert stopper.is_alive() is False
        assert len(shutdown_error) == 1
        assert isinstance(shutdown_error[0], TimeoutError)
    finally:
        release.set()
        stopper.join(1.0)
        if worker.thread is not None:
            worker.thread.join(2.0)

    assert worker.thread is not None
    assert worker.thread.is_alive() is False


def test_main_queues_personal_and_group_decisions_into_the_same_runtime_worker() -> None:
    import main

    ChatIdentity, ChatObservation, _Tracker, _normalize, _fingerprint = _identity_api()
    submitted = []

    class Worker:
        def submit(self, observation, adapters, callback=None):
            submitted.append((observation, adapters, callback))
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.managed_worker = Worker()
    app.settings = SimpleNamespace(
        managed_mode="controlled",
        managed_allowed_chats=["- @微信", "测试群"],
    )
    app.engine = SimpleNamespace(
        decide_managed=lambda _context, _turn: decision,
        managed_decision_still_valid=lambda _decision: True,
    )
    app.sender = SimpleNamespace(send=lambda *_args, **_kwargs: SendAttempt(True, "keypress"))
    app.queue = Queue()
    app.managed_send_inflight = False
    app._managed_reply_readiness_allows_send = lambda: True
    app._capture_fresh_runtime_observation = lambda expected, _window: expected
    app._verify_runtime_send = lambda _fresh, _text, _window: "verified"
    personal = ChatObservation(
        ChatIdentity(100, "- @微信", "personal"),
        "[对方] 问题",
        ("问题",),
        captured_at=1.0,
        foreground=True,
    )
    group = ChatObservation(
        ChatIdentity(100, "测试群", "group"),
        "[对方] 张三\n[对方] @客服 问题",
        ("问题",),
        sender_name="张三",
        captured_at=1.0,
        foreground=True,
    )
    decision = ReplyDecision.no_answer("no_match")

    assert app._queue_runtime_observation(personal, object()) is True
    assert app._queue_runtime_observation(
        group,
        object(),
        outgoing_text=lambda text: f"@张三 {text}",
    ) is True

    assert len(submitted) == 2
    assert submitted[0][0] is personal
    assert submitted[1][0] is group
    assert submitted[0][1].mode == "controlled"
    assert submitted[1][1].decide(group.customer_turn[-1], group.context) is decision
    assert submitted[1][1].send(group.identity, decision.text).keypress_performed is True


def test_main_shutdown_stops_runtime_worker_before_closing_ui() -> None:
    import main

    calls = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.running = True
    app.managed_worker = SimpleNamespace(
        shutdown=lambda timeout=0: calls.append(("shutdown", timeout))
    )
    app.ui = SimpleNamespace(close=lambda: calls.append(("close",)))

    app._exit()

    assert app.running is False
    assert calls == [("shutdown", 10.0), ("close",)]


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("off", False),
        ("observe", True),
        ("controlled", True),
        ("full", True),
        ("invalid", False),
    ),
)
def test_main_managed_gate_includes_observe_and_controlled_modes(
    mode: str,
    expected: bool,
) -> None:
    import main

    assert main._managed_mode_enabled(SimpleNamespace(managed_mode=mode)) is expected


def test_empty_fresh_capture_cannot_fall_back_to_cached_context() -> None:
    import main

    window = SimpleNamespace(
        hwnd=100,
        title="WeCom",
        platform=SimpleNamespace(key="wecom"),
    )
    expected = _personal_observation()
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = SimpleNamespace(foreground_chat=lambda: window)
    app.current_context = expected.context
    app._capture_runtime_source = lambda _window: (
        expected.identity.top_title,
        "personal",
        "",
    )

    assert app._capture_fresh_runtime_observation(expected, window) is None


def test_startup_baselines_only_the_detected_chat_type(tmp_path: Path) -> None:
    import main

    ChatIdentity, ChatObservation, _Tracker, _normalize, _fingerprint = _identity_api()
    window = SimpleNamespace(hwnd=100, title="WeCom", platform=SimpleNamespace(key="wecom"))
    group = ChatObservation(
        identity=ChatIdentity(100, "测试群", "group"),
        context="[对方] 张三@微信\n[对方] @客服 启动前旧问题",
        customer_turn=("启动前旧问题",),
        sender_name="张三",
        captured_at=1.0,
        foreground=True,
    )
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = SimpleNamespace(foreground_chat=lambda: window)
    app.send_ledger = SendLedger(tmp_path / "startup-ledger.json").load()
    app._capture_runtime_observation = lambda _window, chat_type=None: group
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: False)
    app.current_window = None
    app.current_context = ""

    assert app._establish_startup_baseline() is True
    assert app.send_ledger.status(group.turn_key) == "baseline"
    personal = ChatObservation(
        identity=ChatIdentity(100, "测试群", "personal"),
        context=group.context,
        customer_turn=group.customer_turn,
        captured_at=1.0,
        foreground=True,
    )
    assert app.send_ledger.status(personal.turn_key) is None


def test_startup_baseline_retries_one_transient_unreadable_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    window = SimpleNamespace(hwnd=100, title="WeCom", platform=SimpleNamespace(key="wecom"))
    observation = _personal_observation()
    captures = iter((None, observation))
    sleeps: list[float] = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.detector = SimpleNamespace(foreground_chat=lambda: window)
    app.send_ledger = SendLedger(tmp_path / "startup-retry-ledger.json").load()
    app._capture_runtime_observation = lambda _window, chat_type=None: next(captures)
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: False)
    app.current_window = None
    app.current_context = ""
    app.startup_blocking_error = ""
    app.settings = SimpleNamespace(managed_mode="observe", auto_recognize=True)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert app._establish_startup_baseline() is True
    assert sleeps == [0.25]
    assert app.send_ledger.status(observation.turn_key) == "baseline"
    assert app.startup_blocking_error == ""


def test_top_title_capture_uses_the_detector_calibration() -> None:
    import main

    crop_boxes: list[tuple[int, int, int, int]] = []

    class _Image:
        size = (1000, 700)

        def crop(self, box):
            crop_boxes.append(box)
            return object()

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.chat_type_detector = ConversationTypeDetector(
        {
            "top_title_area": {
                "x_left": 0.30,
                "x_right": 0.80,
                "y_top": 0.01,
                "y_bottom": 0.09,
            }
        }
    )
    app.ocr = SimpleNamespace(
        read_visible_text=lambda _image, limit: "- @微信" if limit == 3 else ""
    )

    assert app._read_top_title(_Image()) == "- @微信"
    assert crop_boxes == [(300, 7, 800, 63)]


def test_runtime_source_uses_the_same_platform_chat_area_as_main_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import capture_ocr
    import main

    platform_area = object()
    settings_area = object()
    captured_areas = []
    full_image = object()
    cropped_image = object()
    window = SimpleNamespace(
        platform=SimpleNamespace(key="wecom", chat_area=platform_area)
    )
    monkeypatch.setattr(capture_ocr, "_capture_window_bitmap", lambda _window: full_image)

    def crop_with_origin(image, platform_key, fallback_area):
        assert image is full_image
        assert platform_key == "wecom"
        captured_areas.append(fallback_area)
        return cropped_image, (0, 0)

    monkeypatch.setattr(
        capture_ocr,
        "_crop_detected_chat_area_with_origin",
        crop_with_origin,
    )
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(chat_area=settings_area)
    app._read_top_title = lambda _image: "- @微信"
    app.chat_type_detector = SimpleNamespace(detect=lambda _title, _image: "personal")
    app.ocr = SimpleNamespace(read_image=lambda image: "[对方] 问题" if image is cropped_image else "")

    assert app._capture_runtime_source(window) == (
        "- @微信",
        "personal",
        "[对方] 问题",
    )
    assert captured_areas == [platform_area]


def test_mainloop_return_always_stops_the_non_daemon_runtime_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    calls = []

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            calls.append(("monitor", self.daemon))

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.startup_blocking_error = ""
    app.settings = SimpleNamespace(api_key="", wecom_account_name="客服")
    app.managed_worker = SimpleNamespace(
        start=lambda: calls.append(("worker_start",)),
        shutdown=lambda timeout=0: calls.append(("worker_shutdown", timeout)),
    )
    app.ui = SimpleNamespace(
        set_status=lambda _text: None,
        root=SimpleNamespace(after=lambda *_args: None),
        mainloop=lambda: calls.append(("mainloop",)),
    )
    app.running = True
    app._establish_startup_baseline = lambda: True
    app._monitor_loop = lambda: None
    app._ui_loop = lambda: None
    monkeypatch.setattr(main.threading, "Thread", InlineThread)

    app.start()

    assert app.running is False
    assert calls == [
        ("worker_start",),
        ("monitor", True),
        ("mainloop",),
        ("worker_shutdown", 10.0),
    ]


@pytest.mark.parametrize("mode", ("observe", "controlled", "full"))
def test_personal_managed_modes_submit_to_runtime_worker_instead_of_sender(
    mode: str,
) -> None:
    import main

    observation = _personal_observation()
    decision = ReplyDecision.no_answer("no_match")
    queued = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(
        managed_mode=mode,
        managed_allowed_chats=[observation.identity.top_title],
        api_key="",
    )
    app.current_window = SimpleNamespace(
        hwnd=observation.identity.hwnd,
        title="WeCom",
        platform=SimpleNamespace(key="wecom"),
    )
    app.queue = Queue()
    app.engine = SimpleNamespace(
        last_managed_decision=decision,
        managed_decision_still_valid=lambda value: value is decision,
        last_error="",
        last_source="knowledge",
    )
    app.sender = SimpleNamespace(send=lambda *_args, **_kwargs: pytest.fail("legacy sender called"))
    app.managed_send_inflight = False
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: True)
    app._managed_reply_readiness_allows_send = lambda: True
    app._capture_runtime_observation = lambda _window, chat_type=None: observation
    app._queue_runtime_observation = (
        lambda submitted, _window, **_kwargs: queued.append(submitted)
        or True
    )

    app._maybe_managed_send(
        observation.context,
        [decision.text],
        managed_decision=decision,
    )

    assert queued == [observation]


def test_group_managed_reply_submits_same_runtime_path_without_sender_thread() -> None:
    import main

    ChatIdentity, ChatObservation, _Tracker, _normalize, _fingerprint = _identity_api()
    decision = ReplyDecision.no_answer("no_match")
    window = SimpleNamespace(hwnd=200, title="WeCom", platform=SimpleNamespace(key="wecom"))
    observation = ChatObservation(
        identity=ChatIdentity(200, "测试群", "group"),
        context="[对方] 张三@微信\n[对方] @客服 群问题",
        customer_turn=("群问题",),
        sender_name="张三",
        captured_at=1.0,
        foreground=True,
    )
    queued = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="full", managed_allowed_chats=[])
    app.queue = Queue()
    app.engine = SimpleNamespace(managed_decision_still_valid=lambda value: value is decision)
    app.group_handler = SimpleNamespace(
        build_reply=lambda sender, answer: f"@{sender} {answer}"
    )
    app.sender = SimpleNamespace(send=lambda *_args, **_kwargs: pytest.fail("legacy sender called"))
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: True)
    app._managed_reply_readiness_allows_send = lambda: True
    app._capture_runtime_observation = lambda _window, chat_type=None: observation
    app._queue_runtime_observation = (
        lambda submitted, _window, **kwargs: queued.append(
            (submitted, kwargs["outgoing_text"](decision.text))
        )
        or True
    )

    app._send_group_reply(
        window,
        "群问题",
        sender_name="张三",
        chat_identity="测试群",
    )

    assert queued == [(observation, "@张三 暂未查到对应答案")]


@pytest.mark.parametrize("transition", ("off", "memory_unavailable"))
def test_queued_main_adapter_rechecks_live_mode_and_readiness_before_send(
    transition: str,
    tmp_path: Path,
) -> None:
    import main

    observation = _personal_observation()
    decision = ReplyDecision.no_answer("no_match")
    submitted = []
    state = {"ready": True}

    class Worker:
        def submit(self, _observation, adapters, _callback):
            submitted.append(adapters)
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(
        managed_mode="full",
        managed_allowed_chats=[],
    )
    app.engine = SimpleNamespace(
        decide_managed=lambda _context, _turn: decision,
        managed_decision_still_valid=lambda value: value is decision,
    )
    sender_calls = []
    app.sender = SimpleNamespace(
        send=lambda *_args, **_kwargs: sender_calls.append(True)
        or SendAttempt(True, "keypress")
    )
    app.managed_worker = Worker()
    app.managed_send_inflight = False
    app._managed_pending_count = 0
    app.queue = Queue()
    app._capture_fresh_runtime_observation = lambda expected, _window: expected
    app._verify_runtime_send = lambda *_args: "verified"
    app._managed_reply_readiness_allows_send = lambda: state["ready"]
    live_window = SimpleNamespace(
        hwnd=observation.identity.hwnd,
        platform=SimpleNamespace(key="wecom"),
    )
    app.detector = SimpleNamespace(foreground_chat=lambda: live_window)

    assert app._queue_runtime_observation(observation, live_window) is True
    if transition == "off":
        app.settings.managed_mode = "off"
    else:
        state["ready"] = False

    assert submitted[0].validate_for_send(decision) is False
    coordinator = importlib.import_module("managed_runtime").ManagedReplyCoordinator(
        SendLedger(tmp_path / f"{transition}.json").load()
    )
    outcome = coordinator.process(observation, submitted[0])
    assert outcome.reason == "decision_changed_before_send"
    assert sender_calls == []


def test_managed_window_routing_refreshes_chat_type_on_manual_group_switch() -> None:
    import main

    window = SimpleNamespace(hwnd=300, title="WeCom", platform=SimpleNamespace(key="wecom"))
    handled = []
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.current_chat_type = "personal"
    app.current_context = ""
    app.queue = Queue()
    app._capture_runtime_source = lambda _window: (
        "人工切换后的群聊",
        "group",
        "[对方] 张三@微信\n[对方] 没有@客服",
    )
    app._handle_group_chat = lambda current, context="", full=None: handled.append(
        (current, context)
    )

    assert app._route_managed_window(window) is True
    assert app.current_chat_type == "group"
    assert handled == [(window, "[对方] 张三@微信\n[对方] 没有@客服")]


def test_start_with_managed_off_does_not_require_an_ocr_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    calls = []

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            calls.append("monitor")

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.startup_blocking_error = ""
    app.settings = SimpleNamespace(
        managed_mode="off",
        api_key="",
        wecom_account_name="客服",
    )
    app.managed_worker = SimpleNamespace(start=lambda: None, shutdown=lambda timeout=0: None)
    app.ui = SimpleNamespace(
        set_status=lambda _text: None,
        root=SimpleNamespace(after=lambda *_args: None),
        mainloop=lambda: None,
    )
    app.running = True
    app._establish_startup_baseline = lambda: pytest.fail(
        "manual-only startup must not require OCR baseline"
    )
    app._monitor_loop = lambda: None
    app._ui_loop = lambda: None
    monkeypatch.setattr(main.threading, "Thread", InlineThread)

    app.start()

    assert calls == ["monitor"]


def test_managed_manual_recognize_can_target_visible_wecom_while_assistant_has_focus() -> None:
    import main

    visible = SimpleNamespace(hwnd=200, title="测试群")
    stale = SimpleNamespace(hwnd=100, title="旧聊天")
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="observe")
    app.detector = SimpleNamespace(
        foreground_chat=lambda: None,
        any_chat_window=lambda: visible,
    )
    app.current_window = stale

    assert app._target_wechat_window() is visible


def test_managed_manual_recognize_routes_group_instead_of_using_personal_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    window = SimpleNamespace(hwnd=200, title="测试群")
    routed = []

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="observe")
    app._target_wechat_window = lambda: window
    app._route_managed_window = lambda selected: routed.append(selected) or True
    app._process_window = lambda *_args, **_kwargs: pytest.fail(
        "a managed group must not enter the personal processor"
    )
    monkeypatch.setattr(main.threading, "Thread", InlineThread)

    app._recognize_current()

    assert app.current_window is window
    assert routed == [window]


def test_managed_group_route_publishes_recognized_context_to_ui() -> None:
    import main

    window = SimpleNamespace(hwnd=200, title="测试群")
    context = "[发送者] -@微信\n[对方] @程正恒 社会团体能设多少个分会？"
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.current_context = ""
    app.current_chat_type = "personal"
    app.queue = Queue()
    app._capture_runtime_source = lambda selected: ("测试群", "group", context)
    app._handle_group_chat = lambda *_args, **_kwargs: None

    assert app._route_managed_window(window) is True

    assert app.current_context == context
    assert app.queue.get_nowait() == ("context", context)


def test_managed_idle_keeps_recognized_chat_visible_and_marks_sending_paused() -> None:
    import main

    class UI:
        def __init__(self):
            self.partner = "当前聊天：企业微信：测试群"
            self.status = "已识别当前聊天"

        def set_partner(self, value):
            self.partner = value

        def set_status(self, value):
            self.status = value

    window = SimpleNamespace(hwnd=200, title="测试群", display_title="企业微信：测试群")
    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="observe", auto_recognize=True)
    app.current_window = window
    app.current_context = "[对方] 已识别的问题"
    app.queue = Queue()
    app.queue.put(("idle", None))
    app.ui = UI()
    app.running = False

    app._ui_loop()

    assert app.ui.partner == "当前聊天：企业微信：测试群"
    assert "不在前台" in app.ui.status
    assert "暂停" in app.ui.status


def test_enabling_managed_mode_requires_baseline_before_starting_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.startup_blocking_error = ""
    app.settings = SimpleNamespace(
        managed_mode="off",
        auto_recognize=False,
        save=lambda: None,
    )
    statuses = []
    app.ui = SimpleNamespace(set_status=statuses.append)
    app._establish_startup_baseline = lambda: False
    app._target_wechat_window = lambda: pytest.fail(
        "processing must not start after baseline failure"
    )
    monkeypatch.setattr(
        main.threading,
        "Thread",
        lambda *_args, **_kwargs: pytest.fail("managed processing thread started"),
    )

    app._managed_mode_changed("full")

    assert app.settings.managed_mode == "off"


def test_main_defers_knowledge_decision_until_the_serial_runtime_worker() -> None:
    import main

    observation = _personal_observation()
    decision = ReplyDecision.no_answer("no_match")
    submitted = []
    decide_calls = []

    class Worker:
        def submit(self, submitted_observation, adapters, callback=None):
            submitted.append((submitted_observation, adapters, callback))
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="observe", managed_allowed_chats=[])
    app.engine = SimpleNamespace(
        decide_managed=lambda context, turn: decide_calls.append((context, turn))
        or decision,
        managed_decision_still_valid=lambda value: value is decision,
    )
    app.sender = SimpleNamespace(send=lambda *_args, **_kwargs: SendAttempt(True, "keypress"))
    app.managed_worker = Worker()
    app.managed_send_inflight = False
    app._managed_pending_count = 0
    app.queue = Queue()
    app._capture_fresh_runtime_observation = lambda expected, _window: expected
    app._verify_runtime_send = lambda *_args: "verified"
    app._managed_reply_readiness_allows_send = lambda: True

    assert app._queue_runtime_observation(observation, object()) is True
    assert decide_calls == []

    queued_observation, adapters, _callback = submitted[0]
    assert queued_observation is observation
    assert adapters.decide(
        observation.customer_turn[-1], observation.context
    ) is decision
    assert decide_calls == [(observation.context, list(observation.customer_turn))]


def test_same_turn_cannot_be_queued_twice_while_first_runtime_job_is_pending() -> None:
    import main
    runtime = importlib.import_module("managed_runtime")

    observation = _personal_observation()
    submissions = []

    class DeferredWorker:
        def submit(self, submitted, _adapters, callback=None):
            submissions.append((submitted, callback))
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(managed_mode="observe", managed_allowed_chats=[])
    app.engine = SimpleNamespace(
        decide_managed=lambda *_args: ReplyDecision.no_answer("no_match"),
        managed_decision_still_valid=lambda _value: True,
    )
    app.sender = SimpleNamespace(send=lambda *_args, **_kwargs: SendAttempt(True, "keypress"))
    app.managed_worker = DeferredWorker()
    app.managed_send_inflight = False
    app._managed_pending_count = 0
    app._managed_pending_turns = set()
    app._managed_pending_lock = threading.Lock()
    app.queue = Queue()
    app._capture_fresh_runtime_observation = lambda expected, _window: expected
    app._verify_runtime_send = lambda *_args: "verified"
    app._managed_reply_readiness_allows_send = lambda: True

    assert app._queue_runtime_observation(observation, object()) is True
    assert app._queue_runtime_observation(observation, object()) is False
    assert len(submissions) == 1

    first_callback = submissions[0][1]
    first_callback(
        runtime.RuntimeOutcome(
            phase=runtime.ManagedPhase.BLOCKED,
            sent=False,
            reason="fresh_observation_failed",
            turn_key=observation.turn_key,
            decision=None,
            phase_trace=("waiting", "blocked"),
        )
    )

    assert app._queue_runtime_observation(observation, object()) is True
    assert len(submissions) == 2


def test_observe_mode_evaluates_each_unchanged_turn_only_once_per_session() -> None:
    import main
    runtime = importlib.import_module("managed_runtime")

    observation = _personal_observation()
    decision = ReplyDecision.no_answer("no_match")
    submissions = []

    class ObserveWorker:
        def submit(self, submitted, _adapters, callback=None):
            submissions.append(submitted)
            if callback is not None:
                callback(
                    runtime.RuntimeOutcome(
                        phase=runtime.ManagedPhase.PREFLIGHT,
                        sent=False,
                        reason="observe_only",
                        turn_key=submitted.turn_key,
                        decision=decision,
                        phase_trace=("waiting", "preflight"),
                    )
                )
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(
        managed_mode="observe",
        managed_allowed_chats=[],
        api_key="",
    )
    app.current_window = SimpleNamespace(hwnd=100, title="WeCom")
    app.queue = Queue()
    app.engine = SimpleNamespace(
        decide_managed=lambda *_args: decision,
        managed_decision_still_valid=lambda _value: True,
    )
    app.managed_worker = ObserveWorker()
    app.managed_send_inflight = False
    app._managed_pending_count = 0
    app.observed_runtime_turns = set()
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: True)
    app._managed_reply_readiness_allows_send = lambda: True
    app._capture_runtime_observation = lambda *_args, **_kwargs: observation
    app._reply_source_label = lambda: "知识库"

    app._maybe_managed_send(observation.context, [])
    app._maybe_managed_send(observation.context, [])

    assert submissions == [observation]
    assert app.observed_runtime_turns == {observation.turn_key}


def test_observe_mode_does_not_requeue_a_startup_baseline_turn() -> None:
    import main
    runtime = importlib.import_module("managed_runtime")

    observation = _personal_observation()
    submissions = []

    class BaselineWorker:
        def submit(self, submitted, _adapters, callback=None):
            submissions.append(submitted)
            if callback is not None:
                callback(
                    runtime.RuntimeOutcome(
                        phase=runtime.ManagedPhase.BLOCKED,
                        sent=False,
                        reason="turn_already_handled",
                        turn_key=submitted.turn_key,
                        decision=None,
                        phase_trace=("waiting", "blocked"),
                    )
                )
            return True

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.settings = SimpleNamespace(
        managed_mode="observe",
        managed_allowed_chats=[],
        api_key="",
    )
    app.current_window = SimpleNamespace(hwnd=100, title="WeCom")
    app.queue = Queue()
    app.engine = SimpleNamespace(
        decide_managed=lambda *_args: pytest.fail("a baseline turn must not be decided"),
        managed_decision_still_valid=lambda _value: True,
    )
    app.managed_worker = BaselineWorker()
    app.managed_send_inflight = False
    app._managed_pending_count = 0
    app.observed_runtime_turns = set()
    app.stable_identity_tracker = SimpleNamespace(observe=lambda *_args, **_kwargs: True)
    app._managed_reply_readiness_allows_send = lambda: True
    app._capture_runtime_observation = lambda *_args, **_kwargs: observation

    app._maybe_managed_send(observation.context, [])
    app._maybe_managed_send(observation.context, [])

    assert submissions == [observation]
    assert app.observed_runtime_turns == {observation.turn_key}
