# Phase 1 Task 5 Fix Round 2 最终定向复审

## Assessment: PASS

原 C1 与 N1 均已关闭；Fix Round 1 已关闭的 C2、I1、I2、I3 未被本轮改动破坏。未发现新的 Critical 或 Important。当前 11 文件源码与 checkpoint manifest 完全一致，JUnit 与正式 AppData 证据可复算一致。

本复审未启动企业微信、未调用真实 sender、未运行会重写正式证据制品的 pytest 命令，也未写入正式 AppData。独立验证使用当前源码的只读检查、内存 JSONL/对抗探针以及已有原始 RED/GREEN/JUnit 制品；唯一新增文件为本报告。

## Finding 判定

### C1 — ADDRESSED

证据：`runtime_logging.py:33-64,108-121,124-166`；`tests/test_persistence.py:181-258,438-478`。

- `event`、`state`、`error_category`、`org_scope` 分别拥有独立的显式允许集合，不再共享通用 token/正则；值必须是精确 `str` 且属于对应字段的集合。合法形状的 `sk-secret-token`、`customer-message`、`access-token`、`john.smith` 均被丢弃。
- `record_id` 只接受非空、至多 512 字符的精确字符串类型，并使用 formatter 持有的 installation-local salt 做 HMAC-SHA256；不写原文。`score` 只接受精确 `int/float`，要求有限且位于 0..100；bool、字符串、NaN、Infinity、越界值均被丢弃。调用方 `timestamp` 始终被拒绝，formatter 在序列化时生成 UTC ISO 时间。
- default/debug 都使用同一正向 schema。debug 只对四个明确私密别名输出 HMAC；credential 字段、未知顶层字段、未知嵌套 mapping/list 都不序列化。formatter 也完全忽略日志 message/exception 文本，只消费 `event_data`。
- 独立不落盘探针在 default/debug 下分别注入上述五种合法形状 smuggling、`api_key`、`Authorization`、`chat_title/text/answer/prompt/content/customer/partner`、嵌套 mapping 与无键 list，两个结果均只有 HMAC 后的 `record_id`，原文泄露为 0。有效 schema 探针保留 event/state/category/scope/score，并验证 `record_id` 等于预期 HMAC；调用方时间戳未出现。
- 分值边界独立探针结果：0、100 保留；-1、101、NaN、Infinity、bool、字符串全部丢弃。

Fix2 TDD 证据可信：`artifacts/formal-hardening/2026-08-12/task-5-fix2-c1-red.txt` 记录修复前 default/debug 两个分支均失败且直接显示五项原文穿透；`task-5-fix2-c1-green.txt` 为同一测试 2 passed。当前实现与当前测试哈希均匹配最终 11 文件 manifest。

### N1 — ADDRESSED

证据：`main.py:67-95,1221,1241,1247,1253,1257,1267,1276,1288,1295,1303,1314,1320`；`tests/test_persistence.py:372-413`。

- AST 独立复算得到当前 `main.py` 恰有 12 个生产 `_diag` 调用；每个调用只有一个字符串字面量位置参数。12 个码分别是 `vector_index_not_ready`、`managed_preconditions_not_met`、`api_failure`、`latest_message_not_partner`、`no_unreplied_message`、`turn_already_handled`、`send_already_pending`、`no_candidate_selected`、`send_queued`、`preflight_checked`、`preflight_rejected`、`send_result`。
- 动态异常、聊天名、消息、回复、context、signature、preflight reason、sender message 均不再传入 `_diag`；附加字段仅为枚举 category 和 `send_result` 的 0/100 score。
- 使用当前 `_diag`、内存 `StreamHandler` 与当前 JSON formatter 的不落盘验证实际产生 12 行可解析 JSONL；state 数量为 12、去重后仍为 12，event 全为 `managed-send-diagnostic`，每行均有 formatter 生成的 timestamp。
- 回归测试遍历全部生产调用，强制单一字面量 code、精确 12 码集合，并把 12 码实际写入隔离 JSONL 后断言 state 集合完整，能阻止未来恢复自由文本位置参数。

Fix2 TDD 证据可信：`task-5-fix2-n1-red.txt` 记录修复前 AST 约束失败（生产调用仍有自由文本/多个位置参数）；`task-5-fix2-n1-green.txt` 为同一测试 1 passed。

## Fix Round 1 回归核对

### C2 — 仍为 ADDRESSED

`main.py:1203-1224` 的共享 guard 强制 startup 无 blocking error、`managed_mode == "full"`、memory 存在且 `assert_ready_for_managed_reply()` 通过。群聊 worker 在 `main.py:646-653` 紧邻 sender 前二次验证；个人 delayed worker 在 delay/preflight 后，于 `main.py:1317-1320` 紧邻 sender 前二次验证。`tests/test_persistence.py:777-847` 覆盖个人/群聊乘排队后 off、vector invalid、memory missing 六个分支，均要求 sender=0。Fix2 未改变这些 guard 契约。

### I1 — 仍为 ADDRESSED

`settings.py:320-347,490-518` 继续把非法 UTF-8、读取 OSError 和凭据存储失败归一为 typed `SettingsPersistenceError`，v1/v2 均不再吞掉迁移失败；`main.py:149-198` 将 typed persistence/validation/data error 转换为 UI 可见、managed-off 的 blocking startup state。`tests/test_persistence.py:488-582` 仍断言原 settings/credential 字节不变。Fix2 未改动 `settings.py`，其当前哈希与最终 manifest 一致。

### I2 — 仍为 ADDRESSED

`runtime_logging.py:169-193` 的模块级 RLock 覆盖目录创建、owned-handler 检查、handler/salt/formatter 创建与 `addHandler()`；`main.py:63-95` 的 DIAG lock 保持 double-check。`tests/test_persistence.py:296-369` 继续覆盖同路径 handler 不重复、`propagate=False`、并发首次初始化单行以及 DIAG builder 单次构建。Fix2 未移除任何锁或扩大临界区外的 check/create/add 路径。

### I3 — 仍为 ADDRESSED

`tests/conftest.py:13-64` 仍提供 session 级正式目录 before/after 全量递归快照和整套测试数据根隔离，每个测试另有独立临时根；快照字段为 relative path、size、SHA-256、mtime_ns，且不按名称过滤。`tests/test_persistence.py:25-51` 的 synthetic coverage 明确包含 credential、backup、ledger、log、salt、settings、memory、vector，证明这些类别存在时都会进入快照；fixture 不复制正式文件内容到隔离数据根。Fix2 未改动 `tests/conftest.py`。

## 制品与当前状态复算

- `phase-01-files.sha256.json`：11 个文件逐一复算，mismatch=0；manifest SHA-256 为 `78e71247e6f9ddddd9cb2923ca2c14d96c5de380e6164b716db1c92f7c649312`。
- `phase-01-persistence.xml`：tests=392、failures=0、errors=0、skipped=0；SHA-256 为 `c847b82fcad3bb57169abb69f1b09feb4d18056d545441e1e5e636193d761472`。对应原始阶段输出为 386 passed + 6 subtests passed。
- Fix2 persistence 原始证据：首次为 127 passed / 3 failed，三项均是旧断言仍期待 record_id/调用方 timestamp 原文；更新断言后 `task-5-fix2-persistence-green.txt` 为 130 passed。未通过放宽 schema 消除失败。
- 正式 AppData：before=32、after=32、current=32；before/after JSON 完全相同且 SHA-256 均为 `dce85a645edbf5371d1b65536ef082ca253da7317a15fae5377602bb8beb42e8`。本次重新只读枚举 current 后，逐文件 path/size/hash/mtime mismatch=0。
- 当前 11 个 manifest 文件均通过内存 `compile()` 语法验证；未产生 pyc 或其他项目文件。

## Critical

none。

## Important

none。

## Minor 台账

### M1. Windows 同一目录的大小写别名可绕过 owned-handler 的字符串等值判断

`runtime_logging.py:170-180` 用 casefold 后的路径生成 logger 名，因此大小写不同的同一 Windows 路径会落到同一个 logger；但 `_wechat_runtime_path` 使用未 casefold 的 `str(log_path)` 做精确比较。若未来调用方以不同大小写路径重复调用公开 builder，同一 logger 可能添加第二个 handler并产生重复行。当前生产 `_setup_diag_log()` 始终来自唯一 `logs_dir()` 表示，不存在该调用方式，故不阻断本轮放行。后续可将 marker 与比较值统一使用 `os.path.normcase()`/casefold 后的 resolved path，并增加大小写别名回归测试。

### M2. 已存在但长度不足的 salt 会令诊断日志静默不可用

`runtime_logging.py:79-100` 在 `.runtime-salt` 已存在但长度不足 16 bytes 时抛出异常；`main.py:75-95` 会吞掉该异常以保护主流程，结果是诊断事件静默丢失。该路径不会泄露私密内容，且正常原子竞争已由 `xb` 与模块锁约束，因此为运维可用性 Minor。后续可采用原子写 salt，并在损坏时 fail-closed 地轮换到新 salt且给 UI/本地非私密状态提示。

## 最终结论

C1：ADDRESSED。N1：ADDRESSED。C2/I1/I2/I3：保持 ADDRESSED。无 Critical/Important，Task 5 Fix Round 2 可通过根级复审。
