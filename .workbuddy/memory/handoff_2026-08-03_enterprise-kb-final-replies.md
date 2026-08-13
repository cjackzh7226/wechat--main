# 聊小智项目阶段交接文档

> 交接日期：2026-08-03  
> 本文档面向一个完全没有本项目上下文的新 Agent。请先读完本文档，再查看代码和测试。  
> 本阶段主题：企业年报知识库的模糊匹配、测试命中与实际发送一致性、最终发送拦截。

## 0. 给接替 Agent 的强制起点

1. **必须继续在原项目目录修改：**

   `D:\Vibe C\客服\wechat--main`

2. 这是当前唯一应继续使用的项目路径。请在该目录中直接查看和修改现有的 `memory.py`、`reply_engine.py`、`main.py`、`float_ui.py`、`tests\test_core.py` 等文件。
3. 当前目录不是 Git 仓库，不能依赖 `git status`、分支、提交或 worktree 来恢复上下文；不要为了“接管”而复制项目、重建项目或清空目录。
4. 先阅读本文档，再阅读：

   - `D:\Vibe C\客服\wechat--main\docs\superpowers\plans\2026-08-03-enterprise-kb-final-replies.md`
   - `D:\Vibe C\客服\wechat--main\.workbuddy\memory\2026-08-02.md`
   - `D:\Vibe C\客服\wechat--main\.workbuddy\memory\2026-08-03.md`

5. 不要覆盖上述原日志，不要覆盖用户已有的 `user_data\memory.json` 或 Excel 知识库。后续新增日志或交接说明请使用新的文件名。
6. 当前目标是新的**开发测试版**，不是正式生产版；本阶段不制作压缩包。

## 1. 这个阶段的任务是什么

项目是一个 Windows Python 桌面客服助手“聊小智”，支持微信、企业微信、QQ 的 OCR 识别、知识库匹配、模型生成和全托管自动发送。

本阶段只聚焦企业年报知识库 `enterprise_annual_report_kb` 的最终回复策略，用户已经确认按下面方案执行：

1. “测试命中”和“实际发送”必须使用同一个匹配决策，不能测试页显示第一名、实际发送却再走另一套门槛或模型排序。
2. 去掉旧的“必须 76 分”硬门槛。只有当第一名有足够可靠的匹配信息、明显领先第二名、且不存在时间或操作冲突时，才允许直接发送知识库原始答案。
3. 语义明确但没有出现“年报”二字的问题，也允许进入企业知识库匹配；不可靠、并列或接近的候选不能发送。
4. 企业年报知识库不能命中时，统一只返回：

   `未在当前企业年报知识库中找到对应答案。`

5. 最终发送前必须拦截“这个我得再看一眼”“我再查查”“稍等”等过渡、拖延、承诺但没有实际答案的话术，不能自动发给客户。
6. 加入真实模糊问法回归测试，确保“测试页结果 = 实际发送结果”。

验收样例：

| 客户问法 | 最高/次高候选 | 预期 |
|---|---:|---|
| 想查一下之前的年报 | 58 / 26 | 可直接发送第一名的原始知识库答案 |
| 之前那个报告怎么看 | 74 / 37 | 可直接发送第一名的原始知识库答案；即使没有“年报”字样，也要识别语义范围 |
| 年度工作报告在哪里找 | 53 / 53 | 并列，歧义不发送，只返回企业年报知识库未命中固定文案 |

## 2. 当前代码结构和决策链

请沿着下面的链路理解代码，不要只改 UI 或只改生成器：

```text
企业知识库数据
    ↓
memory.py
    ├─ enterprise_candidates()：候选排序 + 统一 send/ambiguous 决策
    └─ enterprise_scored_examples()：把同一决策传给“测试命中”页面
    ↓
reply_engine.py
    ├─ _select_enterprise_answer()：只接受 memory.py 判定为可直接发送的候选
    └─ 企业范围未命中/有歧义 → 固定企业年报未命中文案
    ↓
main.py
    ├─ 全托管候选选择
    └─ 最终发送前过滤过渡/拖延式话术
    ↓
float_ui.py
    └─ “测试命中”页面显示“可直接发送/歧义不发送”及原因
```

项目主要文件：

- `D:\Vibe C\客服\wechat--main\memory.py`：记忆库读写、企业年报候选匹配和统一决策。
- `D:\Vibe C\客服\wechat--main\reply_engine.py`：企业知识库答案选择、普通模型回复、发送前话术识别所依赖的判断函数。
- `D:\Vibe C\客服\wechat--main\main.py`：OCR 上下文处理、全托管模式、发送前复核和实际发送链路。
- `D:\Vibe C\客服\wechat--main\float_ui.py`：“管理话术 > 测试命中”和设置界面。
- `D:\Vibe C\客服\wechat--main\tests\test_core.py`：核心回归测试，目前共 220 个测试。
- `D:\Vibe C\客服\wechat--main\build_release.py`：打包脚本；它包含清理/打包逻辑，后续测试打包时必须先确认目标目录。

## 3. 当前已经完成的修改

### 3.1 统一企业年报的匹配决策

已修改 `memory.py`：

- 删除旧的 `ENTERPRISE_LOCAL_CONFIDENT_SCORE = 76` 统一放行门槛。
- 当前决策参数为：

  ```python
  ENTERPRISE_LOCAL_CONFIDENT_MARGIN = 8
  ENTERPRISE_LOCAL_MIN_SCORE = 8
  ENTERPRISE_LOCAL_CONFIDENT_FLOOR = 40
  ```

- `ENTERPRISE_LOCAL_CONFIDENT_FLOOR = 40` 只是排除“只有很弱关键词重叠”的候选，不是旧的 76 分硬门槛。
- 第一名满足以下条件时判为 `send`：

  - 最高分不低于可靠性底线 40；
  - 没有时间或操作冲突；
  - 问题是精确命中，或第一名至少领先第二名 8 分。

- 其他情况统一判为 `ambiguous`，不直接发送。
- `_annotate_enterprise_decision()` 会在候选结果上写入：

  - `enterprise_decision`: `send` 或 `ambiguous`
  - `enterprise_decision_label`: `可直接发送` 或 `歧义不发送`
  - `enterprise_decision_reason`: 给测试页和日志使用的原因
  - `enterprise_next_score`
  - `enterprise_score_margin`
  - `enterprise_local_confident`

- `enterprise_scored_examples()` 会将同一个决策字段复制给测试页展示的结果，测试页不再自行推断。
- `_ENTERPRISE_SCOPE_TERMS` 已加入普通的“报告”语义词，所以“之前那个报告怎么看”可以进入企业知识库匹配；并不是只依赖“年报”这个词。

### 3.2 让实际回复只使用共享的本地决策

已修改 `reply_engine.py`：

- `_select_enterprise_answer()` 只接受 `memory.py` 判定为精确或高相关、可直接发送的候选。
- 已删除企业年报场景中另一套模型 selector 逻辑，避免“测试页判定歧义，但运行时交给模型选第一名”的分叉。
- 企业年报知识库存在、问题属于企业知识库范围、但候选不可靠或有歧义时，`generate()` 返回固定文案：

  `未在当前企业年报知识库中找到对应答案。`

- 通过时返回知识库中的原始答案，不改写成模型自由发挥的答案。

### 3.3 拦截过渡和拖延式话术

已修改 `reply_engine.py` 和 `main.py`：

- `_RESULTLESS_PROMISE_MARKERS` 增加了：`我得再看`、`再看一眼`、`我再看`、`先看一下`、`先看下` 等标记。
- `_is_pending_reply()` 会把“这个我得再看一眼”识别为没有实际结果的待完成话术。
- `main.py` 的全托管发送链路会在最终发送前经过 `_filter_sendable_replies()`；待完成候选被过滤，不再把这类话术发给客户。
- 旧的自动补全/诚实兜底补发链已移除，避免把承诺式话术再次自动发出去或造成重复刷屏。

### 3.4 测试页显示明确的发送结论

已修改 `float_ui.py`：

- “测试命中”列表行前缀显示 `[可直接发送]` 或 `[歧义不发送]`。
- 详情摘要显示 `决策：...` 和统一决策原因。
- 测试页显示的是 `memory.py` 的共享决策字段，不是只把第一名当成最终答案。

### 3.5 回归测试已补齐

已修改 `tests\test_core.py`：

- `test_enterprise_decision_accepts_clear_margin_below_legacy_76_gate`
  - 验证低于旧 76 分但第一名明显领先时可以发送。
- `test_enterprise_semantic_scope_without_annual_report_word_returns_official_answer`
  - 验证没有“年报”字样但语义明确的问题可以从企业知识库得到答案。
- `test_enterprise_ambiguous_runtime_and_phrasebook_both_refuse_to_send`
  - 验证并列候选时，实际运行和测试页都拒绝发送，且不调用外部模型 selector。
- `test_managed_send_rejects_long_look_again_transition_candidate`
  - 验证“这个我得再看一眼”不会发送，普通兜底为 `暂时未查到对应答案。`。
- 原有企业 selector 测试已更新为：歧义候选不再调用 selector，也不能绕过共享决策发送。

## 4. 今天具体解决了什么问题

### 4.1 根因：测试命中和实际发送曾经是两套规则

原来测试页能够排出第一名，但实际发送还会经过旧的本地 76 分门槛、企业问题范围判断、模型 selector 或普通兜底逻辑。结果是：测试页看起来“命中”，客户收到的却是“我再看一下”之类的过渡话术，或者根本没有发送。

现在候选排名、明显领先判断、冲突判断和 `send/ambiguous` 结论集中在 `memory.py`，运行时只消费这一个结论，测试页也展示这一个结论。

### 4.2 真实模糊问题已按预期处理

使用现有 90 条企业年报知识库做过只读验证：

- `想查一下之前的年报`：第一名 58，第二名 26，测试页显示 `可直接发送`，运行时发送第一名原始答案。
- `之前那个报告怎么看`：第一名 74，第二名 37，虽然没有“年报”二字，测试页显示 `可直接发送`，运行时发送第一名原始答案。
- `年度工作报告在哪里找`：第一名 53，第二名 53，测试页显示 `歧义不发送`，运行时只返回固定未命中文案。

验证输出中，前两条都命中 `Sheet1!C11:D11`；第三条没有发送知识库答案，也没有调用外部 selector。

### 4.3 解决“过渡话术自动发给客户”问题

“这个我得再看一眼”现在被最终发送过滤器识别为 pending/无结果承诺，不会进入发送器。这样即使模型或候选刷新产生该文本，也不能直接发给客户。

### 4.4 已生成新的开发测试版

已用独立目录生成新的开发测试版，旧目录没有覆盖：

`D:\Vibe C\客服\wechat--main\dist\聊小智_开发测试_20260803_v2\聊小智\聊小智.exe`

本阶段没有制作新的 ZIP。新包自检报告为：

`D:\Vibe C\客服\wechat--main\dist\聊小智_开发测试_20260803_v2\聊小智\自检报告.txt`

自检报告包含依赖、OCR 模型、OCR 引擎初始化、截图权限、设置目录可写、企业微信窗口检测等 14 项 `[通过]`；API Key 为空是有意的开发测试状态，不代表打包失败。新包的 `user_data` 没有预置那 90 条知识库，需要通过程序导入/训练自己的知识库。

## 5. 当前问题、剩余风险和边界

本阶段的核心 bug 已修复，当前没有已知的代码测试阻塞；但下面事项仍需要接替 Agent 或用户做进一步验证：

1. **还没有在用户当前真实企业微信会话里完成一次新的端到端客户发送验证。** 当前已经完成源代码单元测试、当前代码对 90 条知识库的真实问法验证、打包自检和企业微信窗口检测；但“真实客户发问 → OCR → 统一决策 → 最终发送”仍应在用户授权和安全条件下人工验收。
2. **新 v2 开发包没有预置企业年报知识库和 API Key。** 这是为了避免把旧用户数据带入新包，不是功能缺失。实际测试前请导入测试知识库并配置接口。
3. **PyInstaller 曾出现 `onnxruntime.quantization` 收集 `onnx` 的 warning。** 打包完成且自检通过，当前不影响已验证的 OCR 运行；如果后续重新打包或 OCR 行为异常，要优先检查该 warning 和自检报告。
4. **当前 40 分底线、8 分差距是基于现有真实样例的工程规则，不是数学上永久正确的阈值。** 以后如需调整，必须先增加/修改回归测试，再改代码并复跑全套测试，不能凭一条新问法直接放宽为“永远发送最高分”。
5. **当前主要做了代码和自动化验证，未完成完整的 GUI 视觉验收。** 如修改列表样式或弹窗布局，需在 Windows 上打开“管理话术 > 测试命中”人工确认中文显示、颜色和换行。
6. 项目本身没有 Git 历史可供回滚；工作区中的既有改动都视为用户项目的一部分，修改时应做局部补丁，不要用重置或大范围替换。

## 6. 后续计划（按顺序执行）

### 第一步：接管后先做静态确认

在原目录执行：

```powershell
Set-Location 'D:\Vibe C\客服\wechat--main'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_core.py'
.venv\Scripts\python.exe -m compileall -q -f . -x '^(build|dist|\.venv|__pycache__)([\\/]|$)'
```

预期：测试 `Ran 220 tests` 且 `OK`；编译命令无输出并成功结束。若不是这个结果，先定位回归，不要继续改阈值或打包。

### 第二步：复核企业年报三条核心问法

用当前源代码和一个副本/测试数据复核：

1. `想查一下之前的年报` → `可直接发送`。
2. `之前那个报告怎么看` → `可直接发送`。
3. `年度工作报告在哪里找` → `歧义不发送`，只返回固定未命中文案。

同时确认测试页的 label 与运行时 `generate()` 的结果一致，尤其不要只看排名第一行的答案文本。

### 第三步：在安全条件下做真实企业微信 E2E

需要用户明确允许后，再用企业微信测试对话执行三条问法。每条都核对：

- OCR 是否拿到完整最新消息；
- 企业知识库范围是否正确；
- 测试命中标签与实际发送是否一致；
- 并列问法是否没有发出错误答案；
- `managed_send.log` 是否记录了正确的拦截/发送分支；
- 最终发送窗口是否仍然是企业微信，而不是文件资源管理器或其他窗口。

不要一开始就打开全托管对真实客户发送；先用测试联系人、短问题、可观察的日志完成验证。

### 第四步：任何规则调整都采用测试优先

如果发现新的真实模糊问法：

1. 先在 `tests\test_core.py` 写一个会失败的回归测试；
2. 明确它是 `send` 还是 `ambiguous`，并写明测试页和运行时的共同预期；
3. 再修改 `memory.py` 的统一决策或范围词；
4. 复跑 220+ 全量测试、compileall 和真实知识库烟测。

### 第五步：需要新开发包时再独立打包

如果用户要求新的开发测试版，使用新的版本化 `distpath`/`workpath`，不要覆盖旧包。每次打包后必须：

- 检查 EXE 和 `自检报告.txt`；
- 检查报告中的 `[通过]` 与失败项；
- 运行 `--self-check` 或等价自检；
- 明确告知是否制作 ZIP；本项目当前要求不制作 ZIP。

## 7. 绝对不要再踩的坑

### 匹配与发送规则

- **绝对不要恢复“必须 76 分才能发送”。** 当前规则是可靠性底线 + 明显领先 + 无冲突；不要把 76 又塞回运行时。
- **绝对不要无条件发送最高分。** 53/53 这类并列必须拒答；接近候选也不能交给另一个模型“再选一次”。
- **绝对不要让测试页和运行时各写一套排序/门槛。** 统一改 `memory.py` 的 `enterprise_decision`，然后让两边消费它。
- **绝对不要在企业知识库有范围但候选不可靠时回退到普通模型自由回答。** 必须返回 `未在当前企业年报知识库中找到对应答案。`。
- **绝对不要把“没有年报字样”直接当成非企业问题。** 先看语义范围；“之前那个报告怎么看”是本阶段必须保留的正例。
- **绝对不要把“这个我得再看一眼”等承诺式句子交给发送器。** 最终发送前必须经过 pending/transition 过滤。

### 项目、环境和数据

- **绝对不要用 `git reset --hard`、`git checkout --` 或大范围删除恢复项目。** 当前不是 Git 仓库，且目录中的既有修改属于用户。
- **绝对不要覆盖用户的知识库、设置或旧开发包。** 90 条样例知识库只用于只读验证；不要把它误当成新包的默认数据。
- **绝对不要把项目复制到另一个目录后继续改。** 新 Agent 必须回到：

  `D:\Vibe C\客服\wechat--main`

- 项目实际运行环境是 `D:\Vibe C\客服\wechat--main\.venv` 的 Python 3.11.9。不要把依赖只装进 WorkBuddy 的另一套 Python 3.13 环境后就认为项目已修复。
- 如果再次遇到 `SAFE_DELETE_FAIL_CLOSED` 或 OpenCV circular import，先检查并清理 site-packages 中冲突的旧 opencv 包，再用 `--no-cache-dir` 安装；不要盲目重复 pip 安装。
- Windows PowerShell 向 Python 传中文路径时，曾出现中文被传成 `??` 的问题。需要跨进程传路径的脚本优先使用 ASCII 路径、Unicode 转义或直接在 PowerShell 中操作，不要用未经确认编码的 here-string。

### 打包和验证

- **绝对不要直接运行会清理默认 `dist` 并创建 ZIP 的完整发布流程来做小改动。** `build_release.py` 含清理/打包逻辑；本阶段应使用独立的 `--distpath`/`--workpath`，保护旧包。
- **绝对不要因为 PyInstaller 命令成功就宣布功能验证完成。** 必须读取 `自检报告.txt`，并至少完成测试、compileall 和核心知识库烟测。
- **绝对不要一开始用真实客户全托管验证。** 先跑单元测试，再用测试联系人和日志观察，最后才做真实发送。
- 如果看到“候选列表一直刷新但不发送”，先读 `%APPDATA%\WeChatStyleReplyAssistant\managed_send.log` 末尾，检查 `_managed_preflight_context`、窗口捕获和发送前复核分支，不要先改匹配分数。
- 如果看到企业微信被识别成微信或文件资源管理器，先使用 `tools_diag\` 下诊断脚本检查进程、窗口标题、类名和窗口边界，不要凭肉眼随意改比例。

## 8. 已验证的命令和结果

### 全量核心测试

```powershell
Set-Location 'D:\Vibe C\客服\wechat--main'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_core.py'
```

结果：

```text
Ran 220 tests in 3.224s
OK
```

### 全项目编译

```powershell
.venv\Scripts\python.exe -m compileall -q -f . -x '^(build|dist|\.venv|__pycache__)([\\/]|$)'
```

结果：`COMPILE_OK`，无编译错误。

### 90 条企业年报知识库烟测

使用旧开发包中的 90 条知识库文件做了只读验证，文件为：

`D:\Vibe C\客服\wechat--main\dist\聊小智_开发测试_20260803\聊小智\user_data\memory.json`

这不是新 v2 包的默认数据，也不要修改该文件。结果：

```text
想查一下之前的年报       UI=可直接发送   RUNTIME=发送 C11:D11 原始答案
之前那个报告怎么看       UI=可直接发送   RUNTIME=发送 C11:D11 原始答案
年度工作报告在哪里找     UI=歧义不发送   RUNTIME=未在当前企业年报知识库中找到对应答案。
```

### 新开发包

```text
D:\Vibe C\客服\wechat--main\dist\聊小智_开发测试_20260803_v2\聊小智\聊小智.exe
D:\Vibe C\客服\wechat--main\dist\聊小智_开发测试_20260803_v2\聊小智\自检报告.txt
```

自检报告显示 14 项 `[通过]`，包括 OCR 模型/引擎、截图、设置目录可写和企业微信窗口检测；API Key 显示未填写，符合开发测试包状态。没有生成 `聊小智_开发测试_20260803_v2.zip`。

## 9. 重要文件索引

- 统一候选决策：`D:\Vibe C\客服\wechat--main\memory.py`，重点看 `enterprise_candidates()`、`enterprise_scored_examples()`、`_annotate_enterprise_decision()`。
- 企业答案选择：`D:\Vibe C\客服\wechat--main\reply_engine.py`，重点看 `generate()`、`_select_enterprise_answer()` 和企业未命中分支。
- 过渡话术拦截：`D:\Vibe C\客服\wechat--main\reply_engine.py` 的 `_RESULTLESS_PROMISE_MARKERS`、`_is_pending_reply()`；`D:\Vibe C\客服\wechat--main\main.py` 的 `_filter_sendable_replies()` 和 `_maybe_managed_send()`。
- 测试页：`D:\Vibe C\客服\wechat--main\float_ui.py` 的 phrasebook manager `display_text()`、`populate_item()`。
- 回归测试：`D:\Vibe C\客服\wechat--main\tests\test_core.py` 中企业年报测试和 managed send 测试。
- 当前阶段计划/设计：`D:\Vibe C\客服\wechat--main\docs\superpowers\plans\2026-08-03-enterprise-kb-final-replies.md`。
- 历史环境、企业微信 OCR、窗口识别和托管链路修复：`D:\Vibe C\客服\wechat--main\.workbuddy\memory\2026-08-02.md`、`2026-08-03.md`。
- 诊断脚本：`D:\Vibe C\客服\wechat--main\tools_diag\`。

## 10. 接替后的完成检查清单

- [ ] 当前工作目录确认是 `D:\Vibe C\客服\wechat--main`。
- [ ] 已阅读本交接文档、阶段计划和两个历史工作日志。
- [ ] 已运行 220 个核心测试并确认 `OK`。
- [ ] 已运行 compileall。
- [ ] 已复核 58/26、74/37、53/53 三个真实样例。
- [ ] 已确认测试页 label 与运行时发送决策来自同一字段。
- [ ] 已确认企业范围未命中只返回固定未命中文案。
- [ ] 已确认“这个我得再看一眼”等过渡话术不会进入发送器。
- [ ] 若改代码，已先写回归测试并复跑全量测试。
- [ ] 若重新打包，已使用新的版本化输出目录、读取自检报告，且没有覆盖旧包。
- [ ] 在用户授权后才进行企业微信真实发送验证。

**交接结论：后续 Agent 必须在 `D:\Vibe C\客服\wechat--main` 原有代码基础上继续修改，不要另建项目副本，不要恢复旧的 76 分门槛，不要把企业年报歧义交给另一套模型排序，也不要制作本阶段未要求的压缩包。**
