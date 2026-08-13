# Task 4 Independent Review

### Spec Compliance

- ✅ `memory.py:213-246` 已接入 Task 2 的 `load_json` / `atomic_write_json`，写入明确传入 `backup_count=5`；当前 `memory.py` 与 `tests/test_persistence.py` 的 SHA-256 和检查点一致。
- ❌ 内存候选选择仍不完整：主文件不存在时 `memory.py:210-212` 直接返回，不会尝试有效备份；主文件是 `{}`、缺少核心字段的 legacy 对象，或带空 `reply` 的 v2 对象时，`memory.py:125-161` 又会把它当成有效候选，阻止后续有效备份恢复。
- ✅ `memory.py:216-246` 已移除 load/save 的 `[-3000:]` 硬截断；聚焦测试使用 3,001 个不同 `conversation_hash + reply` 的样例同时验证磁盘和重新加载后的数量。
- ✅ 正常加载路径下，向量缺失、JSON 损坏、schema 不匹配或 `memory_hash` 不匹配会由 `memory.py:670-705` 从已加载的 memory 重建；重建写入失败发生在 memory 原子写入之后，不会回写或破坏已验证 memory。
- ✅ `content_hash()` 使用 UTF-8、`sort_keys=True` 和紧凑分隔符；legacy 缺失 `created_at` 时现在归一化为空字符串，不再按每次 load 的当前时间变化。
- ❌ “向量重建失败必须阻断后续自动发送”没有完整实现。当前只有一次调用栈上的原始写异常，没有 `StyleMemory` 健康状态或供发送前检查的阻断接口；失败后的常驻进程仍可能继续走后续自动发送路径。
- ✅ 全新安装且 memory 主文件不存在、也没有备份时，当前实现不会创建 memory/vector 文件。
- ⚠️ 报告记录了所要求的 RED、聚焦 GREEN、memory/vector GREEN、全量 GREEN 和编译结果；差异包与基线能确认测试和生产变更内容，且本次按要求没有重跑完整测试，但仅凭现有材料不能独立证明 RED/GREEN 的实际执行先后。
- ⚠️ 典型旧版无 `schema_version` 文档可以加载，缺失 `created_at` 的 hash 也稳定；但 legacy 兼容边界过宽，已演变成候选有效性缺口，而不是单纯的兼容支持。

### Strengths

- memory 主体与向量索引都统一使用验证、临时文件、`fsync`、原子替换和五个有效快照的持久化层，替代了旧的直接读写与临时 `bak-auto-*` 方案。
- memory 是 hash 的唯一输入源；向量文件不再仅凭存在就被信任，版本、维度、基本结构和 memory hash 均经过检查。
- memory 写入先完成并验证，随后才重建向量；因此向量写失败不会把已验证 memory 替换为空文件或回滚成坏内容。
- 3,001 条唯一记录的保存/重载、坏主文件恢复、全部候选损坏、向量四类失效、稳定 hash、重建失败不改 memory、新安装无文件等测试覆盖了主要正向与失败路径。
- 去重键保留既有的 `conversation_hash + reply` 语义；相同会话 hash 但不同答案不会互相覆盖。

### Issues

#### Critical

1. **`memory.py:226-246, 681-705` — 向量失败没有形成可供后续自动发送检查的阻断状态。**
   - **影响：** `rebuild_vector_index()` 只把 `OSError` 等底层异常抛给当前调用者。若失败发生在运行期 `save()`（例如一次手动学习/编辑已经成功写入 memory，随后 vector 写失败），对象上没有 `vector_ready=False`、错误详情或 typed blocking error 被保留。异常处理完或工作线程退出后，后续 managed-auto 发送无法查询这次失败，因而违反“失败必须可观察并阻断后续自动发送”的绑定约束。现有 `tests/test_persistence.py:218-239` 只证明一次 `load()` 会抛原始 `OSError` 和 memory 字节不变，没有证明后续发送被阻断。
   - **修复：** 定义明确的 typed vector blocking error，并在 `StyleMemory` 上维护失败状态/最近错误；每次重建开始前置为不可用，原子写成功后才置为可用。自动发送的最终 preflight 必须检查该状态并 fail closed。增加“运行期 save 的 vector 写失败 -> memory 仍有效 -> 后续自动发送被拒绝并显示原因 -> 成功重建后才解除”的集成回归。

#### Important

1. **`memory.py:210-213` — memory 主文件缺失时绕过 Task 2 的备份恢复。**
   - **影响：** 只要 `memory.json` 不存在，`load()` 就直接返回当前对象；即使 `memory.json.bak.1` 到 `.bak.5` 中有有效正文也不会调用 `load_json()`。这把“全新安装无任何候选”和“主文件丢失但有可恢复备份”混为一谈，后者会静默呈现空 memory，之后的保存还可能逐步挤掉原有有效快照。
   - **修复：** 仅当主文件和五个备份全部不存在时返回空的新安装状态；只要任一候选存在就调用 `load_json()`。增加“missing primary + valid bak.1 恢复且 `load_result.recovered=True`”以及“missing primary + all invalid backups 抛 `JsonDataError` 且字节不变”的测试。

2. **`memory.py:125-161` — memory validator 在候选选择前没有完整验证 legacy 和字段语义。**
   - **影响：** 缺少 `schema_version` 时默认 v1，但 v1 的 `style_summary`、`examples`、`common_phrases` 全都可缺省，因此 `{}` 会被当作有效主文件；legacy example 也不要求可归一化的非空 `reply`。即使 v2 同时具备四个必需键，空 `reply` 仍能通过 validator，随后在 `memory.py:218-221` 被静默丢弃。上述主文件都会抢先于有效备份被选中，造成空/缩水的运行时 memory；后续 save 会把这种状态持久化并轮换备份。`tests/test_persistence.py:114-169` 没覆盖这些“JSON 合法但语义无效”的主文件。
   - **修复：** 根据真实旧版写盘格式定义严格 v1 schema：要求旧版正文核心顶层字段类型正确，并确保每个样例至少能归一化为非空 reply（以及生产 writer 必需的其他核心语义）；v2 同样校验非空核心字段。合法的 `examples=[]` 文档仍可作为明确的空库，但缺字段/含无效样例的对象必须让位给备份或最终抛 typed error。补充 malformed-primary + valid-backup 的逐字段回归。

3. **`memory.py:164-190` — vector validator 接受非有限浮点数和不合法 bucket。**
   - **影响：** Python JSON 解析器会接受 `NaN`/`Infinity`，当前 `type(weight) in {int, float}` 会继续放行；bucket key 也不要求为 `0..383` 的十进制索引，空 vector 同样能作为 item 通过。带当前 `memory_hash` 的这类损坏向量不会重建，`Infinity` 还可能把错误样例的相似度推到最高，影响自动回复候选排序。
   - **修复：** 对 weight 使用 `math.isfinite()` 并约束为重建器可产生的数值范围；要求 bucket 是 `[0, VECTOR_DIMENSION)` 内的规范十进制索引、item vector 非空，并验证向量 item 的 hash/source/reply 至少与 authoritative memory 一致。增加当前 memory hash + `NaN`/`Infinity`/越界 bucket 的重建测试。

#### Minor

1. **`tests/test_persistence.py:85-93, 282-294` — 3,001 条测试没有明确刻画去重允许丢弃的边界。**
   - **影响：** 当前 3,001 条样例的 hash 和 reply 全部唯一，只能证明没有硬截断；同一 `conversation_hash + compact(reply)`、但来源/机构/题目等元数据不同的记录仍会由 `memory.py:1088-1103` 合并。该行为是既有策略，未必违反本任务，但测试和报告的“3,001 records preserved”容易被理解为所有合法记录都逐条保留。
   - **修复：** 在规格或测试中明确区分“禁止数量截断”和“允许语义去重”；增加超过 3,000 条且混合明确重复项的测试，分别断言唯一项全部保留、仅契约定义的重复项被合并，并验证需要保留的元数据不会因选中首条而丢失。

### Assessment

**Needs fixes**

当前实现已经消除了硬 3,000 条截断，也建立了 memory-hash 驱动的向量重建主路径；但候选恢复仍会在“主文件缺失”或“语义损坏但 JSON 合法”时静默绕过有效备份，且 vector rebuild 失败没有形成后续自动发送可查询的 fail-closed 状态。这三项直接违反绑定的数据安全约束，应修复后再做聚焦复核。
