# Phase 1 Task 5 Fix Round 1 定向复审

## Assessment: Needs fixes

Fix Round 1 已关闭原 C2、I1、I2、I3，并补齐原 M1 的证据缺口；但原 C1 仅部分修复，仍可通过“格式合法”的允许字段原文写入聊天名、消息和 credential。另发现 1 个新的 Important：新 schema 会把当前所有真实 `_diag` 状态理由静默丢弃。

结论计数：1 个 Critical（原 C1 仍未关闭）+ 1 个新增 Important。生产/测试 11 文件与 manifest 一致，JUnit 与正式 AppData 证据一致，但不能抵消上述运行时契约缺陷。

## 原 finding 逐项判定

### C1 — NOT ADDRESSED

**范围：** `runtime_logging.py:33-37,81-100,102-130`；`tests/test_persistence.py:176-213`

修复已做到：未知顶层键、嵌套 mapping/list、`chat_title/text/answer/access_token/prompt/content/customer/partner` 等别名默认丢弃；debug 下明确的 `api_key/authorization` 未落盘。

但所谓“正向 schema”对 `event/state/error_category/org_scope` 仍只是通用正则 `[a-z][a-z0-9_.:-]{0,63}`，不是字段级枚举；`record_id` 也允许任意 UUID。因此秘密只要外形符合该正则或 UUID，就会原文落盘。亲自执行无文件写入探针，default 与 debug 得到相同结果：

```text
DEFAULT={"error_category":"access-token","event":"sk-secret-token","org_scope":"john.smith","record_id":"123e4567-e89b-12d3-a456-426614174000","state":"customer-message"}
DEBUG={"error_category":"access-token","event":"sk-secret-token","org_scope":"john.smith","record_id":"123e4567-e89b-12d3-a456-426614174000","state":"customer-message"}
```

其中：

- `sk-secret-token` 可是 credential；
- `john.smith` 可是完整聊天名；
- `customer-message` 可是消息/回复内容；
- `access-token` 明确是凭据类内容；
- UUID 也可能是访问令牌或其他秘密标识。

现有回归测试没有覆盖这一边界：它给 `event` 使用带空格的 `Bearer EVENT-SECRET`、给 `state` 使用 list、给 `org_scope/record_id` 使用 mapping、给 `error_category` 使用带空格字符串，这些值天然不匹配正则；测试不能证明“合法形状的秘密”不会穿透。

**必须修复：**

- `event/state/error_category/org_scope` 使用每字段明确枚举，不得共享任意 token 正则；
- `record_id` 若必须保留，应只由可信内部生成并优先写 installation-local HMAC，不应原文接受调用方 UUID；
- `score` 应限制业务范围，timestamp 应由 formatter 生成而不是信任调用方；
- 增加上述五个合法形状 smuggling 测试，并在 debug=True 下重复验证 credential 永不原文出现。

### C2 — ADDRESSED

**范围：** `main.py:629-646,1188-1209,1283-1304`；`tests/test_persistence.py:687-757`

- guard 现在强制 `managed_mode == "full"`；memory 缺失返回 False；向量异常强制 managed off。
- 群聊 worker 在 `sender.send()` 前一行复验；个人 worker 在 delay 和 preflight 后、`sender.send()` 前一行复验。
- deferred-thread 测试覆盖个人/群聊 × 排队后 off/vector_invalid/memory_missing 共 6 个分支，均断言 sender=0。

未发现旧 boolean、memory 缺失或排队后向量失效可绕过当前二次 guard 的路径。

### I1 — ADDRESSED

**范围：** `settings.py:41-42,320-347,477-518`；`main.py:152-198`；`tests/test_persistence.py:425-494,1802-1823`

- v1/v2 `CredentialStoreError` 经 `_load_credentials()` 归一为 `SettingsPersistenceError`，启动层返回中文可见 managed-off blocking state。
- 非法 UTF-8 和设置读取 `OSError` 被归一为 typed blocking error；JSON 语法错误保持原有 `JsonDataError` 边界。
- v1 迁移不再 `except Exception` 后静默返回；失败会传播 typed error，并强制内存 managed off。
- 临时目录测试核对原 settings/credentials 字节不变。

`_load_credentials/_save_credentials` 仍捕获宽泛 `Exception`，但会保留 cause、重新抛出 typed error并 fail closed，不属于“不当吞错”；本轮不列 finding。

### I2 — ADDRESSED

**范围：** `runtime_logging.py:39,147-171`；`main.py:63-78`；`tests/test_persistence.py:261-323`

- 模块级 `RLock` 覆盖同路径 logger 的目录创建、handler 检查、创建、formatter/salt 安装和 `addHandler()`。
- `DIAG_LOG` 使用独立 lock 和 double-check，首次并发只构建一次。
- 8 线程测试验证仅 1 个 owned handler、`propagate=False`、单事件只写 1 行；DIAG builder 仅调用 1 次。

### I3 — ADDRESSED

**范围：** `tests/conftest.py:13-64`；`tests/test_persistence.py:23-49`；正式证据 manifest。

- session autouse fixture 在整套测试执行期间设置隔离根；每个测试再使用独立临时根。计划中的原 pytest 命令本身不再依赖外层环境才能安全。
- 正式目录 snapshot 递归覆盖全部文件，字段含 relative path、size、SHA-256、`mtime_ns`；不存在按文件名 token 过滤。
- synthetic coverage 包含 credentials、credential backup、ledger、logs、salt、settings、memory、vector。
- fixture 只读取正式文件并将元数据写到 workspace artifacts，没有把正式内容复制到隔离根。
- 独立复算：before=32、after=32、当前 live=32；before/after 完全一致，当前 live 逐文件 size/hash/mtime mismatch=0。

### M1 — ADDRESSED

Fix Round 1 已保存 5 组 RED 和 8 组 GREEN/全量/编译原始输出；正式 before/after 是逐文件 manifest，不再只有报告中的聚合文字。RED 内容与对应旧失败路径一致，最终 GREEN/JUnit 可复核。

## 新增 Important

### N1. 当前所有真实诊断状态均被 schema 静默丢弃，runtime log 失去发送决策原因

**范围：** `main.py:67-78,1206,1226-1305`；`runtime_logging.py:33,81-100`

`_diag()` 把第一个参数作为 `state`，但当前实际调用全部使用 `[send-block] ...`、`[send-go] ...`、中文、空格或方括号。这些值均不匹配 `_SAFE_TOKEN`，所以 `_allowed_audit_value()` 返回 None。亲自验证：

```python
redact_event({"event": "managed-send-diagnostic", "state": "[send-block] vector_index_not_ready"})
# => {"event": "managed-send-diagnostic"}

redact_event({"event": "managed-send-diagnostic", "state": "[send-block] 无未回复消息"})
# => {"event": "managed-send-diagnostic"}
```

formatter 最终只补 timestamp。也就是说所有真实诊断行几乎都只剩同一个固定 event 和时间，无法区分“向量未就绪、无未回复、preflight 拒绝、发送结果”等原因。这违反任务要求“允许的运维字段实际保留并序列化”，也让正式运维日志失去主要用途。

**修复方向：** 不要把自由文本先写入再依赖正则过滤。让 `_diag` 接收可信的枚举事件码/状态码，例如 `vector_index_not_ready`、`no_unreplied_message`、`preflight_rejected`、`send_result`，并为每个码建立显式允许集合；动态文本、回复、上下文和异常消息不得作为 state。新增遍历所有生产 `_diag` 路径的测试，断言每种状态码可区分且无私密内容。

## 制品与当前状态核对

- 11 文件 `phase-01-files.sha256.json`：当前复算 mismatch=0；manifest SHA-256 为 `5a20f4086583205f0b73e6395abb822a129fd89f741a65ecf0c76671ae75118a`。
- JUnit SHA-256 为 `59cbcd2c999ad2dfbd10bb08b4a0fa70bc4533a345d894e40511acb92bc2d4ed`；XML 为 tests=389、failures=0、errors=0、skipped=0。
- `phase-01-formal-appdata-before.json` 与 after 的 SHA-256 均为 `dce85a645edbf5371d1b65536ef082ca253da7317a15fae5377602bb8beb42e8`；当前正式目录再次只读核对 32 文件 mismatch=0。
- compileall 制品 exit code 0；最终 pytest 输出为 383 passed + 6 subtests。

未重跑会覆盖正式 before/after 制品的 pytest 命令；本复审使用现有原始制品、当前源码复算以及不落盘的对抗探针完成核对。未启动企业微信，未调用真实 sender，未修改生产代码或测试。

## 复审门槛

修复 C1 与 N1 后，需要定向复审：

1. 所有允许字段的合法形状 credential/chat/message smuggling 在 default/debug 下均不能原文出现；
2. 生产 `_diag` 的每个可信状态码可在 JSONL 中区分；
3. 动态错误、聊天名、消息、回复、上下文仍不能进入日志；
4. 更新 tests、11 文件 manifest、JUnit、RED/GREEN 与正式 AppData before/after/current 证据。
