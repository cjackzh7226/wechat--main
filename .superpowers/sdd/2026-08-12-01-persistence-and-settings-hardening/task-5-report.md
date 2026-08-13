# Task 5 报告：隐私安全轮转日志与启动闭锁

## Status

PASS。任务 5 已完成；未提前实施知识 schema 或 state machine 重构。

- 提交：N/A（项目为 non-Git，未初始化、未提交）。
- 生产代码范围：仅新增 `runtime_logging.py`，定点修改 `main.py`。
- 测试范围：仅修改 `tests/test_persistence.py`。
- 证据制品：按简报生成 phase-01 JUnit 与 SHA-256 manifest。

## 实现摘要

### 1. 隐私安全 JSONL 轮转日志

- `runtime_logging.build_runtime_logger(log_dir)` 使用 `RotatingFileHandler`：
  - `maxBytes=2_000_000`
  - `backupCount=5`
  - `encoding="utf-8"`
- 同一路径使用稳定 logger 名称和 handler 所有权标记，重复调用不会叠加 handler。
- 每条日志为独立、可解析 JSON；未提供 timestamp 时补 UTC ISO-8601 timestamp。
- 默认仅保留：`record_id`、`score`、`org_scope`、`state`、`error_category`、`event`、`timestamp`。
- `api_key`、`provider_api_keys`、`Authorization`、`proxy-Authorization` 及大小写、连字符、嵌套变体递归删除。
- `chat_name`、`message`、`reply`、`context` 及 `customer_chat_name`、`message_text`、`reply_content`、`request_context` 等规范化别名递归删除；无键列表标量默认删除。
- one-session debug 仅由当前进程显式调用 `enable_session_debug()` 或命令行 `--debug-session` 开启；私密内容仅写安装本地 salt 的 HMAC-SHA256 摘要，key/authorization 仍完全删除。开关不写 settings 或 marker 文件，进程退出即失效；`.runtime-salt` 持久化只用于安装级不可逆摘要。
- `main.py` 导入时 `DIAG_LOG=None`；只有首次 `_diag()` 才通过 `app_paths.logs_dir()` 初始化日志。旧 `managed_send.log` 不再由导入创建。

### 2. typed 启动错误 fail-closed

- 新增 `StartupState(settings, memory, managed_mode, blocking_error, error_type)` 与 `load_startup_state_for_test()`。
- 复用并区分：
  - `SettingsValidationError`
  - `JsonDataError`
  - `VectorIndexError`
- 现有 `settings._load_document()` 对纯语法损坏会泄漏 `JSONDecodeError`；`main.py` 在边界将其归一为 `JsonDataError` 类别，未修改 `settings.py`。
- 任一上述启动失败均：
  - 设置 `managed_mode="off"`，同步关闭 `auto_recognize` 与 `training_mode`；
  - 返回中文 `blocking_error`；
  - 不创建空 settings/memory/vector replacement；
  - UI 仍进入 mainloop，供手动诊断，但不启动后台 monitor。
- 阻断实例在 UI 创建前安装只读 guard：`settings.save` 不落盘；managed/auto/training 入口恢复 off/false、显示原 blocking_error，且不查找发送目标、不启动线程。这样旧 `settings.managed_auto_reply` 兼容属性不能绕过 `managed_mode="off"`。

### 3. 托管发送共享向量健康守卫

- 新增共享 `_managed_reply_readiness_allows_send()`，直接消费 `StyleMemory.assert_ready_for_managed_reply()`。
- 个人会话 `_maybe_managed_send()` 与群聊 `_send_group_reply()` 均在创建发送线程前调用。
- `vector_ready=False` 或启动闭锁时：
  - 当前进程的 `managed_mode` 强制为 off；
  - sender 调用数为 0；
  - UI queue 收到中文可见原因；
  - 不保存损坏设置。

## 新增测试名

1. `test_runtime_event_redacts_private_values_and_keeps_audit_fields`
2. `test_runtime_event_recursively_drops_nested_private_and_credential_key_variants`
3. `test_runtime_logger_rotates_at_exact_limit_and_writes_parseable_jsonl`
4. `test_runtime_logger_does_not_duplicate_handler_for_same_log_dir`
5. `test_importing_main_does_not_create_runtime_log`
6. `test_one_session_debug_hashes_private_content_and_never_logs_credentials`
7. `test_settings_typed_startup_error_returns_visible_managed_off_state_without_replacement`（2 个参数实例：`JsonDataError` / `SettingsValidationError`）
8. `test_memory_json_startup_error_returns_visible_managed_off_state_without_replacement`
9. `test_vector_startup_error_returns_visible_managed_off_state_without_replacement`
10. `test_blocking_startup_enters_manual_ui_without_starting_background_monitor`
11. `test_managed_send_blocks_before_sender_when_vector_index_is_not_ready`
12. `test_group_managed_send_blocks_before_sender_when_vector_index_is_not_ready`
13. `test_blocking_startup_rejects_managed_callback_without_overwriting_corrupt_settings_or_starting_thread`
14. `test_blocking_startup_rejects_auto_and_training_ui_save_before_callback`（2 个参数实例：auto / training）

另新增 autouse fixture `isolate_data_root_and_preserve_formal_files`：每个 persistence 测试固定临时 `WECHAT_ASSISTANT_DATA_DIR`，并对 setup 时解析出的真实 AppData 根中 memory/settings/vector/log 相关文件逐文件核对集合、SHA-256、`mtime_ns`。

## TDD RED / GREEN 实际证据

### Cycle A：基础隐私过滤

- RED：`test_runtime_event_redacts_private_values_and_keeps_audit_fields`
  - `1 failed`
  - 原因：`runtime_logging` 不存在，`find_spec(...) is not None` 失败。
- GREEN：同测试 `1 passed`。

### Cycle B：轮转、重复 handler、惰性导入、session debug

- RED：4 项选择运行。
  - 初次：`3 failed, 1 passed`；轮转 handler 为 0、缺少 `enable_session_debug`。
  - 强化导入断言后：`test_importing_main_does_not_create_runtime_log` 单独 `1 failed`，证实旧 `main` 导入会在临时 `%APPDATA%` 创建 `managed_send.log`。
- GREEN：日志相关 `5 passed`；加入递归测试后为 `6 passed`。

### Cycle C：settings/memory/vector 启动闭锁与手动 UI

- RED：`4 failed`，均因缺少 `load_startup_state_for_test`；加入 UI 行为后共 5 项待实现。
- 首轮 GREEN：`4 passed, 1 failed`；剩余失败追踪到 `settings._load_document()` 对坏 JSON 泄漏 `JSONDecodeError`。
- 最终 GREEN：`5 passed`。

### Cycle D：个人托管向量 guard

- RED：`1 failed`，真实 sender 记录到 1 次调用。
- GREEN：`1 passed`，sender 调用为 0，managed mode 为 off，中文原因可见。

### Cycle E：递归别名、群聊 guard、阻断 managed 回调

- RED：`3 failed`：
  - `message_text/customer_chat_name/...` 与无键列表内容仍落盘；
  - 群聊 sender 实际调用 1 次；
  - 阻断 managed 回调仍进入目标窗口检查。
- GREEN：`3 passed`。

### Cycle F：阻断 auto/training UI 的“先保存再回调”顺序

- RED：`2 failed`，缺少 `_install_blocking_startup_guards`。
- GREEN：auto/training 两个参数实例均通过；与 managed 回调合并选择运行结果 `3 passed`。

### 全量回归中发现并修复的兼容回归

- 首次最终全量：`365 passed, 3 failed`；既有测试使用 `SimpleNamespace` 调用 `_managed_changed`，没有 `startup_blocking_error` 属性。
- 根因修复：改为 `getattr(self, "startup_blocking_error", "")`。
- 定向复验：`5 passed, 2 subtests passed`。

## 最终验证

### 阶段全量与 JUnit

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py --junitxml=artifacts\formal-hardening\2026-08-12\phase-01-persistence.xml
```

实际结果：

```text
collected 368 items
368 passed in 14.62s
exit code 0
```

JUnit 标准 XML 解析复验：

```text
JUNIT_PARSE=PASS
JUNIT_TESTS=374
JUNIT_FAILURES=0
JUNIT_ERRORS=0
```

`374` 包含 pytest 报告的 368 项和 6 个 unittest subtests。

### 正式 AppData before / after

最终全量命令外层单独对真实 `%APPDATA%\WeChatStyleReplyAssistant` 中 memory/settings/vector/log 相关文件记录相对路径、文件 SHA-256、UTC mtime ticks，并在测试前后聚合 SHA-256：

```text
FORMAL_BEFORE_COUNT=25
FORMAL_AFTER_COUNT=25
FORMAL_BEFORE_MANIFEST_SHA256=64f08a28a24d668fa55ef0fc62886e8ebe4cc5c74bc91804b65ad4c2b87dd057
FORMAL_AFTER_MANIFEST_SHA256=64f08a28a24d668fa55ef0fc62886e8ebe4cc5c74bc91804b65ad4c2b87dd057
FORMAL_UNCHANGED=True
```

最终全量使用临时根：

```text
C:\Users\14913\AppData\Local\Temp\wechat-task5-final3-5bd4e89fdb74407fbfc2ae15067ea0ea
```

### compileall

```powershell
.\.venv\Scripts\python.exe -m compileall -q -f -x '(^|[\\/])(\.venv|build|dist|__pycache__)([\\/]|$)' .
```

实际：`COMPILEALL_EXIT=0`。

### phase-01 制品

- `artifacts/formal-hardening/2026-08-12/phase-01-persistence.xml`
  - SHA-256：`e735c12d7bd10eee4945e00a2fe069920a2f15a879f7a10de301ad3d48f43f4e`
- `artifacts/formal-hardening/2026-08-12/phase-01-files.sha256.json`
  - 8 个必需文件全部存在且复算匹配：`HASH_MISMATCH_COUNT=0`
  - manifest SHA-256：`d8aa7c4845fb8c68f3aa9889610c4bdee68b848804c8e7d3049e1c2116a6d04f`

## 独立代码审查

- reviewer 首轮指出：私密别名/无键列表、群聊 guard、阻断 UI 回调三处边界。
- 修复后 reviewer 再指出 auto/training 会在回调前保存；随后安装只读 settings guard 并补参数化测试。
- reviewer 最终结论：PASS；上述问题均关闭，无剩余 Critical / Important；reviewer 未修改文件。

## 自检顾虑

- 未运行真实 Windows Tk GUI 点击式 E2E；UI 闭锁以真实主流程方法、持久化字节不变、线程禁止和 fake UI 变量/状态进行自动化验证。完整阶段回归与 compileall 均通过。
- 安装本地 `.runtime-salt` 会持久化，这是不可逆摘要稳定性所需；持久化的不是 debug 开关，debug 开关仍只存在于当前进程内存。
- `main.py` 仍是历史大文件；本任务仅做定点接入，没有进行后续知识 schema/state machine 重构。
- 正式 AppData 证据严格按简报指定的 memory/settings/vector/log 范围；每个 persistence 测试和每次阶段全量均使用临时数据根。

---

# Fix Round 1（2026-08-12 根级审查修复）

## 状态

- 已按 `task-5-review.md` 关闭 C1、C2、I1、I2、I3 的实现与回归缺口；本报告不自行宣告根级 review PASS，等待根级复审。
- 未启动企业微信真实发送，未创建空的正式替代数据，正式 AppData 全目录保持不变。

## 改动摘要

- `runtime_logging.py`：改为顶层审计字段的 schema 正向允许；字符串字段限制为短 ASCII token，`record_id` 限制为预期 ID 格式，`score` 仅有限数值，`timestamp` 仅 ISO 时间；嵌套映射/列表与未知字段不序列化。debug 仅对四个明确私密字段输出本地 salt HMAC，credential 别名永不落盘。新增模块锁保护同路径 handler 的检查/创建/安装。
- `main.py`：共享发送 guard 强制 `managed_mode == "full"`、memory 存在且 `assert_ready_for_managed_reply()` 通过；个人路径在 delay/preflight 之后、群聊路径在 worker 内都于紧邻 `sender.send()` 前二次复验。`DIAG_LOG` 采用 double-check lock。启动层捕获新的 typed persistence error，返回中文可见的 managed-off blocking state。
- `settings.py`：新增 `SettingsPersistenceError`，将设置编码/I/O 与凭据读写失败归一化传播；v1 迁移不再 `except Exception` 吞错，失败时强制内存中 managed mode 为 off，保留原始设置/凭据字节。
- `tests/conftest.py`：新增全套件 autouse 隔离。session 守卫对真实 `%APPDATA%\WeChatStyleReplyAssistant` 全目录递归快照，记录相对路径、size、SHA-256、`mtime_ns`；每个测试再获得独立临时数据根。
- `tests/test_core.py`：经根任务明确批准，仅为 4 个既有“成功发送”夹具注入可控的 ready memory。原因是新生产契约明确要求 memory 存在且向量健康，不能为过时夹具放宽生产守卫。

## 新增/更新测试

新增主要测试名：

- `test_runtime_event_uses_positive_schema_and_rejects_aliases_nested_values_and_field_smuggling`
- `test_queued_managed_workers_recheck_full_mode_memory_and_vector_immediately_before_send` （6 个参数分支）
- `test_corrupt_credentials_in_v1_or_v2_produce_visible_typed_startup_block_without_byte_changes` （2 个参数分支）
- `test_illegal_utf8_settings_produce_visible_typed_startup_block_without_replacement`
- `test_settings_read_oserror_produces_visible_typed_startup_block`
- `test_runtime_logger_first_initialization_is_thread_safe_and_writes_event_once`
- `test_diag_logger_lazy_initialization_is_thread_safe`
- `test_entire_suite_uses_session_data_root_isolation`
- `test_full_data_directory_snapshot_includes_credentials_ledger_logs_salt_and_backups`

同步更新旧契约测试：嵌套 state 不再保留未知运维键；轮转测试使用 16,000 条 schema-safe JSONL 触发 2,000,000-byte 阈值；v1 凭据迁移失败改为断言 typed error 传播。

## 严格 TDD 原始证据

每轮命令、exit code 和 pytest 原始输出均保存在 `artifacts/formal-hardening/2026-08-12/`：

- C1：`task-5-fix1-c1-red.txt` = `1 failed`（`RECORD-ID-SECRET` 从允许字段嵌套值泄漏）；`task-5-fix1-c1-green.txt` = `1 passed`。
- C2：`task-5-fix1-c2-red.txt` = `6 failed`（个人/群聊 × off/vector_invalid/memory_missing 全部误调 sender）；`task-5-fix1-c2-green.txt` = `6 passed`。
- I1：`task-5-fix1-i1-red.txt` = `4 failed`（v1 吞错、v2 凭据错误外泄、UTF-8/OSError 外泄）；`task-5-fix1-i1-green.txt` = `4 passed`。
- I2：`task-5-fix1-i2-red.txt` = `2 failed`（并发首启 8 handlers，DIAG builder 8 次）；`task-5-fix1-i2-green.txt` = `2 passed`。
- I3：`task-5-fix1-i3-red.txt` = `2 failed`（无全套件 session fixture、无全目录 snapshot helper）；`task-5-fix1-i3-green.txt` = `2 passed`。
- 刷新首轮 persistence 全量：`124 passed, 3 failed`，失败均为旧测试契约；更新后 `task-5-fix1-persistence-green.txt` = `127 passed`。
- 合并首轮：`379 passed, 4 failed, 6 subtests passed`，4 个失败均为 `test_core.py` 成功路径缺 ready-memory 前置；夹具定向 `task-5-fix1-core-fixture-green.txt` = `4 passed`。

## 最终验证与制品

- 阶段合并命令：`\.venv\Scripts\python.exe -m pytest tests/test_persistence.py tests/test_core.py -q --junitxml=artifacts/formal-hardening/2026-08-12/phase-01-persistence.xml`
- 结果：`383 passed, 6 subtests passed in 18.54s`，failed=0，输出无 warnings。JUnit 记录 tests=389、failures=0、errors=0。
- JUnit SHA-256：`59cbcd2c999ad2dfbd10bb08b4a0fa70bc4533a345d894e40511acb92bc2d4ed`。
- `phase-01-files.sha256.json`：11 个生产/测试范围文件，复算 mismatch=0；manifest SHA-256 = `5a20f4086583205f0b73e6395abb822a129fd89f741a65ecf0c76671ae75118a`。其中显式列入 `tests/test_core.py` 的批准范围扩展说明。
- `phase-01-formal-appdata-before.json` / `phase-01-formal-appdata-after.json`：各 32 个真实文件，全集、相对路径、size、SHA-256、`mtime_ns` 逐项完全一致；两个 manifest SHA-256 均为 `dce85a645edbf5371d1b65536ef082ca253da7317a15fae5377602bb8beb42e8`。
- compileall：`\.venv\Scripts\python.exe -m compileall -q runtime_logging.py main.py settings.py tests`，exit code 0；原始输出为 `task-5-fix1-compileall.txt`。

## Fix Round 1 改动文件

- 生产：`runtime_logging.py`、`main.py`、`settings.py`
- 测试：`tests/test_persistence.py`、`tests/conftest.py`、`tests/test_core.py`（仅 4 个已批准夹具）
- 证据：`artifacts/formal-hardening/2026-08-12/` 下 RED/GREEN 原始输出、JUnit、哈希清单、正式 AppData before/after 清单；本报告仅追加 Fix Round 1，未覆盖原证据。

## 自检顾虑

- 未执行真实企业微信发送或 Windows Tk 点击 E2E；发送路径以 deferred/recording sender 断言调用数为 0，安全上不应为 E2E 而冒真实误发风险。
- 日志字符串字段现为保守短 token schema；新增运维事件若不符合 schema 会被丢弃而不是冒险落盘，后续新事件需显式扩展正向 schema。
- 新增 `tests/conftest.py` 会在每次 pytest session 刷新正式 before/after 证据，但只读取正式目录；证据写入 workspace artifacts，测试数据写入 pytest 临时根。
- `main.py` 依旧为历史大文件；本轮仅做审查要求的定点修复，没有提前进行知识 schema/state-machine 重构。

---

# Fix Round 2（2026-08-12 定向复审修复）

## 状态与范围

- 已按 `task-5-fix1-rereview.md` 修复剩余 C1 和新增 N1；不自行宣告根级 review PASS，交回根级复审。
- 仅改动 `runtime_logging.py`、`main.py`、`tests/test_persistence.py`，另刷新证据制品并追加本报告。未调用真实 sender，未启动企业微信，未写入正式 AppData。

## 字段级显式枚举/规范映射

- `event` 独立枚举：`managed-send-diagnostic`、`managed-send`、`nested-redaction`、`rotation-check`、`debug-check`、`debug-disabled`、`concurrency-check`。
- `state` 独立枚举：`blocked`、`vector_index_not_ready`、`managed_preconditions_not_met`、`api_failure`、`latest_message_not_partner`、`no_unreplied_message`、`turn_already_handled`、`send_already_pending`、`no_candidate_selected`、`send_queued`、`preflight_checked`、`preflight_rejected`、`send_result`。
- `error_category` 独立枚举：`vector/settings/credentials/memory/io/api/preflight/send`；`org_scope` 独立枚举：`local/municipal/provincial/national`。四个字段不共享通用 token 或 ASCII 正则作为信任判断。
- `record_id` 从不原文写入；使用安装本地 `.runtime-salt` 执行 HMAC-SHA256，default/debug 得到同一固定摘要，仅用于关联。`score` 仅接受有限数值且范围为 0..100。调用方 `timestamp` 始终忽略，formatter 在序列化时强制生成 UTC ISO 时间。
- `sk-secret-token/customer-message/access-token/john.smith/UUID` 等即使形状合法也不会从枚举字段原文通过；debug 仍仅允许四个明确私密字段的 HMAC，credential 键/值不落盘，未知嵌套值丢弃。

## `_diag` 迁移与运维可用性

- `_diag` 接口改为 `_diag(code, *, error_category=None, score=None, org_scope=None, record_id=None)`，不再接受自由文本位置参数。
- 12 个生产调用点全部迁移为上述可信 state 码；API 错误原文、候选回复、context、signature、preflight reason、sender message 不再传入日志。`send_result` 只以 0/100 score 表示失败/成功。
- AST 测试遍历 `main.py` 所有生产 `_diag` 调用，强制每个调用只有一个字面量 code，并将 12 码实际写入 JSONL，断言 state 集合完整、可区分，不会退化为只剩固定 event+timestamp。

## TDD RED/GREEN 原始证据

- C1：`task-5-fix2-c1-red.txt` = 2 failed（default/debug 均原文保留五个合法形状 smuggling）；`task-5-fix2-c1-green.txt` = 2 passed。
- N1：`task-5-fix2-n1-red.txt` = 1 failed（生产 `_diag` 仍使用自由文本与多位置参数）；`task-5-fix2-n1-green.txt` = 1 passed。
- persistence 首轮刷新：127 passed / 3 failed，三项均是旧断言仍期待 record_id/调用方 timestamp 原文；没有放宽 schema，而是将旧断言更新为 HMAC/生成时间契约。`task-5-fix2-persistence-green.txt` = 130 passed。

## 最终验证与制品

- 合并阶段全量：`386 passed, 6 subtests passed in 18.81s`，failed=0，输出无 warnings。
- JUnit：tests=392、failures=0、errors=0、skipped=0；SHA-256 = `c847b82fcad3bb57169abb69f1b09feb4d18056d545441e1e5e636193d761472`。
- compileall：`\.venv\Scripts\python.exe -m compileall -q runtime_logging.py main.py settings.py tests`，exit code 0；原始输出 `task-5-fix2-compileall.txt`。
- `phase-01-files.sha256.json`：完整阶段审计范围仍为 11 文件，复算 mismatch=0；SHA-256 = `78e71247e6f9ddddd9cb2923ca2c14d96c5de380e6164b716db1c92f7c649312`。
- 正式 AppData：before=32、after=32，全集/相对路径/size/SHA-256/`mtime_ns` 完全一致；current=32、mismatch=0。before/after manifest SHA-256 均为 `dce85a645edbf5371d1b65536ef082ca253da7317a15fae5377602bb8beb42e8`。
- Fix2 原始输出与最终审计：`artifacts/formal-hardening/2026-08-12/task-5-fix2-*.txt`。

## 剩余顾虑

- 显式枚举是 fail-closed；新增运维 code/scope/category 必须同步更新字段级枚举和遍历测试，否则会被安全丢弃。
- `record_id` 关联性依赖安装本地 salt 稳定；salt 丢失/重建后旧日志与新日志不能跨 salt 关联，这是避免可逆身份泄漏的安全取舍。
- 未执行真实企业微信发送/Tk 点击 E2E；本轮不应为 E2E 引入真实误发风险。
