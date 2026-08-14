# Manual Preview and Visible WeCom Safe Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让“识别当前聊天”在任何托管模式下都只生成知识库预览，同时让后台受控发送在聊小智前台时只激活已复核的可见企业微信窗口，并在第三方窗口前台时保持零抢焦点、零发送。

**Architecture:** 手动预览与后台发送拆成两个入口，只共享指定 HWND 截图、OCR、轮次提取和知识库决策等无副作用能力。后台协调器新增显式 `prepare_send_window()` 适配器；`observe` 绕过该适配器并允许对可见窗口做只读复核，`controlled/full` 则在新鲜 OCR 前先判定前台所有者，只允许目标企业微信或聊小智激活目标窗口。发送器的激活能力改为可验证的公开边界，按键前仍由原有 HWND、标题、会话类型、发送者、问题和答案合同逐项拦截。

**Tech Stack:** Python 3.11、Tkinter、pywin32、Pillow/OCR、pytest、unittest、Git、Windows 企业微信桌面端

## Global Constraints

- 项目根目录固定为 `D:\Vibe C\客服\wechat--main`，所有实现与测试必须在当前 `main` 工作区完成，确保用户能在源代码管理中看到改动。
- 保留用户现有的 `conversation_list.py` 未提交改动；每次暂存必须列出精确文件，不得使用 `git add .`。
- “识别当前聊天”在 `off/observe/controlled/full` 任一模式下都不得创建托管任务、调用窗口激活、调用发送器或写发送台账。
- 托管答案只能来自 `active` 知识记录；模型只做候选排序或一致性判断，不得生成或改写答案；无可靠命中时精确显示并发送 `暂未查到对应答案`。
- `observe` 只能做只读 OCR、知识决策和 UI 预览；激活、发送、发送台账写入次数必须恒为 0。
- `controlled/full` 只允许在目标企业微信已前台，或聊小智是前台且能激活同一已复核非零 HWND 时继续；浏览器、文档等第三方窗口前台时不得激活或发送。
- 任何 HWND、平台、OCR 标题、会话类型、群发送者、客户轮次或答案变化都必须在按键前阻断；没有验证到新我方气泡时不得记录 `verified`。
- 同一 `turn_key` 在待处理期间只能入队一次；保留现有 `verified/uncertain/not_sent` 台账语义。
- 正式 `%APPDATA%\WeChatStyleReplyAssistant` 保持只读；自动测试和现场验收使用隔离 staging 数据目录。
- 本计划只修复预览、窗口焦点与安全发送链；不执行正式知识迁移、删除正式记录、EXE 构建或 push。完成全部项目任务与验收后再统一 push。
- 每个行为边界严格执行 RED→GREEN；完成声明前必须运行完整测试、JUnit、`compileall`、正式 AppData 前后清单比对，并把过程追加到 `.workbuddy\memory\2026-08-15.md`。

---

## File and Interface Map

- Modify `main.py`: 独立手动预览入口、可见企业微信选择、托管发送窗口准备、可见/前台新鲜复核、后台轮询与 UI 状态映射。
- Modify `managed_runtime.py`: 新增 `SendWindowPreparation` 类型和 `RuntimeAdapters.prepare_send_window`，区分只读 observe 复核与需前台的发送复核。
- Modify `wechat_window.py`: 暴露原始前台 HWND、顶层 HWND 规范化，并拒绝无效、不可见或最小化窗口。
- Modify `sender.py`: 新增只激活指定已验证 HWND 且返回验证结果的 `activate_verified_target()`；自动发送继续使用 `allow_activate=False`。
- Modify `tests/test_managed_runtime.py`: 手动预览零副作用、observe、焦点策略、协调器、入队去重和主程序适配器的决定性测试。
- Modify `tests/test_core.py`: Windows 窗口可见/最小化/顶层 HWND 与既有 UI/发送回归。
- Modify `tests/test_group_chat.py`: 群聊发送者、`@` 问题、昵称行与问题正文边界回归。
- Create `.workbuddy/memory/2026-08-15.md`: 记录本次根因、变更、RED/GREEN、现场验收和遗留风险；若执行时文件已存在则只追加，不覆盖。
- Create `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/`: 保存 pytest、JUnit、compileall、AppData 清单和现场验收 JSONL；目录内不得放正式凭据或聊天原文。

### Cross-task interfaces

- `managed_runtime.SendWindowPreparation` is `Literal["ready", "user_active_elsewhere", "target_window_unavailable", "activation_failed"]`.
- `RuntimeAdapters.prepare_send_window` has signature `Callable[[ChatObservation], SendWindowPreparation]`; it is required alongside the existing `decide`、`validate_for_send`、`fresh_observation`、`send` and `verify` callbacks.
- `sender.WeChatSender.activate_verified_target(self, target: object) -> bool` activates only the supplied HWND and returns whether that HWND became foreground.
- `main.WeChatStyleReplyAssistant._manual_managed_preview(self, wechat: WeChatWindow) -> None` publishes preview events only.
- `main.WeChatStyleReplyAssistant._visible_wecom_target(self) -> WeChatWindow | None` selects foreground, then revalidated current, then discovered visible WeCom.
- `main.WeChatStyleReplyAssistant._prepare_managed_send_window(self, expected: ChatObservation, expected_window: WeChatWindow) -> SendWindowPreparation` classifies target/assistant/third-party focus and optionally activates the exact target.
- `main.WeChatStyleReplyAssistant._capture_fresh_runtime_observation(self, expected: ChatObservation, window: WeChatWindow, *, require_foreground: bool) -> ChatObservation | None` shares identity checks while making foreground optional only for read-only observe.
- `main.WeChatStyleReplyAssistant._build_runtime_adapters(self, observation: ChatObservation, window: WeChatWindow, *, outgoing_text: Callable[[str], str] | None = None) -> RuntimeAdapters` constructs callbacks but does not submit work.
- `main.WeChatStyleReplyAssistant._complete_runtime_outcome(self, outcome: RuntimeOutcome) -> None` owns diagnostics, UI copy and pending-state completion side effects.
- `main._managed_outcome_message(reason: str) -> str` maps only the known focus/preflight block reasons and returns an empty string for reasons handled by existing special cases.

### Shared test helpers

Add these queue/thread helpers once near the top of `tests/test_managed_runtime.py`; every later task uses the same definitions:

```python
def _drain_events(ui_queue: Queue) -> list[tuple[str, object]]:
    events = []
    while not ui_queue.empty():
        events.append(ui_queue.get_nowait())
    return events


def _event_value(events: list[tuple[str, object]], kind: str):
    values = [value for event_kind, value in events if event_kind == kind]
    assert values, f"missing queue event: {kind}"
    return values[-1]


class _InlineThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self) -> None:
        self.target()
```

Extend the existing `_RuntimeScenario` rather than adding a second coordinator fake:

```python
# Add `preparation: str = "ready"` to `_RuntimeScenario.__init__`.
self.preparation = preparation
self.prepare_calls = 0
self.fresh_calls = 0
self.call_order: list[str] = []

def prepare_send_window(self, _expected):
    self.prepare_calls += 1
    self.call_order.append("prepare")
    return self.preparation

def fresh_observation(self, _expected):
    self.fresh_calls += 1
    self.call_order.append("fresh")
    return self.fresh
```

At the start of the existing `send()` fake, append `"send"` to `call_order`; at the start of `verify()`, append `"verify"`. Keep the existing concurrency probe and retry sequences unchanged.

Use `_event_value(events, kind)` in the test snippets below; do not add multiple queue-draining helpers with different semantics.

---

### Task 1: Split manual recognition into a zero-side-effect knowledge preview

**Files:**
- Modify: `main.py:199-245, 516-533, 1299-1313, 1551-1557, 1647-1698`
- Test: `tests/test_managed_runtime.py:1140-1288`
- Test: `tests/test_group_chat.py`

**Interfaces:**
- Consumes: `ReplyEngine.decide_managed(conversation_text: str, target_turn: list[str] | None) -> ReplyDecision`, `_current_unreplied_turn(context: str) -> list[str]`, `GroupChatHandler` 的 `detect_at_mention`、`latest_other_message`、`extract_question`、`extract_sender_name`、`build_reply`。
- Produces: `WeChatStyleReplyAssistant._manual_managed_preview(wechat: WeChatWindow) -> None`；该方法只发布 UI 队列事件，不返回发送权限。

- [ ] **Step 1: Write failing tests proving the current manual button enters the send path**

Add one fixture factory with these exact seams: create the app through `WeChatStyleReplyAssistant.__new__`; use a `Queue`; set `settings.managed_mode` to the supplied mode and `wecom_account_name="程正恒"`; return a window with `hwnd=200` and `platform.key="wecom"`; make `_capture_runtime_source()` return title `测试群` plus either `[发送者] 张三@微信\n[对方] @程正恒 社会团体能设多少个分会？` or personal context `[对方] 社会团体能设多少个分会？`; make the engine return `ReplyDecision(kind="answer", text="批准答案", record_id="record-1", answer_hash="answer-1")` unless `no_match=True`; and install fail-on-call fakes for `managed_worker.submit`、`sender.activate_verified_target`、`sender.send` and `send_ledger.prepare`. Patch `main.threading.Thread` to `_InlineThread`. The group-handler fake must return sender `张三`, question `社会团体能设多少个分会？`, and display reply `@张三 批准答案`.

```python
@pytest.mark.parametrize("mode", ("off", "observe", "controlled", "full"))
@pytest.mark.parametrize("chat_type", ("personal", "group"))
def test_manual_managed_preview_never_queues_runtime_sends_or_writes_ledger(
    mode, chat_type, monkeypatch
):
    app, window, effects = _manual_preview_app(chat_type, monkeypatch, mode=mode)
    app._recognize_current()
    assert effects == {"worker": 0, "activate": 0, "send": 0, "ledger": 0}
    assert app.current_context
    events = _drain_events(app.queue)
    assert _event_value(events, "replies")
    assert _event_value(events, "status") == "已生成候选回复，仅预览，未发送"

def test_manual_group_preview_without_account_mention_does_not_reuse_old_candidate(
    monkeypatch,
):
    app, window, effects = _manual_preview_app("group", monkeypatch, mentioned=False)
    app._recognize_current()
    assert effects == {"worker": 0, "activate": 0, "send": 0, "ledger": 0}
    events = _drain_events(app.queue)
    assert [kind for kind, _value in events][-2:] == ["clear_replies", "status"]
    assert _event_value(events, "status") == (
        "已识别群聊；最新消息未 @ 当前账号，不生成候选"
    )

def test_manual_no_match_displays_exact_fixed_answer_without_sending(monkeypatch):
    app, _window, effects = _manual_preview_app("personal", monkeypatch, no_match=True)
    app._recognize_current()
    assert _event_value(_drain_events(app.queue), "replies") == ["暂未查到对应答案"]
    assert effects["send"] == effects["ledger"] == 0
```

- [ ] **Step 2: Run the manual-preview tests and capture the expected RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py -k "manual_managed_preview or manual_group_preview or manual_no_match" -v
```

Expected: FAIL because `_recognize_current()` currently calls `_route_managed_window()`, and group recognition can reach `_handle_group_chat()` / `_send_group_reply()`.

- [ ] **Step 3: Add precise manual-preview messages and implement the independent path**

```python
MSG.update({
    "manual_preview_ready": "已生成候选回复，仅预览，未发送",
    "manual_group_no_mention": "已识别群聊；最新消息未 @ 当前账号，不生成候选",
    "manual_ocr_empty": "未识别到聊天内容，请确认企业微信窗口可见且未最小化",
    "manual_match_error": "匹配暂不可用，未发送",
})

def _recognize_current(self) -> None:
    wechat = self._target_wechat_window()
    if not wechat:
        self.ui.set_status(MSG["no_window"])
        return
    self.current_window = wechat
    work = lambda: self._manual_managed_preview(wechat)
    threading.Thread(target=work, daemon=True).start()
```

Implement `_manual_managed_preview()` with this exact order:

1. Queue `recognizing`.
2. Call `_capture_runtime_source(wechat)` once.
3. Publish `window` and non-empty `context`; update `current_chat_type/current_context`.
4. For group: require current account name, `detect_at_mention`, non-empty sender and extracted question; build the displayed candidate with `build_reply(sender_name, decision.text)`.
5. For personal: use `_current_unreplied_turn(context)` and reject an empty turn.
6. Call `engine.decide_managed(context, list(turn))` once.
7. For `answer/no_answer`, queue one reply, `reply_source`, and `manual_preview_ready`; for `technical_failure` clear stale replies and queue `manual_match_error`.
8. Do not call `_managed_reply_readiness_allows_send`、`_queue_runtime_observation`、`managed_worker`、`sender` or `send_ledger`.

- [ ] **Step 4: Run the focused tests until GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py -k "manual_managed_preview or manual_group_preview or manual_no_match or sender_name or mention" -v
```

Expected: PASS; the effect counters remain zero and the no-match candidate is exactly `暂未查到对应答案`.

- [ ] **Step 5: Commit only the manual-preview slice**

```powershell
git add main.py tests/test_managed_runtime.py tests/test_group_chat.py
git commit -m "fix: separate manual knowledge preview from managed send"
```

---

### Task 2: Preserve recognized UI state and remove false send-failure wording

**Files:**
- Modify: `main.py:199-245, 1058-1111, 1909-1995`
- Test: `tests/test_managed_runtime.py:1251-1300`

**Interfaces:**
- Consumes: UI queue events `window/context/replies/reply_source/status/idle`.
- Produces: `idle` becomes non-destructive when `current_window` and `current_context` exist; outcome reasons map to user-facing non-misleading copy.

- [ ] **Step 1: Replace the obsolete idle expectation with failing state-retention tests**

`_recognized_ui_app(mode)` creates an app with `settings.managed_mode=mode`, `current_window.display_title="企业微信：测试群"`, non-empty `current_context`, one queued candidate already reflected in a UI fake, and `running=False` so `_ui_loop()` drains once. The UI fake stores `partner/status/context/replies/source` and implements the existing setter methods without clearing other fields.

```python
def test_managed_idle_preserves_recognized_chat_context_candidate_and_status():
    app, ui = _recognized_ui_app(mode="observe")
    ui.status = "已生成候选回复，仅预览，未发送"
    app.queue.put(("idle", None))
    app._ui_loop()
    assert ui.partner == "当前聊天：企业微信：测试群"
    assert ui.context == "[对方] 已识别的问题"
    assert ui.replies == ["候选答案"]
    assert ui.status == "已生成候选回复，仅预览，未发送"

@pytest.mark.parametrize(
    ("reason", "message"),
    (
        ("user_active_elsewhere", "检测到你正在使用其他窗口，本轮暂不发送"),
        ("activation_failed", "企业微信窗口当前无法安全激活，本轮未发送"),
        ("customer_turn_changed", "会话或问题已变化，本轮未发送"),
    ),
)
def test_runtime_block_reasons_have_precise_non_manual_messages(reason, message):
    import main
    assert main._managed_outcome_message(reason) == message
```

- [ ] **Step 2: Run the UI tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "idle_preserves or runtime_block_reasons" -v
```

Expected: FAIL because `idle` currently overwrites status with `managed_inactive`, and the completion callback emits a generic `托管发送已阻断：` prefix.

- [ ] **Step 3: Make `idle` non-destructive and centralize outcome copy**

```python
MANAGED_OUTCOME_MESSAGES = {
    "user_active_elsewhere": "检测到你正在使用其他窗口，本轮暂不发送",
    "target_window_unavailable": "企业微信窗口当前不可用，本轮未发送",
    "activation_failed": "企业微信窗口当前无法安全激活，本轮未发送",
    "chat_identity_changed": "会话或问题已变化，本轮未发送",
    "customer_turn_changed": "会话或问题已变化，本轮未发送",
    "fresh_observation_unavailable": "发送前无法重新识别当前会话，本轮未发送",
}
```

For `kind == "idle"`, keep partner/context/replies/source/status unchanged when a recognized window and non-empty context exist. Extract the body of the nested runtime `completed()` callback into `_complete_runtime_outcome(outcome: RuntimeOutcome) -> None` so the mapping is testable without a worker thread. Preserve the existing `observe_only` copy and never use foreground/send failure wording for the manual preview path.

Implement `_managed_outcome_message()` as a pure dictionary lookup over `MANAGED_OUTCOME_MESSAGES`; `_complete_runtime_outcome()` calls it only after the existing ledger-unavailable, uncertain, sent and observe-only branches.

- [ ] **Step 4: Run focused UI and completion-callback regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "idle or runtime_block or observe_only or outcome" -v
```

Expected: PASS; idle does not mutate recognized UI state.

- [ ] **Step 5: Commit the UI-state slice**

```powershell
git add main.py tests/test_managed_runtime.py
git commit -m "fix: preserve recognized preview state across idle polls"
```

---

### Task 3: Add a verified Windows focus and activation boundary

**Files:**
- Modify: `wechat_window.py:99-151`
- Modify: `sender.py:26-91`
- Test: `tests/test_core.py:4815-4910`
- Test: `tests/test_managed_runtime.py:120-180, 451-530`

**Interfaces:**
- Consumes: pywin32 `GetForegroundWindow`、`GetAncestor(hwnd, 2)`、`IsWindow`、`IsWindowVisible`、`IsIconic`、`SetForegroundWindow`.
- Produces: `ChatWindowDetector.foreground_hwnd() -> int`, `ChatWindowDetector.root_hwnd(hwnd: int) -> int`, `WeChatSender.activate_verified_target(target: object) -> bool`.

- [ ] **Step 1: Write Windows-boundary tests before implementation**

Extend `_install_sender_fakes()` with `IsWindow=lambda _hwnd: True` and `IsWindowVisible=lambda _hwnd: True`. `_install_invalid_sender_target()` uses the same call recorder but sets `IsWindow` false and makes `SetForegroundWindow` fail the test if called. `_detector_with_fake_win32()` monkeypatches `wechat_window.win32gui` with deterministic `GetForegroundWindow`、`GetAncestor`、`IsWindow`、`IsWindowVisible`、`IsIconic`、`GetWindowText` and `GetWindowRect`, while patching `_match_platform()` to return `ChatPlatform.for_key("wecom")`.

```python
def test_window_detector_rejects_invalid_hidden_and_minimized_wecom(monkeypatch):
    detector, api = _detector_with_fake_win32(monkeypatch, hwnd=9)
    for state in ("invalid", "hidden", "minimized"):
        api.set_state(state)
        assert detector.window_from_hwnd(9) is None

def test_root_hwnd_and_foreground_hwnd_are_explicit(monkeypatch):
    detector, api = _detector_with_fake_win32(monkeypatch, hwnd=91, root=90)
    assert detector.foreground_hwnd() == 91
    assert detector.root_hwnd(91) == 90

def test_activate_verified_target_activates_only_the_given_hwnd_and_confirms_focus(
    monkeypatch,
):
    target, calls, _clipboard = _install_sender_fakes(
        monkeypatch, foreground_values=(88, 9)
    )
    assert WeChatSender().activate_verified_target(target) is True
    assert [call for call in calls if isinstance(call, tuple) and call[0] == "activate"] == [
        ("activate", 9)
    ]

def test_activate_verified_target_rejects_minimized_or_invalid_target(monkeypatch):
    target, calls = _install_invalid_sender_target(monkeypatch)
    assert WeChatSender().activate_verified_target(target) is False
    assert not any(call[0] == "activate" for call in calls)
```

- [ ] **Step 2: Run detector/sender tests and capture RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_core.py tests\test_managed_runtime.py -k "rejects_invalid_hidden_and_minimized or root_hwnd or activate_verified_target" -v
```

Expected: FAIL because the detector has no raw/root HWND API, minimized windows are accepted, and the sender exposes only a private non-verifying `_activate()`.

- [ ] **Step 3: Implement fail-closed HWND inspection and verified activation**

```python
class ChatWindowDetector:
    def foreground_hwnd(self) -> int:
        if win32gui is None:
            return 0
        try:
            return int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            return 0

    @staticmethod
    def root_hwnd(hwnd: int) -> int:
        if win32gui is None or type(hwnd) is not int or hwnd <= 0:
            return 0
        try:
            return int(win32gui.GetAncestor(hwnd, 2) or hwnd)
        except Exception:
            return 0
```

In `_window_from_hwnd()`, reject when `IsWindow(hwnd)` is false, `IsWindowVisible(hwnd)` is false, or `IsIconic(hwnd)` is true. Implement activation as:

```python
def activate_verified_target(self, target: object) -> bool:
    hwnd = _target_hwnd(target)
    if win32gui is None or not hwnd:
        return False
    try:
        if (
            not win32gui.IsWindow(hwnd)
            or not win32gui.IsWindowVisible(hwnd)
            or win32gui.IsIconic(hwnd)
        ):
            return False
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return self._is_target_foreground(target)
    except Exception:
        return False
```

Do not make managed runtime pass `allow_activate=True` to `send()`; activation and post-activation OCR remain separate operations.

- [ ] **Step 4: Run the complete detector/sender regression subset**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_core.py tests\test_managed_runtime.py -k "ChatWindow or detector or sender or foreground or activate or clipboard" -v
```

Expected: PASS, including existing checks that automatic sender does not activate a background window and rechecks focus before Enter.

- [ ] **Step 5: Commit the Windows boundary**

```powershell
git add wechat_window.py sender.py tests/test_core.py tests/test_managed_runtime.py
git commit -m "feat: verify visible wecom target before activation"
```

---

### Task 4: Teach the serialized coordinator when activation is allowed

**Files:**
- Modify: `managed_runtime.py:13-85, 136-215, 343-360`
- Test: `tests/test_managed_runtime.py:32-116, 300-450`

**Interfaces:**
- Consumes: `RuntimeAdapters.prepare_send_window(expected) -> SendWindowPreparation`.
- Produces: observe preflight with `require_foreground=False`; controlled/full preflight with prepare result plus strict target-foreground verification.

- [ ] **Step 1: Extend the runtime scenario and write failing coordinator tests**

Add this runner next to `_RuntimeScenario`:

```python
def _run_scenario(tmp_path, observation, scenario, *, mode, allowlist=frozenset()):
    ManagedReplyCoordinator, RuntimeAdapters = _runtime_api()
    ledger = SendLedger(tmp_path / f"{mode}-ledger.json").load()
    adapters = RuntimeAdapters.from_object(
        scenario,
        mode=mode,
        allowlist=frozenset(allowlist),
    )
    return ManagedReplyCoordinator(ledger).process(observation, adapters), ledger
```

```python
def test_observe_accepts_visible_background_capture_without_preparing_or_sending(tmp_path):
    observation = replace(_personal_observation(), foreground=False)
    scenario = _RuntimeScenario(observation, fresh=observation)
    outcome, ledger = _run_scenario(tmp_path, observation, scenario, mode="observe")
    assert outcome.reason == "observe_only"
    assert scenario.prepare_calls == scenario.send_calls == scenario.verify_calls == 0
    assert ledger.status(observation.turn_key) is None

def test_controlled_prepares_window_before_strict_fresh_capture_and_send(tmp_path):
    observed = replace(_personal_observation(), foreground=False)
    fresh = replace(observed, foreground=True)
    scenario = _RuntimeScenario(observed, fresh=fresh, preparation="ready")
    outcome, _ledger = _run_scenario(
        tmp_path, observed, scenario, mode="controlled", allowlist={observed.identity.top_title}
    )
    assert outcome.sent is True
    assert scenario.call_order[:3] == ["prepare", "fresh", "send"]

@pytest.mark.parametrize(
    "reason", ("user_active_elsewhere", "target_window_unavailable", "activation_failed")
)
def test_send_window_prepare_failure_has_zero_fresh_send_and_ledger_terminal(
    tmp_path, reason
):
    observation = replace(_personal_observation(), foreground=False)
    scenario = _RuntimeScenario(observation, preparation=reason)
    outcome, ledger = _run_scenario(tmp_path, observation, scenario, mode="full")
    assert outcome.reason == reason
    assert scenario.fresh_calls == scenario.send_calls == 0
    assert ledger.status(observation.turn_key) is None
```

- [ ] **Step 2: Run the coordinator tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "observe_accepts_visible or controlled_prepares or prepare_failure" -v
```

Expected: FAIL because `RuntimeAdapters` lacks `prepare_send_window`, and `_process_locked()` blocks every non-foreground initial observation before deciding mode.

- [ ] **Step 3: Add the typed adapter and mode-specific preflight order**

Implement these coordinator rules exactly:

```python
if adapters.mode == "off":
    return outcome(ManagedPhase.BLOCKED, "managed_mode_off")

# Do not reject observation.foreground here.
for attempt_count in (1, 2):
    phases.append(ManagedPhase.PREFLIGHT)
    if adapters.mode != "observe":
        try:
            preparation = adapters.prepare_send_window(observation)
        except Exception:
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
```

Update `_preflight_error()` so HWND/title/type/sender/turn/context equality remains mandatory in every mode, while `fresh.foreground` is mandatory only when `require_foreground=True`. Update `RuntimeAdapters.from_object()` and every test fake with `prepare_send_window`; do not add a default callback that could silently permit sending.

- [ ] **Step 4: Run coordinator, ledger, retry and serialization regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "coordinator or observe_mode or parallel_process or ledger or retry or preflight" -v
```

Expected: PASS; `observe` has zero prepare/send/verify/ledger writes, controlled/full preserve strict fresh foreground and existing retry semantics.

- [ ] **Step 5: Commit the coordinator contract**

```powershell
git add managed_runtime.py tests/test_managed_runtime.py
git commit -m "feat: gate managed activation by runtime mode"
```

---

### Task 5: Integrate visible-window OCR, assistant-only activation and third-party focus blocking

**Files:**
- Modify: `main.py:471-533, 1299-1313, 1551-1557, 1647-1698, 1810-1905`
- Test: `tests/test_managed_runtime.py:1080-1510`

**Interfaces:**
- Consumes: Task 3 `foreground_hwnd/root_hwnd/activate_verified_target`; Task 4 `prepare_send_window` and mode-specific fresh capture.
- Produces: `_visible_wecom_target()`, `_prepare_managed_send_window()`, mode-aware `_capture_fresh_runtime_observation()` and updated `RuntimeAdapters` construction.

- [ ] **Step 1: Write deterministic main-integration RED tests without sleeps**

Use three `ChatWindow` fixtures with distinct HWND values `101/102/103`, identical `wecom` platform and valid rects. `_visible_target_case()` installs detector functions for foreground/current/discovered and records `window_from_hwnd()` calls. `_focus_app()` uses raw foreground HWND `target=100`, `assistant=200`, or `third_party=300`; `ui.root.winfo_id()` returns `200`; `root_hwnd()` returns its input; `activate_verified_target()` records only the supplied target HWND and changes the fake raw foreground to `100`; fresh capture returns a `replace(observation, foreground=True)` only after that transition. It uses a real temporary `SendLedger`, a synchronous coordinator, and an answer decision with non-empty record/answer identifiers.

```python
def test_visible_target_prefers_foreground_then_live_current_then_discovered():
    foreground, current, discovered = _three_wecom_windows()
    assert _visible_target_case(foreground, current, discovered) is foreground
    assert _visible_target_case(None, current, discovered) is current
    assert _visible_target_case(None, None, discovered) is discovered

def test_assistant_foreground_activates_exact_target_then_recaptures_same_turn():
    app, expected, calls = _focus_app(foreground="assistant")
    assert app._prepare_managed_send_window(expected, app.current_window) == "ready"
    assert calls == [("activate", expected.identity.hwnd)]
    fresh = app._capture_fresh_runtime_observation(
        expected, app.current_window, require_foreground=True
    )
    assert fresh is not None and fresh.turn_key == expected.turn_key

def test_third_party_foreground_never_activates_sends_or_writes_ledger(tmp_path):
    app, observation, calls = _focus_app(foreground="third_party", tmp_path=tmp_path)
    adapters = app._build_runtime_adapters(observation, app.current_window)
    outcome = ManagedReplyCoordinator(app.send_ledger).process(observation, adapters)
    assert outcome.reason == "user_active_elsewhere"
    assert calls == []
    assert app.send_ledger.status(observation.turn_key) is None

def test_observe_visible_target_does_not_activate_even_with_assistant_foreground(tmp_path):
    app, observation, calls = _focus_app(
        foreground="assistant", mode="observe", tmp_path=tmp_path
    )
    adapters = app._build_runtime_adapters(observation, app.current_window)
    outcome = ManagedReplyCoordinator(app.send_ledger).process(observation, adapters)
    assert outcome.reason == "observe_only"
    assert calls == []
```

Use `threading.Event` or inline workers for pending-turn tests; do not use timing sleeps to prove ordering.

- [ ] **Step 2: Run the main-integration tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py -k "visible_target_prefers or assistant_foreground_activates or third_party_foreground or observe_visible_target" -v
```

Expected: FAIL because managed monitor sees only `foreground_chat()`, strict fresh capture rejects assistant focus, and the runtime adapter has no activation preparation callback.

- [ ] **Step 3: Implement target selection and focus classification in `main.py`**

`_visible_wecom_target()` must:

1. Return `detector.foreground_chat()` when it is a valid enterprise WeChat chat.
2. Otherwise revalidate `current_window.hwnd` through `detector.window_from_hwnd()` and return it only when strict HWND/platform equality holds.
3. Otherwise return `detector.any_chat_window()`.

`_prepare_managed_send_window()` must:

```python
live_target = self.detector.window_from_hwnd(expected.identity.hwnd)
if not _same_strict_send_window(expected_window, live_target):
    return "target_window_unavailable"
foreground = self.detector.root_hwnd(self.detector.foreground_hwnd())
target_root = self.detector.root_hwnd(expected.identity.hwnd)
assistant_root = self.detector.root_hwnd(int(self.ui.root.winfo_id()))
if foreground == target_root:
    return "ready"
if not assistant_root or foreground != assistant_root:
    return "user_active_elsewhere"
if not self.sender.activate_verified_target(live_target):
    return "activation_failed"
return (
    "ready"
    if _same_strict_send_window(live_target, _current_foreground_chat(self.detector))
    else "activation_failed"
)
```

Never activate based on title alone. Never restore or activate a minimized target. The current raw foreground HWND must be classified at worker preflight time, not when the OCR observation was initially queued.

- [ ] **Step 4: Make fresh capture and monitor mode-aware**

- `_capture_fresh_runtime_observation()` with `require_foreground=False` revalidates `expected.identity.hwnd` with `window_from_hwnd()` and captures that same visible window without activation.
- With `require_foreground=True`, it additionally requires `_strict_foreground_send_window()` before capture.
- `_monitor_loop()` uses `_visible_wecom_target()` for managed modes, so side-by-side OCR continues while聊小智 has focus.
- `_route_managed_window()` remains the background group route and is never called by the manual button.
- Runtime adapter `fresh()` passes `require_foreground=self.settings.managed_mode != "observe"`.
- Runtime adapter `prepare_send_window()` delegates to `_prepare_managed_send_window(expected, window)`.
- Runtime adapter `send()` continues calling `sender.send()` with `allow_activate=False`.
- Extract the existing nested adapter closures from `_queue_runtime_observation()` into `_build_runtime_adapters(observation, window, outgoing_text=None)`. `_queue_runtime_observation()` remains the sole pending-turn/worker submission owner and calls that builder once.
- Add `from typing import Callable` to `main.py`, and import `SendWindowPreparation` from `managed_runtime`; do not use untyped string returns for `_prepare_managed_send_window()`.

- [ ] **Step 5: Run focus, group/personal, dedupe and outcome regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py -k "foreground or visible or activate or observe or controlled or group or personal or pending or same_turn or outcome" -v
```

Expected: PASS; a third-party foreground results in zero activation/send/terminal ledger, while assistant foreground allows exactly one activation of the original HWND followed by one fresh OCR.

- [ ] **Step 6: Commit the main integration**

```powershell
git add main.py tests/test_managed_runtime.py tests/test_group_chat.py
git commit -m "fix: support visible wecom OCR with safe focus handoff"
```

---

### Task 6: Full verification, protected live acceptance and dated development log

**Files:**
- Create or append: `.workbuddy/memory/2026-08-15.md`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/phase-tests.xml`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/acceptance-observe.jsonl`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/acceptance-controlled.jsonl`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/formal-appdata-before.json`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/formal-appdata-after.json`
- Create: `artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send/files.sha256.json`

**Interfaces:**
- Consumes: all Tasks 1-5.
- Produces: reproducible unit/full-suite evidence, protected AppData comparison, four live acceptance results and append-only development history.

- [ ] **Step 1: Create exact before-state evidence without mutating formal AppData**

Run this read-only snapshot; it records metadata and hashes, never file contents:

```powershell
$evidence = 'artifacts\formal-hardening\2026-08-15\visible-wecom-safe-send'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$formal = Join-Path $env:APPDATA 'WeChatStyleReplyAssistant'
$files = @(
    Get-ChildItem -LiteralPath $formal -Recurse -File -Force |
        Sort-Object FullName |
        ForEach-Object {
            [ordered]@{
                relative_path = $_.FullName.Substring($formal.Length).TrimStart('\').Replace('\', '/')
                size = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                mtime_ns = $_.LastWriteTimeUtc.Ticks * 100
            }
        }
)
[ordered]@{ root = $formal; exists = (Test-Path -LiteralPath $formal); files = $files } |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath (Join-Path $evidence 'formal-appdata-before.json') -Encoding UTF8
```

Do not copy credentials or settings contents into the artifact directory.

- [ ] **Step 2: Run focused and full automated verification against one isolated data root**

```powershell
$testRoot = Join-Path $env:TEMP ('wechat-assistant-visible-safe-send-' + [guid]::NewGuid().ToString('N'))
$env:WECHAT_ASSISTANT_DATA_DIR = Join-Path $testRoot 'data'
$env:TEMP = Join-Path $testRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $testRoot 'pycache'
New-Item -ItemType Directory -Force -Path $env:WECHAT_ASSISTANT_DATA_DIR,$env:TEMP,$env:PYTHONPYCACHEPREFIX | Out-Null
.\.venv\Scripts\python.exe -m pytest tests\test_managed_runtime.py tests\test_group_chat.py tests\test_core.py -v
.\.venv\Scripts\python.exe -m pytest tests -v --junitxml=artifacts\formal-hardening\2026-08-15\visible-wecom-safe-send\phase-tests.xml
.\.venv\Scripts\python.exe -m compileall -q .
```

Expected: both pytest commands exit 0; JUnit has zero failures/errors; compileall exits 0. Record existing warning classes separately instead of suppressing them.

- [ ] **Step 3: Verify the formal AppData tree is byte-for-byte and metadata unchanged**

Repeat Step 1 with only the output filename changed to `formal-appdata-after.json`, then run:

```powershell
$before = Get-Content -LiteralPath (Join-Path $evidence 'formal-appdata-before.json') -Raw
$after = Get-Content -LiteralPath (Join-Path $evidence 'formal-appdata-after.json') -Raw
if ($before -cne $after) { throw 'formal AppData manifest changed' }
```

Expected: exact JSON equality, which means path count and every `(relative_path, size, sha256, mtime_ns)` tuple are unchanged. If the comparison throws, stop acceptance, preserve both manifests, and diagnose before running the app again.

- [ ] **Step 4: Run manual side-by-side preview acceptance with zero send authority**

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = 'D:\Vibe C\客服\wechat--main-phase3\artifacts\formal-hardening\2026-08-14\staging-data'
.\.venv\Scripts\python.exe main.py --managed-mode observe --acceptance-report artifacts\formal-hardening\2026-08-15\visible-wecom-safe-send\acceptance-observe.jsonl
```

With聊小智 and enterprise WeChat both visible, click `识别当前聊天` once for a personal chat and once for the approved test group. Confirm context/candidate/source remain visible, status is `已生成候选回复，仅预览，未发送`, no send-preflight warning appears, and the report contains no sent event from the manual button.

- [ ] **Step 5: Run observe and controlled live acceptance in the approved test group**

1. In `observe`, send one new active-knowledge question from the test contact. Confirm exactly one `observe_only` outcome with `sent=false`, no focus activation, and no outgoing bubble.
2. Restart with `--managed-mode controlled` and the same staging directory/report `acceptance-controlled.jsonl`; keep聊小智 foreground and enterprise WeChat visible. Send one new approved question, confirm only the exact verified enterprise WeChat HWND is activated, then one reply is sent and a new own bubble is verified.
3. Put a third-party window in foreground and create another test turn. Confirm no focus stealing, no send, no terminal ledger state, and user status `检测到你正在使用其他窗口，本轮暂不发送`.
4. If any safety invariant fails, immediately return to `observe`; do not continue controlled/full acceptance.

- [ ] **Step 6: Write hashes and append the dated development log**

`files.sha256.json` must contain SHA-256 for all changed production/test files, both acceptance JSONL files, JUnit and both AppData manifests. Append these sections to `.workbuddy/memory/2026-08-15.md`:

- 用户复现现象与根因；
- 手动预览/自动发送拆分后的接口；
- 每轮 RED/GREEN 命令与结果；
- 正式 AppData before/after 计数及 mismatch；
- 现场四项验收结果，明确是否发生真实发送；
- 当前未完成的 Phase 3/EXE/发布/push 工作。

Generate `files.sha256.json` from an explicit path list so unrelated/user files are not swept in:

```powershell
$hashTargets = @(
    'main.py',
    'managed_runtime.py',
    'wechat_window.py',
    'sender.py',
    'tests\test_managed_runtime.py',
    'tests\test_core.py',
    'tests\test_group_chat.py',
    '.workbuddy\memory\2026-08-15.md',
    (Join-Path $evidence 'phase-tests.xml'),
    (Join-Path $evidence 'acceptance-observe.jsonl'),
    (Join-Path $evidence 'acceptance-controlled.jsonl'),
    (Join-Path $evidence 'formal-appdata-before.json'),
    (Join-Path $evidence 'formal-appdata-after.json')
)
$hashTargets | ForEach-Object {
    [ordered]@{
        path = $_.Replace('\', '/')
        sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
    }
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidence 'files.sha256.json') -Encoding UTF8
```

- [ ] **Step 7: Commit evidence and log without pushing**

```powershell
git add .workbuddy/memory/2026-08-15.md artifacts/formal-hardening/2026-08-15/visible-wecom-safe-send
git commit -m "test: verify manual preview and visible wecom safe send"
git status --short --branch
```

Expected: `conversation_list.py` remains the only unrelated unstaged user change; no `git push` is run.

---

## Plan Self-Review Checklist

- [ ] Manual button coverage: personal/group, mention/no-mention, no-match, technical failure, zero worker/activation/sender/ledger side effects.
- [ ] Read-only coverage: visible non-minimized enterprise WeChat OCR works with聊小智 foreground; observe never prepares, activates, sends or writes ledger.
- [ ] Send coverage: target already foreground; assistant foreground with exact-HWND activation; third-party foreground; minimized/hidden/invalid target; activation failure; fresh OCR failure; identity/sender/turn change.
- [ ] UI coverage: manual-only wording, observe-only wording, third-party wording, idle state retention, no generic send error in manual path.
- [ ] Safety coverage: pre-keypress identity/turn/answer validation, pending-turn dedupe, serialized sender, verified/uncertain/not_sent behavior.
- [ ] Data/release coverage: temporary data root, formal AppData before/after mismatch 0, no formal migration/deletion/EXE/push, dated `.workbuddy` append.
- [ ] Placeholder scan: search the plan for forbidden placeholder vocabulary from the writing-plans skill; the search returns no matches.
- [ ] Type consistency: `SendWindowPreparation` values, `prepare_send_window` signature, `require_foreground` keyword and outcome reason names match in Tasks 3-6.
