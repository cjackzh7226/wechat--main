### Spec Compliance

- ❌ 总体未通过。成功迁移后旧明文凭据会被 Task 2 的备份轮换保留在 `settings.json.bak.*`，直接违反“API keys 从不序列化到 settings JSON”。
- ✅ `settings.py` 和 `secure_store.py` 默认路径消费 Task 1 的 `app_paths.config_path()` / `credential_path()`，源码与 EXE 共享 AppData 路径的接口对齐。
- ✅ DPAPI 调用形状正确：`CryptProtectData` 接收字节并返回密文字节，`CryptUnprotectData` 解包 `(description, payload)`；解密后还有严格 bytes/UTF-8/JSON/键值字符串检查。
- ✅ 凭据二进制写入使用同目录临时文件、`flush`、`fsync`、`os.replace`，并且只轮换可解密且可解析的快照；有效备份压紧与五槽位逻辑符合简报。
- ✅ legacy `managed_auto_reply=true` 被迁移为 `observe`，而兼容属性只在 `full` 时返回真；加密写入或读回失败时，主 settings 文件未被改写且内存模式被强制为 `off`。
- ✅ 布尔、数字范围、整数、非有限浮点数以及 `chat_area` 的 `0..1` 和几何顺序边界处理基本符合规范。
- ❌ 持久化白名单排除了直接 key 字段和运行时对象，但仍允许将含真实 Bearer/API key 的任意 `custom_headers` / `custom_body` 明文写入 settings JSON。
- ⚠️ UI 已移除可选的自由生成模式和联网勾选框，但真实 `main.py` 旧回调会破坏新设置不变量；回复来源 tooltip 也仍在宣传已移除的大模型和模型兜底。
- ✅ 未发现 Git 初始化或提交；当前三个交付源文件的 SHA-256 与 checkpoint 一致。
- ⚠️ RED 原始文件确实记录 `17 failed, 87 passed`，之后也有 GREEN 和全量回归证据；但报告所称“目标命令原始摘要 103 passed”与其指向的 GREEN 原始文件 `102 passed` 不一致。

### Strengths

- 凭据层将加解密函数做成可注入依赖，便于验证密文往返、损坏主文件回退和备份轮换，同时生产默认仍是 Windows DPAPI。
- `CredentialStore.load()` 遍历主文件与备份，解密层、编码层或 JSON 层任一失败都会继续查找下一份有效快照，而不会把损坏数据当成空凭据。
- v1 迁移顺序在主路径上是对的：先合并明文与已有密文，再加密写入、精确读回比对，最后才写 v2 settings。
- 显式 `PERSISTED_FIELDS` 代替 `asdict(self)` 是正确的安全方向，直接的 `api_key` / `vision_api_key` / `provider_api_keys`、`config_path`、`credential_store` 和动态属性不会被自动序列化。
- `math.isfinite` 防止 NaN/Infinity 绕过范围校验；`type(...) is bool/int/float` 也避免了 Python 中 `bool` 是 `int` 子类导致的伪数字。

### Issues

#### Critical

1. **`settings.py:400` — 成功迁移会把明文凭据复制进 settings 备份。**
   - **影响：** `load()` 在 DPAPI 写入与读回成功后调用 `_persist_settings()`。该方法把允许 v1 且允许 v1 key 字段的 `_validate_raw_document` 传给 Task 2 `atomic_write_json`。Task 2 写入器会在替换主文件前轮换所有“验证有效”的快照，因此旧 `settings.json` 及旧备份中的 `api_key` / `provider_api_keys` / `vision_api_key` 会以明文进入或继续留在 `settings.json.bak.*`。主文件无 key 的现有测试无法发现这个泄露。
   - **修复：** 只在 DPAPI 精确读回成功后，使用迁移专用的无密钥验证器写 v2；该验证器对所有版本的三类 key 字段都应拒绝，使轮换跳过并清理含明文的旧备份。新增回归必须遍历主文件和所有 `settings.json.bak.*` 断言无任何测试密钥，并保留“DPAPI 失败时原主文件逐字节不变”的测试。

#### Important

1. **`settings.py:84` — 白名单仍可序列化嵌在自定义 Header/Body 中的真实凭据。**
   - **影响：** `custom_headers` 和 `custom_body` 是 UI 可编辑的任意字符串，并被无条件纳入 settings payload。用户如果把 `Authorization: Bearer <secret>` 或其他 token 直接写入模板，它仍会进入主 JSON 及五份备份，尽管字段名不含 `key`。
   - **修复：** 将可持久的 custom HTTP 配置限制为只能使用 `{api_key}` 等凭据占位符的模板，拒绝字面量认证值；如果产品必须支持其他秘密 header/body 参数，应将它们拆入 `CredentialStore`。测试应使用真实字面量 token 扫描主 JSON 与全部备份，不能只检查字段名。

2. **`float_ui.py:600` — 固定 knowledge-only 之后仍调用旧来源回调，会把设置改回非法模式并抛异常。**
   - **影响：** 真实 `main.py` 注册的 `_source_mode_changed` 只认 `model/memory/model_memory`，收到 `knowledge_only` 时会把对象改为 `model_memory` 再调用 `save()`。新 `validate()` 随即拒绝该值，因而用户即使只点击 UI 里唯一的“仅使用知识库”项，也会在 Tk 回调中抛错，并把内存设置留在无法再保存的非法状态。现测试用 `list.append` 代替真实回调，所以得到了假阳性 GREEN。
   - **修复：** 对固定不变的来源不再调用旧“切换模式”回调，或者同步提供一个明确接受 `knowledge_only` 且不能改回旧模式的新回调契约。增加一个使用真实旧回调语义的集成测试，验证点击后无异常且对象/磁盘都仍是 `knowledge_only`。

3. **`float_ui.py:583` — `observe/controlled` 与旧 `managed_changed(bool)` 回调的持久化兼容实际失效。**
   - **影响：** UI 先保存新模式，再对 `observe/controlled` 调用旧回调 `False`。真实 `main.py._managed_changed(False)` 通过兼容 setter 把模式改成 `off` 并再次保存。UI 之后只恢复内存中的 `selected`，没有再保存，所以重启后丢失新模式。设置对话框的新控件在 `float_ui.py:1478` 又绕过了这套回调，会让顶部 UI 状态与运行时状态不同步。
   - **修复：** 将模式变更收敛到一个方法。如果必须调用旧 bool 回调，应在回调返回后重新设置新模式并做唯一一次最终保存；设置对话框也应走同一路径。测试中的假回调应模拟旧 setter 与 `save()`，然后重新从磁盘加载断言 `observe/controlled` 存在。

4. **`settings.py:208` — 备份候选的原始文档验证不完整，部分无效主文件不会回退到有效备份。**
   - **影响：** `_validate_raw_document` 未检查 `api_provider`、`base_url`、`model`、`vision_*`、`custom_*`、`ocr_engine`、`wecom_account_name` 等字符串字段的类型。如果主文件中 `api_provider` 被破坏为数字，`load_json` 会把它当成有效候选并停止搜索，随后 `_apply_document` 才抛 `SettingsValidationError`；后面的有效 `.bak.*` 永远没有机会恢复。这破坏了 Task 2 “只从验证有效的候选加载”的实际契约。
   - **修复：** 在传给 `load_json` 的 validator 中完成全部持久字段的严格类型与语义验证，不要把候选有效性的一部分延迟到选中候选之后。增加“主文件字符串字段类型错误 + 有效备份”的聚焦回归。

#### Minor

1. **`settings.py:176` — 公开 `validate()` 对不可哈希的 `managed_mode` 抛出 `TypeError`，而非 `SettingsValidationError`。**
   - **影响：** `AppSettings(managed_mode=[]).validate()` 在集合成员检查时直接抛 `TypeError`，与“严格 schema 失败统一为 `SettingsValidationError`”的接口预期不一致。
   - **修复：** 先检查 `type(self.managed_mode) is str`，再做枚举成员检查，并增加不可哈希输入测试。

2. **`float_ui.py:65` — 回复来源 tooltip 仍宣传大模型自由生成与模型兜底。**
   - **影响：** 虽然菜单只剩 `knowledge_only`，但用户悬停后仍看到三种旧模式的说明，会认为正式版仍有自由生成入口。
   - **修复：** 删除旧来源文案并改为“仅使用本地知识库，未命中时明确返回无答案”的单一说明。

3. **`task-3-report.md:35` — 目标 GREEN 计数与指向的原始证据不一致。**
   - **影响：** 报告写 `103 passed, 182 deselected in 1.95s`，但 `03-settings-credentials-green.txt:110` 是 `102 passed, 182 deselected in 2.19s`。完整回归与补充单测表明最终代码有 GREEN 证据，但当前报告不是可逐项核对的原始记录。
   - **修复：** 不要改写历史 RED；为最终测试集重新保存一份聚焦 GREEN 原始输出，或将报告改为精确引用“102 个聚焦 GREEN + 1 个补充 GREEN + 333 个全量 GREEN”。

### Assessment

**Needs fixes**

至少必须先修复明文 settings 备份泄露、custom HTTP 字段的凭据绕过，以及两类旧 UI 回调的真实集成问题，再进行一轮聚焦复核。
