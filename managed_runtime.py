"""Pure serialized coordinator for knowledge-only managed sending."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from queue import Full, Queue
import threading
from typing import Callable, FrozenSet, Literal, TypeAlias

from chat_identity import ChatIdentity, ChatObservation, normalize_chat_title
from managed_reply import ReplyDecision
from send_ledger import LedgerBlockingError, SendLedger


Verification: TypeAlias = Literal["verified", "not_sent", "uncertain"]
ManagedMode: TypeAlias = Literal["off", "observe", "controlled", "full"]
SendWindowPreparation: TypeAlias = Literal[
    "ready",
    "user_active_elsewhere",
    "target_window_unavailable",
    "activation_failed",
]
_MANAGED_MODES = {"off", "observe", "controlled", "full"}
_SEND_WINDOW_PREPARATIONS = {
    "ready",
    "user_active_elsewhere",
    "target_window_unavailable",
    "activation_failed",
}
_VERIFICATIONS = {"verified", "not_sent", "uncertain"}


class ManagedPhase(str, Enum):
    WAITING = "waiting"
    STABLE = "stable"
    MATCHING = "matching"
    JUDGING = "judging"
    PREFLIGHT = "preflight"
    SENDING = "sending"
    VERIFYING = "verifying"
    RECORDED = "recorded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SendAttempt:
    keypress_performed: bool
    reason: str
    retry_safe: bool = False

    def __post_init__(self) -> None:
        if type(self.keypress_performed) is not bool:
            raise TypeError("keypress_performed must be a boolean")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("send attempt reason must not be empty")
        if type(self.retry_safe) is not bool:
            raise TypeError("retry_safe must be a boolean")
        if self.keypress_performed and self.retry_safe:
            raise ValueError("a keypress attempt cannot be declared retry-safe")

    def __iter__(self):
        """Keep old UI tuple-unpacking compatible during Phase 3 integration."""
        yield self.keypress_performed
        yield self.reason


@dataclass(frozen=True)
class RuntimeAdapters:
    decide: Callable[[str, str], ReplyDecision]
    validate_for_send: Callable[[ReplyDecision], bool]
    prepare_send_window: Callable[[ChatObservation], SendWindowPreparation]
    fresh_observation: Callable[[ChatObservation], ChatObservation | None]
    send: Callable[[ChatIdentity, str], SendAttempt]
    verify: Callable[[ChatObservation, str], Verification]
    mode: ManagedMode = "full"
    allowlist: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        for name in (
            "decide",
            "validate_for_send",
            "prepare_send_window",
            "fresh_observation",
            "send",
            "verify",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} adapter must be callable")
        if type(self.mode) is not str or self.mode not in _MANAGED_MODES:
            raise ValueError("managed mode is invalid")
        normalized = frozenset(normalize_chat_title(item) for item in self.allowlist)
        if any(not item for item in normalized):
            raise ValueError("allowlist entries must not be empty")
        object.__setattr__(self, "allowlist", normalized)

    @classmethod
    def from_object(
        cls,
        value: object,
        *,
        mode: ManagedMode = "full",
        allowlist: frozenset[str] = frozenset(),
    ) -> "RuntimeAdapters":
        return cls(
            decide=getattr(value, "decide"),
            validate_for_send=getattr(value, "validate_for_send"),
            prepare_send_window=getattr(value, "prepare_send_window"),
            fresh_observation=getattr(value, "fresh_observation"),
            send=getattr(value, "send"),
            verify=getattr(value, "verify"),
            mode=mode,
            allowlist=allowlist,
        )


@dataclass(frozen=True)
class RuntimeOutcome:
    phase: ManagedPhase
    sent: bool
    reason: str
    turn_key: str
    decision: ReplyDecision | None
    phase_trace: tuple[str, ...]
    attempt_count: int = 0
    verification: Verification | None = None


@dataclass(frozen=True)
class RuntimeRequest:
    observation: ChatObservation
    adapters: RuntimeAdapters
    on_outcome: Callable[[RuntimeOutcome], None] | None = None


class ManagedReplyCoordinator:
    """Own the only serialized authority that may invoke an automatic sender."""

    def __init__(self, ledger: SendLedger) -> None:
        if not isinstance(ledger, SendLedger):
            raise TypeError("ledger must be SendLedger")
        self.ledger = ledger
        self._serial_lock = threading.Lock()

    def process(
        self,
        observation: ChatObservation,
        adapters: RuntimeAdapters,
    ) -> RuntimeOutcome:
        if not isinstance(observation, ChatObservation):
            raise TypeError("observation must be ChatObservation")
        if not isinstance(adapters, RuntimeAdapters):
            raise TypeError("adapters must be RuntimeAdapters")
        with self._serial_lock:
            return self._process_locked(observation, adapters)

    def _process_locked(
        self,
        observation: ChatObservation,
        adapters: RuntimeAdapters,
    ) -> RuntimeOutcome:
        phases: list[ManagedPhase] = [ManagedPhase.WAITING]
        decision: ReplyDecision | None = None
        attempt_count = 0

        def outcome(
            phase: ManagedPhase,
            reason: str,
            *,
            sent: bool = False,
            verification: Verification | None = None,
        ) -> RuntimeOutcome:
            if not phases or phases[-1] != phase:
                phases.append(phase)
            return RuntimeOutcome(
                phase=phase,
                sent=sent,
                reason=reason,
                turn_key=observation.turn_key,
                decision=decision,
                phase_trace=tuple(item.value for item in phases),
                attempt_count=attempt_count,
                verification=verification,
            )

        if adapters.mode == "off":
            return outcome(ManagedPhase.BLOCKED, "managed_mode_off")
        if (
            adapters.mode == "controlled"
            and observation.identity.top_title not in adapters.allowlist
        ):
            return outcome(ManagedPhase.BLOCKED, "chat_not_allowed")
        try:
            if self.ledger.contains(observation.turn_key):
                return outcome(ManagedPhase.BLOCKED, "turn_already_handled")
        except LedgerBlockingError:
            return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")

        phases.append(ManagedPhase.STABLE)
        phases.append(ManagedPhase.MATCHING)
        try:
            decision = adapters.decide(
                observation.customer_turn[-1],
                observation.context,
            )
        except Exception:
            return outcome(ManagedPhase.BLOCKED, "decision_exception")
        phases.append(ManagedPhase.JUDGING)
        if not isinstance(decision, ReplyDecision):
            return outcome(ManagedPhase.BLOCKED, "invalid_decision")
        if decision.kind == "technical_failure":
            return outcome(ManagedPhase.BLOCKED, "technical_failure")

        for attempt_count in (1, 2):
            phases.append(ManagedPhase.PREFLIGHT)
            if adapters.mode != "observe":
                try:
                    valid_before_focus = adapters.validate_for_send(decision)
                except Exception:
                    valid_before_focus = False
                if valid_before_focus is not True:
                    return outcome(
                        ManagedPhase.BLOCKED,
                        "decision_changed_before_send",
                    )
                try:
                    preparation = adapters.prepare_send_window(observation)
                except Exception:
                    preparation = "activation_failed"
                if preparation not in _SEND_WINDOW_PREPARATIONS:
                    preparation = "activation_failed"
                if preparation != "ready":
                    return outcome(ManagedPhase.BLOCKED, preparation)
            try:
                fresh = adapters.fresh_observation(observation)
            except Exception:
                return outcome(ManagedPhase.BLOCKED, "fresh_observation_failed")
            preflight_error = _preflight_error(
                observation,
                fresh,
                require_foreground=adapters.mode != "observe",
            )
            if preflight_error:
                return outcome(ManagedPhase.BLOCKED, preflight_error)
            if adapters.mode == "observe":
                return outcome(ManagedPhase.PREFLIGHT, "observe_only")
            try:
                valid_for_send = adapters.validate_for_send(decision)
            except Exception:
                valid_for_send = False
            if valid_for_send is not True:
                return outcome(ManagedPhase.BLOCKED, "decision_changed_before_send")

            try:
                prepared = self.ledger.prepare(
                    observation.turn_key,
                    observation.identity.key,
                    record_id=decision.record_id or decision.kind,
                    answer_hash=decision.answer_hash or decision.text,
                )
            except LedgerBlockingError:
                return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
            if not prepared:
                return outcome(ManagedPhase.BLOCKED, "turn_already_handled")

            phases.append(ManagedPhase.SENDING)
            try:
                send_attempt = adapters.send(observation.identity, decision.text)
            except Exception:
                return self._record_possible_side_effect(
                    observation,
                    decision,
                    phases,
                    attempt_count,
                    "send_exception",
                )
            if not isinstance(send_attempt, SendAttempt):
                return self._record_possible_side_effect(
                    observation,
                    decision,
                    phases,
                    attempt_count,
                    "invalid_send_attempt",
                )
            if not send_attempt.keypress_performed:
                if not send_attempt.retry_safe:
                    try:
                        self.ledger.mark_uncertain(
                            observation.turn_key,
                            "unsafe_not_sent_state",
                        )
                    except LedgerBlockingError:
                        return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
                    return outcome(
                        ManagedPhase.BLOCKED,
                        "send_uncertain",
                        verification="uncertain",
                    )
                try:
                    self.ledger.mark_failed(observation.turn_key, "not_sent")
                except LedgerBlockingError:
                    return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
                if attempt_count == 1 and self.ledger.may_retry(observation.turn_key):
                    continue
                return outcome(
                    ManagedPhase.BLOCKED,
                    "not_sent_after_retry",
                    verification="not_sent",
                )

            try:
                self.ledger.mark_keypress(observation.turn_key)
            except LedgerBlockingError:
                return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
            phases.append(ManagedPhase.VERIFYING)
            try:
                verification = adapters.verify(fresh, decision.text)
            except Exception:
                verification = "uncertain"
            if verification not in _VERIFICATIONS:
                verification = "uncertain"
            if verification == "verified":
                try:
                    self.ledger.mark_verified(observation.turn_key)
                except LedgerBlockingError:
                    return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
                return outcome(
                    ManagedPhase.RECORDED,
                    "verified",
                    sent=True,
                    verification="verified",
                )
            if verification == "uncertain":
                try:
                    self.ledger.mark_uncertain(observation.turn_key, "send_uncertain")
                except LedgerBlockingError:
                    return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
                return outcome(
                    ManagedPhase.BLOCKED,
                    "send_uncertain",
                    verification="uncertain",
                )
            try:
                self.ledger.mark_failed(observation.turn_key, "not_sent")
            except LedgerBlockingError:
                return outcome(ManagedPhase.BLOCKED, "ledger_unavailable")
            if attempt_count == 1 and self.ledger.may_retry(observation.turn_key):
                continue
            return outcome(
                ManagedPhase.BLOCKED,
                "not_sent_after_retry",
                verification="not_sent",
            )

        return outcome(ManagedPhase.BLOCKED, "retry_exhausted")

    def _record_possible_side_effect(
        self,
        observation: ChatObservation,
        decision: ReplyDecision,
        phases: list[ManagedPhase],
        attempt_count: int,
        reason: str,
    ) -> RuntimeOutcome:
        try:
            self.ledger.mark_keypress(observation.turn_key)
            self.ledger.mark_uncertain(observation.turn_key, reason)
        except LedgerBlockingError:
            reason = "ledger_unavailable"
        phases.append(ManagedPhase.VERIFYING)
        phases.append(ManagedPhase.BLOCKED)
        return RuntimeOutcome(
            phase=ManagedPhase.BLOCKED,
            sent=False,
            reason=reason,
            turn_key=observation.turn_key,
            decision=decision,
            phase_trace=tuple(item.value for item in phases),
            attempt_count=attempt_count,
            verification="uncertain",
        )


def _preflight_error(
    expected: ChatObservation,
    fresh: ChatObservation | None,
    *,
    require_foreground: bool,
) -> str:
    if fresh is None:
        return "fresh_observation_unavailable"
    if not isinstance(fresh, ChatObservation):
        return "invalid_fresh_observation"
    if require_foreground and not fresh.foreground:
        return "window_not_foreground"
    if fresh.identity != expected.identity:
        return "chat_identity_changed"
    if not fresh.context.strip():
        return "fresh_context_unavailable"
    if fresh.turn_key != expected.turn_key:
        return "customer_turn_changed"
    return ""


class ManagedRuntimeWorker:
    """One bounded, non-daemon worker for all personal and group operations."""

    _STOP = object()

    def __init__(self, coordinator: ManagedReplyCoordinator, *, max_pending: int = 64) -> None:
        if not isinstance(coordinator, ManagedReplyCoordinator):
            raise TypeError("coordinator must be ManagedReplyCoordinator")
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self.coordinator = coordinator
        self._queue: Queue[RuntimeRequest | object] = Queue(maxsize=max_pending)
        self._state_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._accepting = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        with self._state_lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self._stop_requested.clear()
            self._accepting = True
            self.thread = threading.Thread(
                target=self._run,
                name="managed-reply-coordinator",
                daemon=False,
            )
            self.thread.start()

    def submit(
        self,
        observation: ChatObservation,
        adapters: RuntimeAdapters,
        on_outcome: Callable[[RuntimeOutcome], None] | None = None,
    ) -> bool:
        if not isinstance(observation, ChatObservation):
            raise TypeError("observation must be ChatObservation")
        if not isinstance(adapters, RuntimeAdapters):
            raise TypeError("adapters must be RuntimeAdapters")
        if on_outcome is not None and not callable(on_outcome):
            raise TypeError("on_outcome must be callable")
        with self._state_lock:
            if not self._accepting:
                return False
            try:
                self._queue.put_nowait(RuntimeRequest(observation, adapters, on_outcome))
            except Exception:
                return False
            return True

    def shutdown(self, *, timeout: float = 10.0) -> None:
        if type(timeout) not in {int, float} or float(timeout) < 0:
            raise ValueError("timeout must not be negative")
        with self._state_lock:
            thread = self.thread
            if thread is None:
                self._accepting = False
                return
            if self._accepting:
                self._accepting = False
                self._stop_requested.set()
                try:
                    self._queue.put_nowait(self._STOP)
                except Full:
                    # A full queue must not make the caller's timeout unbounded.
                    # The worker observes the stop request after draining work.
                    pass
        thread.join(float(timeout))
        if thread.is_alive():
            raise TimeoutError("managed runtime worker did not stop cleanly")

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            try:
                if request is self._STOP:
                    return
                assert isinstance(request, RuntimeRequest)
                outcome = self.coordinator.process(request.observation, request.adapters)
                if request.on_outcome is not None:
                    try:
                        request.on_outcome(outcome)
                    except Exception:
                        pass
            finally:
                self._queue.task_done()
            if self._stop_requested.is_set() and self._queue.empty():
                return
