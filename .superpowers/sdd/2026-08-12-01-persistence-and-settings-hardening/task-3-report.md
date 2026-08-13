# Task 3 报告：严格版本化设置与 Windows DPAPI 凭据

## 状态

PASS。仅实施任务 3 简报范围；未修改 `main.py`，未提前实施 memory/runtime 任务，未初始化 Git 或提交。

## 实施结果

- 新增 `secure_store.py`：`CredentialStore.load()` / `save()` 使用 Windows DPAPI；保护/解保护函数可注入，便于不依赖用户配置的行为测试。
- 凭据文件采用同目录临时二进制文件、`flush`、`fsync`、`os.replace`；只轮换可解密且可解析的历史快照，保留五份有效备份。
- `settings.py` 引入 `CONFIG_VERSION = 2`、`SettingsValidationError`、严格布尔/字符串/数值校验、全部指定边界和 `chat_area` 坐标顺序校验。
- 正式设置强制 `reply_source_mode="knowledge_only"`、`enable_web_search=False`；托管模式为 `off/observe/controlled/full`，受控聊天名单为严格字符串列表。
- legacy `managed_auto_reply=true` 只迁移到 `observe`；兼容属性仅在 `full` 时向旧 runtime 暴露为真，因此 legacy 迁移不会启动自动发送。
- v1 明文 `provider_api_keys`、`api_key`、`vision_api_key` 只有在加密写入且读回完全一致后才从 settings JSON 移除；保存/读回失败时保持原 settings 字节不变并强制 `managed_mode="off"`。
- v2 设置从显式字段白名单构建；不持久化 `config_path`、`credential_store`、任意动态运行时对象或任何 API key 字段/值。
- `float_ui.py` 的回复来源只保留 knowledge-only，不再提供联网搜索开关；提供四种 managed mode 与 controlled allowlist 输入。
- 保留旧 `managed_changed(bool)` 回调的最小兼容：仅 `full` 回传真，`observe/controlled` 回传假且不覆盖新模式；若后续提供 `managed_mode_changed(str)` 则优先使用新接口。

## TDD 证据

### RED

命令（使用独立临时 `WECHAT_ASSISTANT_DATA_DIR`）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or credential or string_false" -v
```

原始摘要：`17 failed, 87 passed, 180 deselected in 2.97s`。失败原因命中预期缺口：无 `SettingsValidationError` / `credential_store` 注入、无 `secure_store`、字符串伪布尔被接受、无严格数值边界、无 UI 模式常量/allowlist/managed callback，以及来源仍可切换到 model。原始输出：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-red.txt`。

补充有效备份 RED：`test_credential_store_skips_undecryptable_primary_and_loads_valid_backup` 为 `1 failed`，证明原实现未捕获 DPAPI 解密层的通用失败；最小修复后该测试 `1 passed`。

### GREEN

简报目标命令原始摘要：`103 passed, 182 deselected in 1.95s`。

完整 persistence：`32 passed in 0.63s`。

完整回归：`333 passed, 74 warnings, 4 subtests passed in 7.51s`。

GREEN 原始输出：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-green.txt`；完整回归输出：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-full.txt`。

## 覆盖的关键行为

- 字符串 `"false"` 对 `managed_auto_reply`、`auto_recognize`、`training_mode` 均被拒绝。
- `poll_interval_ms`、`group_poll_interval_ms`、`auto_reply_delay_seconds`、`capture_cooldown_seconds`、`search_max_results` 的上下界包含性、越界值、字符串和布尔伪数值均覆盖。
- `chat_area` 的 `0..1` 范围、`left < right`、`top < bottom` 与字符串伪数值均覆盖。
- model 来源和 web search 均由正式设置验证拒绝。
- legacy managed true 迁移到 observe，旧兼容发送门保持关闭。
- 注入 fake protect/unprotect 的凭据往返、密文中无明文、同目录临时写、`fsync`、`replace`、五份有效备份和不可解密主文件回退均覆盖。
- v1 明文三类 key 的成功迁移，以及加密失败/读回失败时原字节不变且 managed off 均覆盖。
- 持久化白名单排除运行时对象、动态对象及所有 key 字段；UI knowledge-only、四模式、allowlist 去重顺序及旧回调兼容均覆盖。

## 最终验证

- 目标测试：`103 passed, 182 deselected`。
- `tests/test_persistence.py`：`32 passed`。
- 真实 Windows DPAPI 临时凭据往返：`REAL_DPAPI_ROUND_TRIP=PASS`，未输出真实密钥。
- 编译：`.\.venv\Scripts\python.exe -m compileall -q -f secure_store.py settings.py float_ui.py`，退出码 0。
- 全量测试：`333 passed, 74 warnings, 4 subtests passed in 7.51s`。

## Checkpoint

文件：`artifacts/formal-hardening/2026-08-12/03-settings-credentials.sha256.json`

- `secure_store.py`: `5DD70DCD1D5C5B33F4622687CC9862102A8CF5BBA25C12B8F4D3FCD848D49BBA`
- `settings.py`: `6BA801B39D66D4ABFD3A1D5AB5701697612053C3693681902F41900651671DBE`
- `float_ui.py`: `7EAF64D2CDBC99270689B42E24DD759A8A836E66A4F5330987E525E85C8A6FFE`

## 文件清单

- 新增：`secure_store.py`
- 修改：`settings.py`
- 修改：`float_ui.py`
- 修改：`tests/test_persistence.py`
- 修改：`tests/test_core.py`
- 新增：本报告及 `artifacts/formal-hardening/2026-08-12/03-settings-credentials-*.txt/json`

## 自检与顾虑

- 确认未修改 `main.py`。本阶段通过兼容属性保证 observe/controlled 不会进入旧自动发送路径；四模式的完整 runtime 行为和 allowlist 执行必须由后续任务接入，不能在本任务内提前决定。
- DPAPI 密文绑定当前 Windows 用户/机器上下文；五份备份同样只能在可解密该密文的上下文恢复，这是 DPAPI 的预期安全边界。
- UI 行为由无桌面依赖的回调/选项测试覆盖；未进行人工可视化布局验收。若后续 UI 布局调整，应在可交互 Windows 桌面补充视觉检查。
- 全量测试中的 Pillow `Image.getdata` 弃用警告属于既有 `conversation_list.py`，与本任务无关。

## Fix Round 1（1 Critical + 4 Important）

### 修复结果

1. **Critical：迁移后清除 settings 主文件及全部备份中的明文凭据。** `_persist_settings()` 现使用迁移/持久化专用 `_validate_redacted_document()`；该 validator 对 v1/v2 均拒绝 `api_key`、`vision_api_key`、`provider_api_keys`。Task 2 的原子轮换因此只保留无密钥有效快照，并删除含密钥的旧备份。
2. **Important 1：自定义请求模板拒绝字面凭据。** `custom_headers` / `custom_body` 中，`auth/authorization/key/token/secret/password/credential` 类字段只允许 `{api_key}`、`Bearer {api_key}` 或 `Basic {api_key}`；任意位置的字面 Bearer token 也被拒绝。普通非凭据常量和既有请求模板仍可持久化。
3. **Important 2：固定来源不再调用旧 source callback。** `_set_source_mode()` 固定保存 `knowledge_only` 后不触发旧 `source_mode_changed`，避免 `main.py` 旧回调改成 `model_memory` 并留下无效内存状态。
4. **Important 3：统一 managed mode 变更入口。** `_set_managed_mode(mode, persist=...)` 是主界面和设置对话框的共同入口；旧 bool 回调返回后恢复所选模式，并由当前入口执行唯一一次最终保存。`observe` / `controlled` 从新建 `AppSettings` 重载仍保持。
5. **Important 4：raw validator 完整检查持久化字符串字段。** 传入 `load_json()` 的 validator 现在在选中候选前检查全部持久化字符串字段，并继续检查版本、布尔、数值、模式、allowlist 和 chat area；损坏主文件不再阻断有效 `bak.1` 接管。

### 新增或更新的行为测试

- `test_legacy_migration_purges_plaintext_keys_from_primary_and_all_settings_backups`
- `test_settings_reject_literal_credentials_in_custom_request_templates`（5 个参数：literal Bearer、header token、body token、body auth secret、body api_key）
- `test_settings_allow_api_key_placeholder_in_custom_request_templates`
- `test_settings_load_uses_valid_backup_when_primary_persisted_string_type_is_corrupt`
- `test_source_mode_callback_cannot_enable_free_generation`（更新为固定来源不调用旧 callback）
- `test_fixed_source_mode_ignores_real_legacy_callback_and_remains_reloadable`
- `test_observe_and_controlled_survive_real_legacy_managed_callback_on_disk`（2 个 subtests）
- `test_settings_dialog_managed_mode_path_uses_same_callback_safe_transition`

### RED 原始证据

首轮聚焦命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "legacy_migration_purges or literal_credentials_in_custom or allow_api_key_placeholder or persisted_string_type_is_corrupt or source_mode_callback_cannot or fixed_source_mode_ignores or survive_real_legacy_managed or settings_dialog_managed_mode" -v
```

实际输出：`11 failed, 2 passed, 284 deselected in 2.07s`；其中 `observe`、`controlled` 两个失败 subtests 均显示磁盘重载为 `off`。允许 `{api_key}` 的正向护栏和一个既有路径先通过。原始日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix1-red.txt`。

补充 `auth` 字段边界 RED：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py::test_settings_reject_literal_credentials_in_custom_request_templates -v
```

实际输出：`1 failed, 4 passed in 0.33s`，失败项为 `custom_body={"auth":"literal-body-secret",...}` 未被拒绝；修复字段名分词识别后，同命令为 `5 passed in 0.17s`。

### GREEN 原始证据

聚焦修复集合：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "legacy_migration_purges or literal_credentials_in_custom or allow_api_key_placeholder or persisted_string_type_is_corrupt or source_mode_callback_cannot or fixed_source_mode_ignores or survive_real_legacy_managed or settings_dialog_managed_mode" -q
```

准确输出：`12 passed, 284 deselected, 2 subtests passed in 1.03s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix1-green-focused.txt`。

目标 settings/credential/mode 集合：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or credential or source_mode or managed_mode" -q
```

准确输出：`114 passed, 182 deselected, 2 subtests passed in 1.91s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix1-green-target.txt`。

完整 persistence：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -q
```

准确输出：`40 passed in 0.61s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix1-green-persistence.txt`。

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

准确输出：`344 passed, 74 warnings, 6 subtests passed in 5.43s`。74 个 warning 仍是既有 Pillow `Image.getdata` 弃用警告。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix1-green-full.txt`。

编译与真实 DPAPI：

- `.\.venv\Scripts\python.exe -m compileall -q -f secure_store.py settings.py float_ui.py`：`COMPILEALL=PASS`。
- 独立临时 `WECHAT_ASSISTANT_DATA_DIR` 下真实 DPAPI 写入/读回：`REAL_DPAPI_ROUND_TRIP=PASS`。

### Fix Round 1 Checkpoint

`artifacts/formal-hardening/2026-08-12/03-settings-credentials.sha256.json` 已更新；以下哈希取代本报告前文的初始实现 checkpoint：

- `secure_store.py`: `5DD70DCD1D5C5B33F4622687CC9862102A8CF5BBA25C12B8F4D3FCD848D49BBA`
- `settings.py`: `EA55E873498482C1BBD5052DDF13110C33993968ED21A3F9F0FE9E8E585316C2`
- `float_ui.py`: `FDBC5362B06F2D4ECDDA11D6790FD9B2109CA947C9740DC438538B3F2A68E5BB`

### Fix Round 1 顾虑

- 凭据模板校验依据语义字段名与 Bearer 形式识别；语义中性的普通业务常量允许保留。若未来新增新的凭据字段命名，需同步扩展该字段分类并先加拒绝测试。
- 未修改 `main.py`。本轮通过 UI 兼容边界隔离旧 callback；四种 managed mode 的完整 runtime 执行仍属于后续任务。

## Fix Round 2（2 Open Items）

### 修复结果

1. **复合敏感字段名不再绕过 custom credential guard。** 字段名先按 camelCase 边界、连字符、下划线及其他非字母数字字符规范化分词；词元包含 `auth`、`authorization`、`authentication`、`oauth`、`key`、`token`、`secret`、`password`、`credential` 时，其值只能是 `{api_key}`、`Bearer {api_key}` 或 `Basic {api_key}`。因此 `X-Auth`、`Proxy-Authorization`、`access_token`、`client_secret`、`password` 的 literal 均被拒绝，而相同字段的占位符模板被允许。
2. **v1 凭据字段在候选选择前严格校验。** `_validate_raw_document()` 在交给 `load_json()` 选择候选时即要求 `api_key` / `vision_api_key` 为 `str`，`provider_api_keys` 为完整 `dict[str, str]`。畸形 primary 会被判为无效，随后有效 `settings.json.bak.1` 接管，不再先选 primary 后由 `_legacy_credentials()` 强制 managed off。

### 新增测试

- `test_compound_credential_fields_reject_literals_and_keep_all_settings_snapshots_clean`（5 个参数；逐一扫描 primary 与 5 个 backups，断言测试秘密均不存在）
- `test_compound_credential_fields_allow_api_key_placeholder`（5 个参数）
- `test_v1_malformed_credential_field_is_rejected_before_valid_backup_selection`（3 个参数：`api_key` 非字符串、`vision_api_key` 非字符串、`provider_api_keys` 非 `dict[str,str]`）

### RED 证据

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "compound_credential_fields or v1_malformed_credential_field" -v
```

实际输出：`5 failed, 8 passed, 40 deselected in 1.12s`。

- `X-Auth` 与 `Proxy-Authorization` 两个 literal 用例未抛出 `SettingsValidationError`。
- 三个 v1 畸形 primary 均错误地保留默认 `api_provider="zhipu"`，未加载 backup 的 `deepseek` 和区分模型名。
- `access_token`、`client_secret`、`password` 的 literal 以及五个 `{api_key}` 正向用例由已有部分防线先通过。

原始日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix2-red.txt`。

### GREEN 证据

聚焦命令同上，准确输出：`13 passed, 40 deselected in 0.78s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix2-green-focused.txt`。

目标 settings/credential/mode 集合：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "settings or credential or source_mode or managed_mode" -q
```

准确输出：`127 passed, 182 deselected, 2 subtests passed in 2.57s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix2-green-target.txt`。

完整 persistence：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -q
```

准确输出：`53 passed in 1.25s`。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix2-green-persistence.txt`。

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

准确输出：`357 passed, 74 warnings, 6 subtests passed in 6.35s`。74 个 warning 仍为既有 Pillow `Image.getdata` 弃用警告。日志：`artifacts/formal-hardening/2026-08-12/03-settings-credentials-fix2-green-full.txt`。

编译与真实 DPAPI：

- `.\.venv\Scripts\python.exe -m compileall -q -f secure_store.py settings.py float_ui.py`：`COMPILEALL=PASS`。
- 独立临时 `WECHAT_ASSISTANT_DATA_DIR` 下真实 DPAPI 写入/读回：`REAL_DPAPI_ROUND_TRIP=PASS`。

### Fix Round 2 Checkpoint

`artifacts/formal-hardening/2026-08-12/03-settings-credentials.sha256.json` 已更新；以下哈希取代此前 checkpoint：

- `secure_store.py`: `5DD70DCD1D5C5B33F4622687CC9862102A8CF5BBA25C12B8F4D3FCD848D49BBA`
- `settings.py`: `3FF501FA9C77C396636E59B4E402C6F3B3FAAD414E2118E1BAF846FB27886AE3`
- `float_ui.py`: `FDBC5362B06F2D4ECDDA11D6790FD9B2109CA947C9740DC438538B3F2A68E5BB`

### Fix Round 2 顾虑

- 字段名识别基于规范化语义词元，覆盖本轮明确要求及常见 camelCase/连字符/下划线变体；未来若协议引入全新凭据语义词，应先增加 literal 拒绝与 placeholder 允许测试再扩展词表。
- 未修改 `main.py`，未处理已记账 Minors。
