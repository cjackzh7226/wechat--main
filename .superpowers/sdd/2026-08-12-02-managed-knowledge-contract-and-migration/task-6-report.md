# Task 6 — 449 条知识迁移只读演练报告

日期：2026-08-13  
状态：完成（仅 dry-run，未应用）

## 结论

迁移工具已在真实 v4 来源上完成确定性只读演练，结果为 **PASS**。来源 449 条全部进入可追溯归档，形成 435 条规范记录；14 条完全重复项被确定性折叠；19 个问题冲突组覆盖 40 条来源记录。正式 AppData 中原有 3 条记录没有被删除、替换或写回，正式目标文件的前后 SHA-256 完全一致。

## 实现范围

- 新增 `tools/migrate_formal_knowledge.py`，默认行为固定为 dry-run。
- `--apply` 必须同时满足：显式应用开关、预期来源/输出/重复/冲突数量、正式备份及其哈希、进程单实例锁和目标未漂移校验；本次没有进入该路径。
- 迁移复用运行时相同的 `question_match_key()`、机构范围推断、记录哈希、冲突审计与确定性归档逻辑，避免演练与正式消费口径分叉。
- 演练报告路径有正向保护，禁止覆盖 settings、memory、vector、credentials、ledger、导入暂存、SOP、日志及其备份/事务文件。
- dry-run 同样获取单实例锁，并在计算前后重新读取正式目标；目标发生漂移即失败，不以“复制 before hash”为 after 证据。

## 真实演练结果

来源文件：`dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json`  
正式目标：`C:\Users\14913\AppData\Roaming\WeChatStyleReplyAssistant\memory.json`  
报告：`artifacts\formal-hardening\2026-08-13\knowledge-migration-dry-run.json`

| 指标 | 结果 |
|---|---:|
| 来源记录 | 449 |
| 规范输出记录 | 435 |
| 完全重复 | 14 |
| 冲突组 | 19 |
| 冲突来源记录 | 40 |
| 归档来源记录 | 449 |
| scope: unknown / social / mfei / both | 248 / 82 / 68 / 51 |
| status: active / conflict / pending / inactive | 171 / 38 / 226 / 0 |
| 正式目标记录（演练前） | 3 |
| `applied` | `false` |

- 来源 SHA-256：`99021624250CA59FA72F47491448D22ADBD0DB0F25CD84E15085191B4C1CA11B`
- 正式目标演练前后 SHA-256：`9DD71CBD01468025011311F5481F115C6B39946E74842FAF81ED26FBABE1F2C8`
- 演练报告 SHA-256：`2C85349839C45C3211811A61177B60876B4BF68EE215E1EB4C534B70D3379D00`

最初演练使用旧冲突键得到 17 组/36 条；里程碑自审发现，审计键与运行时匹配规范不一致会漏掉数字两侧空格差异的两个真实冲突组。统一到 `question_match_key()` 后，新增识别两组：来源索引 101/131 的“监事不足 3 人设置监事长怎么办？”及其无空格变体，以及索引 102/132 的同类变体。最终基线因此正确更新为 19 组/40 条，没有为了维持旧计数而放行冲突。

## TDD 与验证证据

- Task 6 初始缺口、默认 dry-run、计数门禁、备份门禁、正式路径拒绝、目标漂移和单实例锁均有先失败后通过的聚焦测试。
- 最终知识合同与迁移聚焦批次：`tests/test_knowledge_contract.py tests/test_migration.py -v`，**220 passed**。
- Phase 2 最终全量门禁：**768 passed，另 9 subtests passed，0 failed，74 warnings**；JUnit 聚合为 777 tests、0 failures、0 errors、0 skipped。
- 11 个相关模块在目标 Python 3.11.9 下 `py_compile`：exit 0。

## 明确未执行

- 没有使用 `--apply`。
- 没有删除正式目录中的 3 条旧记录。
- 没有写入正式知识库，也没有建立正式迁移备份。
- 没有启动企业微信真实发送、端到端发送或 EXE 构建。

