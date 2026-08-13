# Phase 1 final production-safety review

审查日期：2026-08-12  
审查方式：严格只读代码审查；未启动企业微信、未调用真实发送方、未进行网络/E2E；仅用纯内存替身复现关键分支。  
结论计数：**Critical 2 / Important 3 / Minor 7**。

## Strengths

- `app_paths.py:11-42` 将 source/frozen 共用的数据根、测试 override、设置、记忆、向量、凭据、账本和日志路径集中到一个无 import-time 写入的解析层。当前设置、记忆、向量、凭据和运行日志的生产消费者均使用该层。
- `atomic_json.py:33-55` 对主文件和五个备份逐个执行 JSON 与业务 schema 校验；全部失效时抛出 `JsonDataError`，不会静默生成空数据。`settings.py:320-340`、`secure_store.py:91-106` 同样把“文件不存在”与“已有文件但均损坏”区分开。
- `settings.py:490-518` 的 v1 明文凭据迁移顺序为：读取与校验旧配置 -> DPAPI 写入 -> 回读比对 -> 最后写去密设置；任一凭据读取/回读错误均升级为 typed error。正常路径没有先删除明文再尝试加密的问题。
- `settings.py:247-318` 对版本、布尔、数值、托管模式、allowlist 与废弃字段实施严格 schema；`settings.py:211-214,366-367` 强制 `knowledge_only` 且关闭联网搜索。
- `memory.py:178-213,701-763` 严格校验向量 schema、维度、`memory_hash`、桶索引和有限正数权重，并对主文件恢复、陈旧或语义不一致索引保持 fail-closed；单字符文本也在 `memory.py:1125-1139` 得到非空向量。
- `main.py:149-218` 将配置、凭据、记忆、向量错误转为 typed blocking startup state；`main.py:1203-1224` 在发送前再次检查启动阻断和向量健康。个人发送在 `main.py:1298-1338` 还会重新定位窗口、重新 OCR 并做 preflight。
- `runtime_logging.py:108-166` 使用正向字段 schema 和 HMAC 隐私哈希；`runtime_logging.py:169-193` 的初始化锁和 2 MB × 5 轮转为并发初始化与容量控制提供了良好基础。
- 测试隔离夹具 `tests/conftest.py:35-64` 在测试会话开始前设置独立数据根，并比较正式 AppData 前后快照。本次独立复算确认已有 before/after/current 三者完全相同。

## Critical

### C1. `knowledge_only` 可达本地自由话术并进入真实托管发送链

**位置：** `settings.py:167,211-214,366-367`；`reply_engine.py:98-161,314-329,658-678`；`main.py:846-869,1239-1338,637-653`。

**复现条件：** 正式设置必为 `reply_source_mode="knowledge_only"`，知识库对当前消息无可靠命中，托管模式为 `full` 且其余发送 guard 通过。纯内存探针得到：

```text
"嗯嗯"           -> ["嗯嗯", "好的", "可以，先这样"]，source=本地兜底规则
"谢谢"           -> ["不客气，拿到就行", "收到就好", "哈哈不客气"]，source=本地兜底规则
"工作量太大了"   -> ["可以，我看下具体情况", "这个我确认下再回你", "我先看下，晚点回你"]，source=本地兜底规则
```

原因是 `reply_engine.py:135` 只把旧值 `memory/model_memory` 视为话术库模式；`knowledge_only` 最终落到 `reply_engine.py:161` 的 `_fallback()`，再由 `reply_engine.py:322-329,658-678` 返回手写规则或通用回复。个人路径在 `main.py:848-869` 把该结果交给 `_maybe_managed_send()`，通过 guard 后于 `main.py:1319` 调用 sender；群聊路径在 `main.py:637-653` 同样可达 sender。因此这不是只影响候选展示的兼容性问题，而是实际自动发送路径。另有 `reply_engine.py:39` 的固定文案为“暂时未查到对应答案。”，也不等于设计要求的精确“暂未查到对应答案”。

**影响：** 正式 `knowledge_only` 承诺被破坏；无知识依据的程序自拟内容可以自动发给个人或群聊客户，属于错误对外发送。

**修复方向：** 在 `ReplyEngine.generate()` 增加显式且唯一的 `knowledge_only` 分支：只允许受批准的本地知识记录；无可靠命中必须返回精确固定未命中文案；不得调用 `_fallback()`、`_rule_replies()`、自由生成或搜索。新增个人、群聊两条 fake-sender 集成测试，断言无知识命中时 sender 只能看到固定文案，并覆盖低信息、致谢、陈述、问句。

### C2. 一致性 JSON 中的字符串 `"false"` 被转为真并发送候选答案

**位置：** `reply_engine.py:408-470`；发送可达性同 `main.py:846-869,1239-1338,637-653`。

**复现条件：** 企业知识库产生候选，模型返回合法 JSON `{"chosen": 1, "consistent": "false"}`。纯内存替身（无网络）实测：

```text
_judge_enterprise_consistency -> {'chosen_index': 0, 'consistent': True}
_enterprise_answer_via_judgement -> ['SENSITIVE_KB_ANSWER']
```

`reply_engine.py:464-465` 使用 `int(...)` 和 `bool(...)` 宽松转换；Python 中任何非空字符串（包括 `"false"`）均为真。`reply_engine.py:414-418` 随后直接返回原候选答案，主程序会把它送入真实托管发送链。

**影响：** 模型明确表达“不一致”仍可能发送不对应的企业年报答案；一致性判断不再 fail-closed，属于错误对外发送。

**修复方向：** 要求根对象与键集合精确匹配；`type(chosen) is int`（拒绝 bool、字符串、float），`type(consistent) is bool`，并严格做候选范围检查。任何额外键、类型偏差、解析异常或 `consistent is False` 均只返回固定未命中文案。加入 `"false"`、`"true"`、0/1、`True` 充当 chosen、额外键和缺键的回归测试，并贯通 fake sender。

## Important

### I1. JSON Unicode 转义可绕过凭据模板检查，明文秘密会落入设置及五个备份

**位置：** `settings.py:33-34,79-104,203-207,247-256,461-485`；实际请求消费位于 `llm_clients.py:124-144`。

**复现条件：** 将 `custom_headers` 设为 `{"X-\\u0041uth":"literal-secret"}`。纯内存实测 `AppSettings.validate()` 接受该值，而 `json.loads()` 后的实际键是 `X-Auth`。文本正则看到的是未解码键，真实请求构造则在 `llm_clients.py:140-144` 解码 JSON 并使用该 header。

**影响：** schema 会错误地证明模板“不含明文凭据”；秘密随后由 `settings.py:477-485` 以明文进入 `settings.json` 及五份备份，并可作为 HTTP header 发出。备份扩大了残留面。

**修复方向：** 用无秘密哨兵渲染模板后进行真实 JSON 解析，对解码后的所有嵌套键做规范化与敏感词判定，并只允许敏感位置使用精确 `{api_key}` 形式；不要用原始文本正则代替 JSON 语义校验。覆盖 Unicode 转义、大小写、camelCase、分隔符、嵌套对象/数组和 settings 五备份的明文扫描测试。

### I2. JSON/DPAPI 五槽轮转在写失败时会部分提交并丢失既有有效版本

**位置：** `atomic_json.py:67-77,80-95,98-119`；`secure_store.py:42-57,108-139`；现有不足测试 `tests/test_persistence.py:1477-1495`。

**复现条件：** 已存在主文件和多个彼此不同的有效备份，在第 N 个备份的 fsync/replace 或最终主文件 replace 处注入 `OSError`。纯内存函数替身在第 3 个备份失败时记录到以下已发生调用：

```text
bak.1 <- primary
bak.2 <- old bak.1
bak.3 <- old bak.2   # 此步注入失败
```

由于 `atomic_json.py:92-95` 逐槽覆盖/删除，而最终主文件到 `atomic_json.py:116` 才 replace，异常前的槽不会回滚。例如第 3 槽 replace 前失败时，旧 `bak.2` 已因 `bak.2 <- old bak.1` 而丢失；最终主 replace 失败时，完整轮转已经丢弃旧 `bak.5`。`secure_store.py:130-139` 采用同构顺序。现有 replace 测试只断言 primary 仍可读，未断言所有旧备份保持不变。validator 对新数据失败发生在变更前，是安全的；问题集中在轮转/replace/fsync 的中途失败语义。

**影响：** 调用方收到“保存失败”，却已经永久失去一份或多份此前有效的恢复版本；在混合损坏与连续故障下会降低甚至耗尽恢复能力。凭据备份同样受影响。

**定级依据：** 当前 primary 通常仍可读，故未定为 Critical；但它违反“失败不丢任何既有有效数据”的明确持久化契约，且影响设置、记忆、向量和凭据四类正式数据，定为 Important。

**修复方向：** 采用 generation/manifest 两阶段方案：把完整新备份集写入唯一临时 generation，逐文件 flush+fsync，最后一次原子切换 manifest/目录指针；任何异常都让旧 generation 完整可恢复。主文件与备份提交顺序必须定义清楚，并补目录元数据持久化。对每个备份槽 fsync、备份 replace、删除和主 replace 逐点 fault injection，断言失败后旧主文件及所有旧有效候选逐字节不变。DPAPI store 使用同一经验证的事务组件。

### I3. `ledger_path` 无生产消费者；重启/多实例后发送幂等性失效

**位置：** `app_paths.py:37-38`；`main.py:243-252,310-322,625-629,1260-1278,1321-1329`。

**复现条件：** `full` 模式成功回复后，在自己的回显尚未稳定被 OCR 捕获时重启；或同时运行两个进程面对同一条客户消息。代码库生产范围内 `ledger_path/send_ledger` 的唯一命中是路径定义，当前正式 AppData 32 文件也没有 `send_ledger.json`。去重签名、inflight、最后已处理 turn 和最后发送时间均只在 `main.py:243-252` 初始化为进程内空值，成功后也只在 `main.py:1321-1329` 更新内存；`main.py:310-322` 启动即监控，没有启动基线或单实例互斥。群聊还在生成/发送前即于 `main.py:625-629` 标记消息已处理，失败后本进程内会吞掉重试。

**影响：** 重启或多实例可以对同一可见客户消息再次发送；反向地，群聊发送失败又可能被内存提前标记而不重试。当前 preflight 只能解决单进程、单次队列期间的 TOCTOU，不能提供跨进程/跨重启 exactly-once/at-most-once 语义。

**修复方向：** 在允许 `full` 前实现持久 `pending/confirmed/failed` ledger、稳定的 chat+turn 幂等键、启动水位线和 Windows 单实例锁；个人与群聊都应在紧邻 sender 前原子登记 pending，成功后确认，失败按明确策略记录/重试。读取或写入 ledger 失败必须阻断托管发送，不能退化为内存集合。虽然 Phase 3 计划描述了该组件，当前代码尚未实现，计划本身不能降低当前生产风险。

## Minor

### M1. 四托管模式只在设置/UI 存在，`controlled` allowlist 没有运行时语义

`settings.py:17,171-172` 声明四模式，`float_ui.py:574-593,1152-1193,1467-1490` 可保存模式和 allowlist；但 `main.py:284-305` 仍只注册旧 bool 回调，`float_ui.py:584-588` 会把非 `full` 传成 `False`，而 `main.py:1208-1211` 又把任何非 `full` 模式改为 `off`。生产范围内 `managed_allowed_chats` 没有发送守卫消费者。结果是 `observe/controlled` 不会越权发送，因而仍属 fail-closed；但“受控名单试运行”功能实际不可用。应接入字符串模式回调，并在单一发送策略中实现 off/observe/controlled/full，controlled 对规范化 chat identity 做 allowlist 检查且排队后重查。

### M2. `backup_count` 写 API 与固定五槽读 API 不一致

`atomic_json.py:41-45` 永远只读 `.bak.1` 至 `.bak.5`，而 `atomic_json.py:98-103` 公开接受任意 `backup_count`。生产调用当前均传 5，因此没有现时数据丢失；但调用 6 会写出永不恢复的 `.bak.6`，调用较小值又改变保留语义。应删除该参数或让 load/write 共用同一显式策略对象，并严格验证为 5。

### M3. `managed_mode` 的 unhashable 非字符串值会泄漏 `TypeError`

`settings.py:215` 在先做类型判断前执行集合成员测试；传入 list/dict 时不是 `SettingsValidationError` 而是 `TypeError`。持久文件的 raw validator 已在 `settings.py:298-301` 正确防住，风险主要在直接构造/未来 UI 调用。应先 `type(value) is str` 再查集合。

### M4. 回复来源 tooltip 仍描述已禁止的自由生成和模型兜底

`float_ui.py:65-68` 仍宣称“话术库无数据时自动用大模型兜底”和“检索后交给大模型生成”，与 `knowledge_only` 正式契约冲突；`float_ui.py:43-44` 也仍写“生成3条候选”。它不会直接开启发送，但会误导操作和验收。应改为“仅本地知识、模型最多排序/一致性判断、未命中固定答复”。

### M5. 3001 条边界的去重键可能吞掉业务元数据

`memory.py:1154-1169` 仅以 `conversation_hash + compact(reply)` 去重，冲突时除 `source_file` 外保留第一条；若两个记录回复相同但 `organization_type`、来源定位或其他业务元数据不同，后者会消失。当前 3001 测试主要覆盖数量和唯一值，未覆盖冲突元数据。应定义可合并字段并确定性合并，或把稳定来源/业务 scope 纳入 identity，并增加恰好 3001 条、跨去重边界的元数据测试。

### M6. Windows 同一路径大小写变体可能重复安装日志 handler

`runtime_logging.py:170-172` 用 casefold 路径计算同一个 logger key，但 `runtime_logging.py:176-188` 用大小写敏感字符串比较 handler marker。同一 NTFS 路径以不同大小写传入时可能在同一 logger 上安装第二个 handler，导致重复日志与轮转竞争。应保存并比较同一种规范化/casefold key，而不是原始 `str(log_path)`。

### M7. 已存在的损坏 salt 会让所有诊断静默消失

`runtime_logging.py:79-100` 遇到已存在但短于 16 字节的 `.runtime-salt` 会抛错；`main.py:67-95` 捕获所有异常并无提示地丢弃事件。发送仍会继续，因此未升级为 Important；但正式运行失去全部安全诊断且操作者不知情。salt 写入本身在 `runtime_logging.py:89-95` 也没有显式 flush/fsync。应原子且持久化地创建 salt；检测损坏时进入明确的 logger-unavailable 状态并在 UI/启动诊断中提示（托管是否阻断应由正式策略明确），不要永久 silent-pass。

## Deferred-minor triage

1. **app path 消费者覆盖：部分关闭，核心账本项升级为 Important I3。** 配置、记忆、向量、凭据、日志均已有集中消费者；但 `ledger_path()` 仍只有定义，没有生产消费者，且这已构成跨重启/多实例发送风险，不再只是测试覆盖问题。
2. **atomic_json `backup_count` 不一致：仍为 Minor M2。** 当前生产调用固定五槽，故未升级；与 I2 的“失败时部分提交”是两个独立问题。
3. **unhashable managed_mode、过时 tooltip、报告计数：前两项仍为 Minor M3/M4；报告计数已关闭。** 当前 JUnit 元数据提供单一可复算计数，不再把历史报告措辞差异作为代码 finding。
4. **3001 去重元数据边界：仍为 Minor M5。** 可能丢业务元数据，但当前没有证据表明它已直接造成错误自动发送。
5. **Windows 路径大小写 handler：仍为 Minor M6。** 规范生产路径下不易触发，大小写别名/未来调用才会重复 handler。
6. **salt 损坏静默日志：仍为 Minor M7。** 它损害可观测性但不放宽发送 guard，也不泄露明文，故不升级；应在正式发送前修复或至少显式告警。

## Evidence verification

### 已完整阅读的材料

已按要求顺序完整阅读设计、Phase 1 计划、4578 行 `phase-01-review-package.txt`、`progress.md`、Task 1-5 report，以及全部 task review / fix rereview；随后按需直接核对当前 11 个审计文件，并跨模块核对 `reply_engine.py`、`llm_clients.py` 与实际发送路径。历史 review 的关闭结论均以当前代码重新查证，没有直接沿用。

### ZIP、审查包、manifest 与 JUnit

- 审计前 ZIP：104,151,720 bytes；SHA-256 `522DB2E0A6EA93BD1AF289A75053C3DE9161265C9F05F81DC00FE003EF37D12A`，与声明一致。
- Phase 1 review package：197,922 bytes；SHA-256 `32A4123A485543D5ED26DE2D0E394BE46E03255819A5FC238FEE9B67359ECE1C`，与声明一致。
- `phase-01-files.sha256.json`：1,410 bytes；artifact SHA-256 `78E71247E6F9DDDDD9CB2923CA2C14D96C5DE380E6164B716DB1C92F7C649312`。独立重算 11/11 当前文件，0 mismatch：

| 文件 | bytes | 当前 SHA-256 |
|---|---:|---|
| app_paths.py | 875 | `f8f5ff09720facc8b5384d302ae1f6a9083042a573cea5ecbde2060d1a530440` |
| atomic_json.py | 4,147 | `7aa66d59f228bcfe404c471a3f700aa06b53889068a944b686059c8b70874e1b` |
| secure_store.py | 4,699 | `5dd70dcd1d5c5b33f4622687cc9862102a8cf5bba25c12b8f4d3fcd848d49bba` |
| runtime_logging.py | 6,160 | `d3c2f6cb9c459b9dd672924cd36727ffd85c786cb2911c7aa45ce80e7c1b5cbb` |
| settings.py | 23,144 | `0c83c8efb7f5a2a4f5cdb68f09b96a95cb437bdf88834b0ef33d2258a378e541` |
| memory.py | 48,910 | `3eb04ad35be57b66a961013889cc20586e480f7dbc3fe99848b712dd7b7a4c6b` |
| float_ui.py | 83,902 | `fdbc5362b06f2d4ecdda11d6790fd9b2109ca947c9740dc438538b3f2a68e5bb` |
| main.py | 94,620 | `0ffddeb09a056d982b17c931a257ec5315c5487cd301273e6fd49717457184ea` |
| tests/conftest.py | 2,469 | `5c1c67648a5c77c3d6a7dbf34dd3770a436c6f3ca6f4e4720aaf1864ebf3d2a8` |
| tests/test_persistence.py | 71,470 | `e3df1e32be627df6d9346e27b39b2631264c910b6259bee1097f069394a800d0` |
| tests/test_core.py | 233,886 | `1d92460f414c875ef103042daefdbd20798ed82ff12f81d82703c4ebdd92f5f3` |

- `phase-01-persistence.xml`：54,832 bytes；SHA-256 `C847B82FCAD3BB57169ABB69F1B09FEB4D18056D545441E1E5E636193D761472`。JUnit 根 suite 记录 `tests=392, failures=0, errors=0, skipped=0, time=18.738`，时间戳 `2026-08-12T07:35:45.321534+08:00`；历史终端报告将其表述为 386 passed + 6 subtests。
- 本轮没有重跑 pytest：`tests/conftest.py:30-57` 会重写 before/after artifact，而任务唯一允许写入的是本报告。现有 JUnit 只能证明其记录的版本与测试通过；11 文件 manifest 确认当前审计范围与该版本相同，但绿灯未覆盖 C1、C2、I1 和 I2 的上述反例。

### 正式 AppData before / after / current 逐项复算

`phase-01-formal-appdata-before.json` 与 after 均为 6,836 bytes，SHA-256 均为 `DCE85A645EDBF5371D1B65536EF082CA253DA7317A15FAE5377602BB8BEB42E8`。根路径均为 `C:\Users\14913\AppData\Roaming\WeChatStyleReplyAssistant`。独立枚举 current 得到相同 32 个文件；以下每行的 size/SHA-256/mtime_ns 在 before、after、current 三者均完全一致：

| relative path | size | SHA-256 | mtime_ns | 比较 |
|---|---:|---|---:|---|
| credentials.dat | 304 | `1040ba5eaa98347e4fa2d3e54fbf356966fde435fd83064f5ebeebf03048e710` | 1786488373807829200 | MATCH |
| credentials.dat.bak.1 | 304 | `1d76573f442ebd863fe0dc13ff196edae6c52889afa21ecd9808bc1578cb0150` | 1786488373789806300 | MATCH |
| credentials.dat.bak.2 | 304 | `3c22f74793f3d6cd9cc89776a14a7931d532121daff6b4af3a53b8969e0e5651` | 1786488373792817400 | MATCH |
| credentials.dat.bak.3 | 304 | `83016cf785ad814f2c49b6a41e836b073a3191902cfe926fdd70ac3079aa88d0` | 1786488373796804100 | MATCH |
| credentials.dat.bak.4 | 304 | `a8ea4b27f4e41bd8f358a93de4a45f0da08d89288dc44bef9a928dd6c68cab9d` | 1786488373800825000 | MATCH |
| credentials.dat.bak.5 | 304 | `2555a18134fc27b03ea7dfcef25fad2971566712fe5f0961a916a9cea72b3715` | 1786488373803823100 | MATCH |
| managed_send.log | 154,761 | `d9a879a54eaab37dcce9de2c57dcc4d773c29a250ceddf55e90111d2f1c549eb` | 1786487362105105500 | MATCH |
| memory.json | 1,151 | `9dd71cbd01468025011311f5481f115c6b39946e74842faf81ed26fbabe1f2c8` | 1786488373591266200 | MATCH |
| memory.json.bak-20260807-100255 | 1,160 | `a2fe21b4c201ff00a999afcd5bd887618a3a0c9287f483677c917fbc24183b4b` | 1786068037014282700 | MATCH |
| memory.json.bak-20260807-164917 | 1,160 | `68715d16f2de187d81868af437934ccd1414b5509b3ac155ffc5a913cbcaaa37` | 1786071389038420200 | MATCH |
| memory.json.bak-20260807-170259 | 1,160 | `d6e3c9dec97c19b6086ceb23468035fff37d20d66a0ab81cf004a7690d3c48d9` | 1786093300397972000 | MATCH |
| memory.json.bak.1 | 508 | `87c7a7df3c618489257dcd6589614d16cbee90e7767b63c45a4f49c4f0f96ae8` | 1786488373594242600 | MATCH |
| memory.json.bak.2 | 1,032 | `d8e735723fa889cb0e996f7be9731397c823285d45055acf7a247944d86e834b` | 1786488373598261100 | MATCH |
| memory.json.bak.3 | 812 | `8026520eace28b2eeb1af9530d1d63d0d04bac3677f45807bb468ccc9d82a02a` | 1786488373601248300 | MATCH |
| memory.json.bak.4 | 508 | `87c7a7df3c618489257dcd6589614d16cbee90e7767b63c45a4f49c4f0f96ae8` | 1786488373603249500 | MATCH |
| memory.json.bak.5 | 508 | `87c7a7df3c618489257dcd6589614d16cbee90e7767b63c45a4f49c4f0f96ae8` | 1786488373606102400 | MATCH |
| memory.json.before-enterprise-kb-20260803-201001.bak | 500 | `499225dad26fc9a90a11ffdd36d8637f57ad9afb183a9b989e175fa92afe6a27` | 1785758205943535900 | MATCH |
| memory.json.before-reimport-20260803-203824.bak | 500 | `cf6a511f1c649b2468e06602fb6b1dbd030c8326991dc2759698a576314a2c49` | 1785760624479863600 | MATCH |
| settings.json | 991 | `384fdaf5d10a9687427d9bdefbd11d3381e32e491a6200866d9d051556e833b3` | 1786488373812560700 | MATCH |
| settings.json.bak-20260803-2340 | 1,062 | `cfc13a196327ddd09f0e94794b507b12639940854680d8a3ff9c94724bff0658` | 1785771667952484600 | MATCH |
| settings.json.bak.1 | 997 | `0cd9306cf8f252a4f833601f44f94d4e83540db2c5a6f26346384561cfcf9679` | 1786488373816420300 | MATCH |
| settings.json.bak.2 | 991 | `384fdaf5d10a9687427d9bdefbd11d3381e32e491a6200866d9d051556e833b3` | 1786488373819360600 | MATCH |
| settings.json.bak.3 | 997 | `0cd9306cf8f252a4f833601f44f94d4e83540db2c5a6f26346384561cfcf9679` | 1786488373822406200 | MATCH |
| settings.json.bak.4 | 991 | `384fdaf5d10a9687427d9bdefbd11d3381e32e491a6200866d9d051556e833b3` | 1786488373824820100 | MATCH |
| settings.json.bak.5 | 997 | `0cd9306cf8f252a4f833601f44f94d4e83540db2c5a6f26346384561cfcf9679` | 1786488373826820900 | MATCH |
| sop_library.json | 154,541 | `d78499307e3e2e8002d532d9e1c84838dbf45db25185b30851fb668d7b8ee0dc` | 1785943583570913400 | MATCH |
| vector_memory.json | 4,902 | `173cca6f617fa7af85b30e7baceecc7954c4a1067aac19ae9ca5ebd050815548` | 1786488373611127700 | MATCH |
| vector_memory.json.bak.1 | 1,470 | `6164aa5190b6ea8ee6070a1d1f7cc3cf948ad9bd8b7db1e9260f16061c0c6ed7` | 1786488373615116000 | MATCH |
| vector_memory.json.bak.2 | 7,394 | `fa6ee8865339fa1fbba459dbca018e730cc0bb7e24c69d29b6cdfd2b4e3a8356` | 1786488373618130600 | MATCH |
| vector_memory.json.bak.3 | 2,929 | `44f2d5030c6b319d71135ddd64990186e90444a6ab493b41ab7831ec3d9d2878` | 1786488373621129800 | MATCH |
| vector_memory.json.bak.4 | 1,470 | `6164aa5190b6ea8ee6070a1d1f7cc3cf948ad9bd8b7db1e9260f16061c0c6ed7` | 1786488373624130500 | MATCH |
| vector_memory.json.bak.5 | 1,470 | `6164aa5190b6ea8ee6070a1d1f7cc3cf948ad9bd8b7db1e9260f16061c0c6ed7` | 1786488373626127800 | MATCH |

### 本轮只读反例验证

- C1：三类消息均在 `knowledge_only` 下得到 `本地兜底规则`，无真实 sender。
- C2：模型内容由内存替身固定为字符串 `"false"`，观察到被转换为 `True` 并返回敏感候选，无网络。
- I1：只调用内存对象的 `validate()` 与 `json.loads()`，观察到转义键由校验接受、解码为 `X-Auth`。
- I2：替换 `_read_valid_snapshot/_replace_backup` 为内存记录器，第 3 槽注入失败，观察到前两槽提交顺序；未创建、修改任何生产/测试数据文件。

## Assessment: Needs fixes

Phase 1 当前不能作为正式个人使用的生产安全基线。C1 和 C2 均已证明可以生成并沿真实托管链发送错误内容；I1 会留下明文凭据；I2 违反失败不丢旧有效数据的持久化契约；I3 表明跨重启/多实例发送尚无持久幂等边界。必须至少关闭全部 Critical/Important、加入对应反例测试并重新生成隔离的全套件/JUnit/manifest/AppData 证据后，才可重新评估为 PASS。
