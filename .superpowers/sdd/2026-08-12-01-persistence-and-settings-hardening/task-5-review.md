# Phase 1 Task 5 独立严格审查

## Assessment: Needs fixes

结论：**不能通过**。共发现 2 个 Critical、3 个 Important、1 个 Minor。当前实现已满足轮转参数、JSONL 基本格式、惰性导入和制品哈希一致性，但仍可泄露受保护内容，也存在关闭托管或向量失效后仍执行已排队发送的竞态。

审查方式：逐行阅读 `task-5-brief.md`、`task-5-report.md`、完整 1300 行 `task-5-review-package.txt`，核对当前源码与测试，并只在显式临时目录中运行对抗性探针。未启动企业微信，未调用真实 sender，未改动生产代码或测试。

## Critical

### C1. 嵌套 `state` 的否定词表可被常见别名绕过，聊天名、消息、回复和令牌会原文落盘

- 文件：`runtime_logging.py:16-25,68-90,109-118`
- 事实：顶层仅允许审计字段，但允许字段 `state` 可以携带任意嵌套映射。递归过滤只识别包含 `apikey`、`authorization`、`chatname`、`message`、`reply`、`context` 的键；`chat_title`、`text`、`answer`、`access_token`、`prompt`、`content`、`customer`、`partner` 等常见别名均原样保留。允许字段本身也没有类型、长度或格式约束，因此把秘密放进 `record_id`、`state` 或 `event` 同样可绕过。
- 可复现：向 `build_runtime_logger(temp_dir)` 写入：

  ```python
  {"event": "probe", "state": {
      "chat_title": "FULL-CHAT-NAME",
      "text": "FULL-MESSAGE",
      "answer": "FULL-REPLY",
      "access_token": "API-SECRET",
  }}
  ```

  实际 `runtime.log` 中四个值全部原文存在。该结果直接违反“任何日志路径不得包含 API key、Authorization、完整聊天名、message/reply/context（包括嵌套、大小写、别名、列表）”的绑定约束。
- 修复方向：改成**模式化正向允许列表**，不要继续扩充否定词表。为 `event/state/error_category/org_scope` 定义枚举或受限短字符串；`score` 只允许有限数值；标识符只允许预期格式和最大长度。嵌套映射只保留显式批准的运维键，未知键和值默认删除；凭据相关键无论 debug 与否均删除。增加上述别名、任意允许字段夹带秘密、嵌套列表/映射的对抗测试。

### C2. 发送健康检查存在 TOCTOU；关闭托管或向量在排队后失效，线程仍调用 sender

- 文件：`main.py:619-634,1176-1191,1206-1211,1265-1285`
- 事实：个人和群聊都只在线程创建前调用 `_managed_reply_readiness_allows_send()`。线程内、实际 `sender.send()` 前没有再次检查；该 helper 本身也不检查 `managed_mode == "full"`，甚至在 `memory is None` 时返回 `True`。
- 可复现：令 `memory.vector_ready=True`、`managed_mode="full"`，用延迟执行的假线程调用 `_send_group_reply()`；在线程 target 真正运行前把 `managed_mode` 改为 `off`、`vector_ready` 改为 `False`。实际结果：`sender.calls == 1`。个人路径在 `auto_reply_delay_seconds` 睡眠期间存在同一竞态。
- 影响：用户关闭托管、向量索引转为不健康或启动安全状态改变后，已经排队的自动消息仍可能发出；这违反“技术/索引不确定不发送”和“旧 boolean/callback 不得绕过 off”。
- 修复方向：共享 guard 必须要求完整运行条件（至少 `managed_mode == "full"`、memory 存在、`assert_ready_for_managed_reply()` 通过）。群聊和个人 worker 都要在**紧邻 `sender.send()` 之前**再次调用；个人路径应在延迟和 preflight 后复验。增加可控 deferred-thread 测试：排队后切 off、排队后令向量失效、memory 缺失，三种情况个人和群聊均断言 sender 为 0。

## Important

### I1. typed startup 闭锁没有覆盖凭据损坏及部分设置 I/O/编码错误

- 文件：`main.py:149-188`；`settings.py:316-325,464-495`；`secure_store.py:91-106`
- 事实：生产启动只捕获 `JSONDecodeError`、`SettingsValidationError`、`JsonDataError`、`VectorIndexError`，没有捕获 `CredentialStoreError`、`UnicodeDecodeError` 或 `OSError`。此外，v1 明文凭据迁移在 `settings.py:481-495` 用 `except Exception` 吞掉失败，只把内存中的 managed mode 改成 off，不向启动层暴露阻断原因。
- 可复现：临时数据根中写入合法 v2 `settings.json` 和无效 `credentials.dat`，`load_startup_state_for_test()` 实际直接抛 `CredentialStoreError`，没有 UI 闭锁；换成 v1 明文设置和同一损坏凭据文件，函数返回 `blocking_error == ""`，错误完全不可见。
- 修复方向：持久化层把这些失败归一为明确 typed error 并传播；v1 迁移失败不得吞异常。启动层捕获后返回中文可见的 blocking state，保持原始设置/凭据字节不变。补 v1/v2 损坏凭据、非法 UTF-8、读取权限/I/O 失败测试。

### I2. 首次并发写日志可重复安装 handler，违反“不重复 handler”并可能导致重复行/轮转冲突

- 文件：`runtime_logging.py:136-158`；`main.py:63-74`
- 事实：`owned_handlers` 的检查与 `logger.addHandler()` 不是同一临界区，`DIAG_LOG is None` 的惰性初始化也无锁。两个线程首次同时 `_diag()` 时都可观察到“没有 handler”并各自添加一个。
- 可复现：用 barrier 包装 `RotatingFileHandler.__init__`，两个线程同时调用 `build_runtime_logger(same_dir)`，最终同一 logger 上出现 **2 个** owned handlers。现有测试只串行调用两次，无法覆盖该竞态。
- 修复方向：用模块级锁保护同路径 logger 的检查、创建和添加（以及必要时 DIAG_LOG 初始化）；补确定性的并发回归测试，并断言 `propagate is False`、仅一个 owned handler、单个事件只写一行。

### I3. 测试隔离不是套件内生保证，正式 AppData 守卫还漏掉凭据和发送账本

- 文件：`tests/test_persistence.py:21-48`；示例真实写路径：`tests/test_core.py:3735-3741` 与 `memory.py:336-359`
- 事实：隔离 fixture 只定义在 `test_persistence.py`，不会应用于同一命令中的 `test_core.py`。`test_core.py` 存在 `StyleMemory()` 默认路径后调用 `learn_from_training_examples()` 的测试，而该方法会 `save()`；如果直接执行报告中展示的 pytest 命令、外层没有预先设置覆盖变量，就可能写正式 AppData。报告所称临时根依赖未写入命令的外层环境。
- 事实：正式文件快照只选择路径名含 `memory/settings/vector/log` 的文件，遗漏 `credentials.dat`、`send_ledger.json` 等正式数据；因此“逐文件不变”断言无法发现这些文件的新增、删除或 mtime/hash 变化。
- 修复方向：把数据根隔离移到 `tests/conftest.py` 的全套件 autouse fixture，或让所有写测试显式传临时路径；直接运行计划中的 pytest 命令也必须安全。正式根守卫应快照整个正式数据目录（至少包括 settings、memory、vector、credentials、ledger、logs/salt 及备份），记录逐文件相对路径、SHA-256 和 `mtime_ns`，并把 before/after 明细保存为制品。

## Minor

### M1. RED/GREEN 与正式 AppData 外层守卫缺少可独立复核的原始制品

- 文件：`task-5-report.md:72-116,147-163`；现有制品仅有最终 JUnit 与生产文件哈希。
- 事实：最终 GREEN 可由 JUnit 哈希确认，但每轮 RED、修复后的选择性 GREEN、正式 AppData 外层 before/after 明细只有报告文字，没有命令输出、逐文件 manifest 或时间戳制品。因此“严格 TDD RED/GREEN”不能由本审查独立确认。
- 修复方向：保存每个 cycle 的最小命令、退出码和关键失败/通过摘要；保存正式根 before/after 的逐文件 manifest。不要只在报告中转述聚合哈希。

## 已核实通过的部分

- `RotatingFileHandler` 参数精确为 `maxBytes=2_000_000`、`backupCount=5`、UTF-8；当前源码设置 `logger.propagate=False`。
- 当前 `runtime_logging.py`、`main.py` 等 8 个生产文件 SHA-256 与 `phase-01-files.sha256.json` 一致；JUnit SHA-256 与报告一致，XML 表示 374 cases、0 failure/error。
- `import main` 的实现路径没有立即创建 runtime log；logger 初始化保持惰性。
- 当前 JSONL formatter 会实际序列化 `event_data`，且正常的 `record_id/score/org_scope/state/error_category/event/timestamp` 可保留。
- 现有 JSON/记忆/向量损坏测试以及个人/群聊“调用前已不健康”测试是有效的，但没有覆盖上述凭据错误、别名绕过、并发和排队后状态变化。

## 复审门槛

修复全部 Critical/Important 后，至少需要新增并通过：模式化日志字段测试、凭据/I/O typed startup 测试、个人与群聊 deferred-send 复验、并发单 handler 测试、全套件内部隔离与完整正式 AppData before/after manifest。修复前 Task 5 不应标记完成。
