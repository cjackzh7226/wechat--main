# Serialized Safe-Send Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route personal and group automatic replies through one fail-closed state machine that verifies chat identity, foreground state, customer turn and actual outgoing bubble before recording success.

**Architecture:** Add pure chat-identity/state/ledger modules and a single coordinator that uses injected capture, decision, send and verify adapters. `main.py` becomes the Windows/UI adapter and owns one non-daemon worker; group and personal paths submit the same observation type instead of starting independent send threads.

**Tech Stack:** Python 3.11.9, dataclasses/enums, queue/threading, Windows named mutex, existing OCR/UI automation, atomic JSON ledger, pytest/unittest.

## Global Constraints

- State sequence is `waiting -> stable -> matching -> judging -> preflight -> sending -> verifying -> recorded`.
- Only one automatic operation may manipulate WeCom at a time.
- Identity is WeCom HWND plus normalized top chat name plus `personal/group`; system/unknown chats are ineligible.
- WeCom background, user typing or identity instability means no capture activation and no send.
- Send preflight must use a fresh OCR capture; cached context cannot satisfy it.
- A verified own bubble is required for success.
- Explicitly not-sent may retry once; uncertain never retries automatically.
- Startup-visible messages are baselined and never replied to.
- Personal dedupe persists by chat/current customer turn; group dedupe persists by group/sender/message.
- The repository is not Git-backed; each task ends with tests and SHA-256 evidence.

---

## File Structure

- Create `chat_identity.py`: normalized top-title/chat-type identity and stability tracking.
- Create `send_ledger.py`: persistent idempotency states backed by atomic JSON.
- Create `managed_runtime.py`: pure serialized state machine and outcome types.
- Create `single_instance.py`: Windows named mutex lifetime guard.
- Modify `main.py`: one queue/worker, startup baseline, fresh preflight, unified personal/group adapter and graceful shutdown.
- Modify `sender.py`: foreground-only automatic send and typed keypress attempt result.
- Modify `conversation_list.py`: safe chat classification, stable identity, group-idle interruption semantics and Pillow compatibility.
- Modify `group_chat_handler.py`: exact @ matching, trustworthy sender parsing and post-success dedupe integration.
- Modify `capture_ocr.py`: expose fresh capture metadata and own-bubble verification input without hidden activation.
- Modify `float_ui.py`: display managed phase, blocking error, observation decision and uncertain-send alert.
- Create `tests/test_managed_runtime.py`: identity, state, ledger, preflight, retry and concurrency tests.
- Modify `tests/test_group_chat.py`, `tests/test_conversation_list.py`, `tests/test_core.py`.

### Task 1: Define Chat Identity and Stable Observation

**Files:**
- Create: `chat_identity.py`
- Modify: `conversation_list.py:173-198, 263-360`
- Test: `tests/test_managed_runtime.py`
- Modify: `tests/test_conversation_list.py:263-278`

**Interfaces:**
- Produces: `ChatIdentity(hwnd: int, top_title: str, chat_type: ChatType)`, `ChatObservation(identity, context, customer_turn, sender_name, captured_at, foreground)`, `ChatObservation.turn_key` computed from identity/turn/sender, `StableIdentityTracker.observe(identity, context_hash, now) -> bool`.
- Produces: `normalize_chat_title(text: str) -> str`, `turn_fingerprint(identity, messages, sender_name="") -> str`.

- [ ] **Step 1: Write failing same-HWND/different-chat and stability tests**

```python
def test_same_hwnd_with_different_top_title_is_different_chat():
    left = ChatIdentity(100, "张三 @微信", "personal")
    right = ChatIdentity(100, "李四 @微信", "personal")
    assert left != right
    assert turn_fingerprint(left, ["同一个问题"]) != turn_fingerprint(right, ["同一个问题"])

def test_stability_requires_two_identical_observations_after_delay():
    tracker = StableIdentityTracker(required_seconds=1.2)
    identity = ChatIdentity(100, "- @微信", "personal")
    assert tracker.observe(identity, "h1", now=10.0) is False
    assert tracker.observe(identity, "h1", now=11.1) is False
    assert tracker.observe(identity, "h1", now=11.2) is True
    assert tracker.observe(ChatIdentity(100, "其他人", "personal"), "h1", now=12.5) is False
```

- [ ] **Step 2: Run and verify current `_same_chat_session` behavior fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_core.py -k "different_top_title or stability or same_chat_session" -v`

Expected: FAIL because the current object carries only the WeCom main title/HWND and manual chat switches can appear identical.

- [ ] **Step 3: Implement normalized identity and stable tracker**

```python
@dataclass(frozen=True)
class ChatIdentity:
    hwnd: int
    top_title: str
    chat_type: Literal["personal", "group"]

    @property
    def key(self) -> str:
        payload = f"wecom\n{self.hwnd}\n{normalize_chat_title(self.top_title)}\n{self.chat_type}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def normalize_chat_title(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()
```

`StableIdentityTracker` resets when identity or context hash changes; it returns true only after the configured 1.2-second window and a second matching sample. Remove broad system-keyword substring matching; retain exact/explicit system whitelist rules so ordinary contacts containing “服务/管理/联系” are not skipped.

- [ ] **Step 4: Run identity/classification tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_conversation_list.py -k "identity or stable or TypeDetector" -v`

Expected: PASS; system/unknown cannot instantiate an eligible automatic `ChatIdentity`.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash chat_identity.py,conversation_list.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\10-chat-identity.sha256.json -Encoding UTF8`

### Task 2: Create Persistent Send Ledger and Startup Baseline

**Files:**
- Create: `send_ledger.py`
- Test: `tests/test_managed_runtime.py`
- Modify: `main.py:123-203, 204-234`

**Interfaces:**
- Produces: `LedgerStatus = prepared|keypress|verified|uncertain|failed|baseline`, `SendLedger.contains(turn_key)`, `prepare`, `mark_keypress`, `mark_verified`, `mark_uncertain`, `mark_failed`, `baseline`.
- Consumes: `atomic_write_json`, `load_json`, `app_paths.ledger_path`.

- [ ] **Step 1: Write failing restart/idempotency/state-transition tests**

```python
def test_verified_turn_survives_restart(tmp_path):
    path = tmp_path / "send_ledger.json"
    first = SendLedger(path)
    first.prepare("turn-1", identity_key="chat-1", record_id="r1")
    first.mark_keypress("turn-1")
    first.mark_verified("turn-1")
    second = SendLedger(path).load()
    assert second.contains("turn-1") is True
    assert second.status("turn-1") == "verified"

def test_uncertain_turn_is_handled_but_not_retryable(tmp_path):
    ledger = SendLedger(tmp_path / "ledger.json")
    ledger.prepare("turn-1", "chat-1", "r1")
    ledger.mark_uncertain("turn-1", "ocr_inconclusive")
    assert ledger.contains("turn-1") is True
    assert ledger.may_retry("turn-1") is False
```

- [ ] **Step 2: Run and confirm in-memory dedupe fails restart test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "ledger or restart or uncertain" -v`

Expected: FAIL because current dedupe lives in process fields and marks some group messages before successful send.

- [ ] **Step 3: Implement validated ledger transitions and baseline**

```python
ALLOWED_TRANSITIONS = {
    None: {"prepared", "baseline"},
    "prepared": {"keypress", "failed"},
    "keypress": {"verified", "uncertain", "failed"},
    "failed": {"prepared"},
    "baseline": set(),
    "verified": set(),
    "uncertain": set(),
}
```

Persist only hashed identity/message keys, record ID, answer hash, status, retry count, timestamps and redacted reason. Startup observes the current eligible turn and calls `baseline(turn_key)` before enabling observe/send processing. Prune only entries older than 90 days and never while a transition is in progress.

- [ ] **Step 4: Run ledger corruption/recovery and baseline tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "ledger or baseline or transition" -v`

Expected: PASS; baseline and uncertain entries suppress later sending after restart.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash send_ledger.py,main.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\11-ledger-baseline.sha256.json -Encoding UTF8`

### Task 3: Build the Pure Serialized State Machine

**Files:**
- Create: `managed_runtime.py`
- Test: `tests/test_managed_runtime.py`

**Interfaces:**
- Produces: `ManagedPhase`, `Verification = verified|not_sent|uncertain`, `RuntimeOutcome`, `ManagedReplyCoordinator.process(observation, adapters) -> RuntimeOutcome`.
- Consumes adapters: `decide(question, context) -> ReplyDecision`, `fresh_observation(expected) -> ChatObservation | None`, `send(identity, text) -> SendAttempt`, `verify(expected, text) -> Verification`, `ledger: SendLedger`.

- [ ] **Step 1: Write failing happy-path, preflight and concurrency tests**

```python
def test_state_machine_records_only_after_verified_bubble(runtime_fixture):
    outcome = runtime_fixture.process(known_observation(), adapters(verification="verified"))
    assert outcome.phase == "recorded"
    assert outcome.sent is True
    assert runtime_fixture.ledger.status(outcome.turn_key) == "verified"

def test_fresh_preflight_is_required_and_identity_change_blocks(runtime_fixture):
    changed = replace(known_observation(), identity=ChatIdentity(7, "其他人", "personal"))
    outcome = runtime_fixture.process(known_observation(), adapters(fresh=changed))
    assert outcome.sent is False
    assert outcome.reason == "chat_identity_changed"

def test_parallel_submit_never_calls_send_concurrently(runtime_fixture):
    run_two_threads(runtime_fixture.process, observation_a(), observation_b())
    assert runtime_fixture.adapters.maximum_parallel_send_calls == 1
```

- [ ] **Step 2: Run and confirm no cohesive coordinator exists**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "state_machine or fresh_preflight or parallel" -v`

Expected: FAIL because current generation/group/send daemon threads have separate flags and no shared serial authority.

- [ ] **Step 3: Implement the fail-closed coordinator**

```python
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

def process(self, observation: ChatObservation, adapters: RuntimeAdapters) -> RuntimeOutcome:
    with self._serial_lock:
        return self._process_locked(observation, adapters)
```

The locked implementation checks foreground, stable identity, eligible chat type, allowlist/mode and ledger before decision. `technical_failure` blocks without sending. `no_answer` is sendable as the exact fallback. Fresh preflight must return a nonempty observation with exactly the same identity and equivalent customer-turn fingerprint; no cached substitute is accepted.

After keypress: `verified` records success; `not_sent` permits one complete fresh-preflight retry; `uncertain` records uncertain and returns a blocking alert. No exception escapes the coordinator; it becomes a redacted technical outcome while the ledger remains consistent.

- [ ] **Step 4: Run full state-transition and fault-injection matrix**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "coordinator or state or preflight or verification or retry or parallel" -v`

Expected: PASS; test traces contain exactly the prescribed phase order.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash managed_runtime.py,tests\test_managed_runtime.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\12-managed-runtime.sha256.json -Encoding UTF8`

### Task 4: Make Sender Foreground-Only and Verify Actual Own Bubble

**Files:**
- Modify: `sender.py:24-135`
- Modify: `capture_ocr.py` own-bubble verification helper area
- Test: `tests/test_managed_runtime.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `SendAttempt(keypress_performed: bool, reason: str)`, `WeChatSender.send(target, text, allow_activate=False) -> SendAttempt`, `verify_own_bubble(before_context, after_context, expected_text) -> Verification`.

- [ ] **Step 1: Write failing no-focus-steal and verification tests**

```python
def test_automatic_send_does_not_activate_background_window(sender_spies):
    sender_spies.foreground_hwnd = 999
    attempt = WeChatSender().send(target(hwnd=100), "答案", allow_activate=False)
    assert attempt.keypress_performed is False
    assert sender_spies.set_foreground_calls == []

def test_enter_key_without_new_own_bubble_is_not_success():
    assert verify_own_bubble("[对方] 问题", "[对方] 问题", "答案") == "not_sent"

def test_changed_context_without_confident_role_is_uncertain():
    assert verify_own_bubble("[对方] 问题", "无法识别的新区域", "答案") == "uncertain"
```

- [ ] **Step 2: Run and confirm current sender reports keypress as success**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_core.py -k "foreground_only or own_bubble or enter_key" -v`

Expected: FAIL because current sender activates the target and returns `True, 已发送` immediately after Enter.

- [ ] **Step 3: Implement typed attempt and post-send OCR verification**

Automatic send checks foreground both before clicking and immediately before Enter. It never calls `_activate` when `allow_activate=False`. Preserve/restore clipboard, but a clipboard restoration error does not change send verification status.

`verify_own_bubble` requires a new latest `[我]` message whose normalized text equals the expected reply or is a high-confidence OCR-equivalent. A clearly unchanged context returns `not_sent`; changed but unparseable/ambiguous context returns `uncertain`.

Modify `scroll_chat_to_bottom` to return without calling `SetForegroundWindow` unless the target is already foreground. Monitoring must never use `allow_activate=True`.

- [ ] **Step 4: Run sender/verification tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_core.py -k "sender or clipboard or foreground or verify_own" -v`

Expected: PASS; no automatic test spy observes a foreground activation call.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash sender.py,capture_ocr.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\13-sender-verification.sha256.json -Encoding UTF8`

### Task 5: Unify Personal and Group Processing in `main.py`

**Files:**
- Modify: `main.py:123-771, 1028-1180, 1258-1270, 1438-1658, 1935-1945`
- Modify: `group_chat_handler.py:35-197`
- Modify: `conversation_list.py:204-261`
- Modify: `float_ui.py` managed status display
- Test: `tests/test_managed_runtime.py`
- Modify: `tests/test_group_chat.py`, `tests/test_conversation_list.py`, `tests/test_core.py`

**Interfaces:**
- Consumes: `ChatObservation`, `ManagedReplyCoordinator`, `ManagedReplyService`, `SendLedger`.
- Produces: one `queue.Queue[ChatObservation]`, one non-daemon worker, `_capture_fresh_observation(expected_identity)`, `_verify_after_send(...)`.

- [ ] **Step 1: Write failing integration tests for mode, group identity and cached OCR**

```python
def test_observe_mode_never_calls_sender(app_fixture):
    app_fixture.settings.managed_mode = "observe"
    app_fixture.submit(known_observation())
    app_fixture.drain_worker()
    assert app_fixture.sender.calls == []
    assert app_fixture.ui.last_decision.record_id == "r1"

def test_empty_fresh_ocr_cannot_use_cached_context(app_fixture):
    app_fixture.cached_context = "[对方] 问题"
    app_fixture.ocr.next_context = ""
    app_fixture.submit(known_observation())
    app_fixture.drain_worker()
    assert app_fixture.sender.calls == []

def test_group_turn_key_includes_group_sender_and_message():
    a = group_observation("测试群", "张三", "问题")
    b = group_observation("测试群", "李四", "问题")
    assert a.turn_key != b.turn_key
```

- [ ] **Step 2: Run and confirm separate group threads/cached preflight fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py tests\test_core.py -k "observe_mode or cached_context or group_turn_key" -v`

Expected: FAIL because group sends bypass the personal preflight/inflight path and `_managed_preflight_context_or_cached` permits cache substitution.

- [ ] **Step 3: Replace automatic thread paths with one worker**

Initialize a bounded observation queue and one worker before monitoring. Both `_process_window` and `_handle_group_chat` construct `ChatObservation` and submit it; neither calls the sender or starts a send thread. Remove `_managed_preflight_context_or_cached`, in-memory handled-signature authority and early group “handled” marking.

Refresh `current_chat_type` on every stable capture, not only `_after_switch`. `system` and `unknown` are blocked. In managed modes, `_target_wechat_window` returns only `foreground_chat()`; it cannot fall back to `any_chat_window()`.

Mode rules:

```python
def mode_allows_send(mode: str, identity: ChatIdentity, allowlist: set[str]) -> bool:
    if mode in {"off", "observe"}:
        return False
    if mode == "controlled":
        return normalize_chat_title(identity.top_title) in allowlist
    return mode == "full"
```

In observe mode, run through decision and preflight simulation but never call sender. Show record ID/scope/score or exact fallback in the UI without exposing private data to logs.

Fix group behavior: @ target must equal the normalized full account name, not a prefix; latest sender parsing requires a recognized sender header; no-@ group idle may trigger list scanning without being immediately rejected as an interruption. Mark the group ledger only after verified success.

- [ ] **Step 4: Run personal/group/UI integration tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py tests\test_conversation_list.py tests\test_core.py -k "managed or group or observe or preflight or chat_type or bypass" -v`

Expected: PASS; all sender calls originate from the coordinator worker.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash main.py,group_chat_handler.py,conversation_list.py,float_ui.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\14-main-group-integration.sha256.json -Encoding UTF8`

### Task 6: Single Instance and Graceful Shutdown

**Files:**
- Create: `single_instance.py`
- Modify: `main.py:204-212, 1249-1270`
- Test: `tests/test_managed_runtime.py`

**Interfaces:**
- Produces: `SingleInstance(name)`, `acquire() -> bool`, `release() -> None`, context-manager support.
- Consumes: Windows kernel32 named mutex; formal name `Local\\WeChatStyleReplyAssistant.ManagedRuntime.v1`.

- [ ] **Step 1: Write failing duplicate-instance and worker-shutdown tests**

```python
def test_second_mutex_instance_is_rejected(fake_mutex_api):
    first = SingleInstance(MUTEX_NAME, api=fake_mutex_api)
    second = SingleInstance(MUTEX_NAME, api=fake_mutex_api)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()

def test_shutdown_waits_for_worker_and_does_not_abandon_prepared_entry(app_fixture):
    app_fixture.submit(known_observation())
    app_fixture.exit()
    assert app_fixture.worker.is_alive() is False
    assert app_fixture.ledger.has_transient_entries() is False
```

- [ ] **Step 2: Run and confirm multiple instances/daemon shutdown are unsafe**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "mutex or shutdown" -v`

Expected: FAIL because no process lock exists and current daemon threads can be killed mid-operation.

- [ ] **Step 3: Implement mutex lifetime and ordered shutdown**

Call `CreateMutexW`, reject `ERROR_ALREADY_EXISTS`, retain the handle for the entire UI process and close it exactly once. On duplicate start, show a small message that the existing app is already running and exit before loading/sending.

Shutdown order: set stop event, stop new monitoring submissions, put one sentinel into the worker queue, wait up to 10 seconds, persist a failed/uncertain terminal state for any in-flight ledger entry, close UI, release mutex. The coordinator worker is non-daemon; diagnostic-only background threads may remain daemonized if they never send or write critical state.

- [ ] **Step 4: Run concurrency, restart and full existing tests in isolation**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py tests\test_conversation_list.py tests\test_core.py -v`

Expected: PASS; no transient ledger records remain after controlled shutdown tests.

- [ ] **Step 5: Write phase evidence**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py tests\test_conversation_list.py tests\test_core.py --junitxml=artifacts\formal-hardening\2026-08-12\phase-03-runtime.xml`

Expected: exit code 0. Record hashes for all runtime modules in `phase-03-files.sha256.json` and verify the formal `memory.json` hash matches the pre-phase hash.
