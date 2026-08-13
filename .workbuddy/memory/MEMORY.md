# 聊小智项目长期记忆

## 2026-08-14 当前权威状态（覆盖下方历史条目）

- 自动发送只允许已批准知识记录答案原文或精确 `暂未查到对应答案`；大模型仅候选排序/一致性判断，不得生成或改写正文。
- 当前知识合同阈值为最低分 30、领先差 8；社团/民非/both/unknown 分区，同分区不同答案 conflict 隔离。下方 20 分、自由生成/规则兜底描述均为历史，禁止复用。
- 源码和 frozen EXE 统一读取 `%APPDATA%\WeChatStyleReplyAssistant`，测试/staging 仅通过 `WECHAT_ASSISTANT_DATA_DIR` 覆盖；EXE 不再读包内 `user_data`。
- 正式包采用不可覆盖的版本化 windowed onedir；当前候选为 `2026.08.14.5`。不再覆盖旧 v4 目录；失败包保留，修复版递增版本号。
- Phase 2 PASS：真实来源 449 -> 435，14 条完全重复，19 组/40 条冲突。正式 AppData 仍待 Phase 4 最终 apply。
- Phase 3 PASS：个人/群聊共用唯一串行 worker；自动发送前精确前台身份和新 OCR 轮次复核；verified 气泡才记成功；uncertain 不重发；启动 baseline 和持久 ledger 防重复。
- Phase 4 进行中：`.5` EXE package-check 和显式 runtime-check PASS，2118 个清单文件 mismatch 0、私密文件 0；隔离 staging migration/runtime-check PASS；真实企业微信观察/受控发送、正式迁移、快捷方式和最终 push 尚待完成。
- Git 当前启用。用户要求所有任务做完后统一 push；中间只做本地提交。
- 当前零上下文交接以 `docs/项目工作提示词_给新会话.md`、`.workbuddy/memory/2026-08-14.md` 和 `docs/正式上线验收清单_20260814.md` 为准。

## 项目约定（用户明确要求，必须遵守）

- **打包纪律（2026-08-04 用户要求）**：以后修改代码后**不要每次都打包一份新 EXE**。需要打包时，直接在当前开发测试版目录（`dist\聊小智_开发测试_20260803_v4\聊小智\`）上**覆盖更新**，不新建 v5/v6 等版本目录；日常小改动用源码（`启动.bat` / `.venv`）验证即可。
- **不改用户数据**：不覆盖 `%APPDATA%\WeChatStyleReplyAssistant\settings.json`、`memory.json`、`vector_memory.json`；EXE 包内 `user_data` 同理（改设置前先备份）。
- **测试优先**：任何匹配阈值/决策规则调整，先写回归测试 → 改 `memory.py` 统一决策 → 复跑全量 + compileall + 真实数据烟测。
- **交接文档**：`\.workbuddy\memory\handoff_2026-08-03_enterprise-kb-final-replies.md`（企业库统一决策 + 最终发送拦截）；阶段分析：`\.workbuddy\memory\analysis_phrasebook_matching_2026-08-03.md`。

## 关键架构事实

- 匹配决策集中在 `memory.py`：企业库 = `enterprise_candidates()` + `_annotate_enterprise_decision()`（send/ambiguous 统一决策，测试页 `enterprise_scored_examples` 与运行时 `_select_enterprise_answer` 都消费它）；普通话术库 = `phrasebook_candidates()`（scored_examples 同款打分）。
- 阈值常量：`ENTERPRISE_LOCAL_CONFIDENT_FLOOR = 20`（企业库可靠底线，2026-08-04 由 40 降为 20）、`ENTERPRISE_LOCAL_CONFIDENT_MARGIN = 8`（明显领先差距，**并列/接近必须拒答，不能去掉**）、`PHRASEBOOK_CONFIDENT_SCORE = 20`（普通话术库最低匹配值，低于则"暂时未查到对应答案。"）。
- `generate()` 决策链：企业分支（高置信直发/范围未命中固定文案）→ 话术库优先（memory/model_memory）→ 模型 → 知识库有条目但低分 → `暂时未查到对应答案。` → 空库走本地规则。
- EXE（frozen）模式读 `EXE目录\user_data\memory.json`，源码模式读 `%APPDATA%\WeChatStyleReplyAssistant\`——**数据不互通**，换包需重新导入或复制 user_data。
- **知识库导入两种路径（2026-08-09 确认，重要区别）**：UI 正式入口 `import_chat_zip_to_memory_with_llm`（大模型识别）→ 同 source **叠加不清空**（chat_training.py:393 注释明确）；命令行/无 Key 路径 `import_chat_zip_to_memory`→ `_import_training_files` **同 source 替换**。用户平时 UI 导入是叠加。
- **知识库评分核心**：`enterprise_candidates` 55 分权重压在 token 覆盖率（`_text_tokens` 为 2-8 字串+n-gram），**问题问法越多命中率越高**——单问法口语命中 ~35%，每知识点 2-4 问法可到 ~97%。module/subcategory 元数据参与评分（起名好白送分）；数字比例加分（`_enterprise_ratio_bonus`）。
- 运行环境：`.venv` Python 3.11.9（已装 openpyxl 3.1.5、pytest）。reply_engine.py 末尾 `_readable_*` 版本覆盖旧函数，改逻辑必须改 readable 版本。

## 计划中的新功能（2026-08-07 探讨中，未实现）

### 1. 左侧会话列表红点自动切换（用户已决策 2026-08-07）
- **触发机制（双重保险）**：
  - 当前聊天框 OCR 内容哈希连续 N 轮未变 → 才允许启动左侧列表 OCR 轮询
  - 轮询频率：每 2 秒一次
  - 识别到红点后**再次**确认聊天框哈希仍未变（保险二）
  - 焦点不在输入框 + 最近 1.5s 无键盘活动（保险三，用户未在输入）
  - 红点选第一个 y 最小者（从上到下），且 ≠ 当前会话标识
  - 全部通过 → pyautogui 点击该行头像区 + sleep(1.2s) 等待刷新
- **关键坑**：同窗口切会话 hwnd 不变，**现有 `window_key`(hwnd+title) 去重失效**。必须新增"当前会话标识"，用顶部标题文字作 key（替代 hwnd+title）。
- **集成点**：新增旁路，**不动现有 `_monitor_loop` 主路**（主路继续盯前台 + `_process_window`，零回归）。
- **新增模块**：`conversation_list.py` 内放 `ConversationListMonitor`、`ChatIdentityTracker`、`ChatStateGuard`。

### 2. 会话类型判定（四分类，用户已决策 2026-08-07）
- **四类**：personal（个人）/ group（群聊）/ system（系统号）/ unknown（兜底）
- **判定优先级**：system（名称）→ group（头像九宫格）→ personal（默认）→ unknown
- **system 系统/服务号**（**直接跳过不回复**）：企业微信内置应用/第三方应用/公众号推送。
  - 白名单（基于截图，实测补全）：客户联系、邮件提醒、行业资讯、企业微信团队、腾讯云企业服务、管理企业、腾讯云客服@...
  - 辅助关键词：团队/客服/服务/提醒/管理/联系
  - 辅助信号：头像是单色 logo
  - 顶部标题 OCR 的作用**专门用于识别 system 名称**（不再辅助群聊判定）
- **group 群聊判定核心信号**：头像 = **多个独立头像方块拼接的九宫格**（2×2/3×3 网格，每格独立头像，格间有分隔）。
  - 算法：头像区域分 3×3 子块，算相邻子块主色差异度。群聊九宫格=相邻子块主色跳变大；个人合影=子块主色连续渐变。
  - **关键陷阱**：单个合影头像也会色彩丰富，**必须用"子块边界颜色跳变"区分，绝不能用整体色彩丰富度**，否则合影头像误判成群聊。
- **personal 个人**：单方块头像（一个完整圆形/方形图片）。默认兜底。
- **unknown**：判定不确定时跳过+提示，绝不把群聊误当个人发话术。
- **关键改造**：必须新增"顶部标题栏区域"截图+OCR（现有 chat_area 裁剪 y 从 0.10 起，丢了顶部）。
- **截图标定样本**（5.0.9.6060，用户已确认分类）：
  - 第1条"-@微信" = **personal**（单方块头像；"@微信"是互通联系人标记，**不是群特征**）
  - 第2~8条（客户联系/邮件提醒/行业资讯/腾讯云客服/企业微信团队/腾讯云企业服务/管理企业）= **system**
  - 第9条"阳光，-"带"外部"标签 = **group**（九宫格头像）

### 3. 群聊专属问答客服（2026-08-07 已探讨确认，交接文档已写）
- **交接文档**：`docs/群聊客服功能_功能交接.md`（前置依赖=四分类 group 分支）
- **UI**：当前账号用户名输入框（"当前聊天上下文"上方），首次空弹提示，持久化 `settings.wecom_account_name`，下次启动不覆盖直到修改
- **核心逻辑**：群聊消息开头 @ 当前账号用户名 → 走回复；没@ → 认定"聊天内容无变化"不回复
- **@ 识别**：@ 是黑色文字（非蓝色），OCR 无障碍；容错匹配（去空格标点后比较）
- **发送者名字**：灰色名字 + 绿色平台标记（@微信 等），字符串规则去标记后缀得纯名字
- **回复格式**：文字 @（非真@提醒），`@发送者名字 回复内容`，复用 sender.py
- **话术**：复用现有企业库/话术库/模型/知识库
- **群聊 OCR 间隔**：1200ms → 800ms（`settings.group_poll_interval_ms`）
- **触发扫描扩展**：群聊无@=聊天内容无变化，等同"我方发出"待遇；@我时不触发扫描（走回复）
- **@ 被覆盖**：接受漏掉（最新消息为准）
- **群聊上下文**：只取 @我 那条消息作为问题（不取前文）
- **右侧群成员面板**：自动检测"我"名字作预填值（备用）
- **用户名示例**：程正恒

### 平台与版本
- 平台：企业微信（`wechat_window.py::_match_platform` 已只认 wecom）
- **用户版本：5.0.9.6060**（不是代码注释里写的 4.x）→ 所有比例、颜色阈值、布局常量**必须实测重定**，不能照搬旧注释

### 用户已拍板（2026-08-07 全部确认）
- ✅ 触发条件：托管模式 + 最新消息为我方发出 + 持续 **2 秒** → 扫描左侧列表（每 2 秒一次）
- ✅ 6 种打断：最新消息变对方 / 用户输入中(焦点在输入框+1.5s内有键盘) / 发送中(managed_send_inflight) / 处理中(processing) / 企业微信不在前台 / 切换重试中
- ✅ 群聊红点也切过去判定（方案A）；红点切过去后自动消失（5.0.9实测），天然不循环
- ✅ 多红点：从上到下，选第一个 y 最小且 ≠ 当前会话标识
- ✅ 切换失败：重试1次→再失败放弃；连续3次→UI正中央+最上层+系统提示音+暂停5分钟
- ✅ 企业微信不在前台：跳过本轮，不抢焦点
- ✅ 切换后主路以 force 模式重置（清空会话状态重新识别）
- ✅ 发送中/处理中禁止切换
- ✅ system 白名单：先截图这7个，关键词兜底，后续再加
- ✅ 群聊分支：结构预留，本次暂不回复+UI提示待补齐，等用户后续给需求
- ✅ 交接文档：`docs/会话列表自动切换_功能交接.md`（给执行 agent 的提示词，执行 agent 熟悉项目）
