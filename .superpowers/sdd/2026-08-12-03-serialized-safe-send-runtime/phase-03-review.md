# Phase 3 串行安全发送运行时审查报告

日期：2026-08-14  
分支：`codex/phase-3-serialized-safe-send`  
结论：PASS（进入 Phase 4 前的本地里程碑；尚未 push）

## 交付范围

- 新增稳定聊天身份与观察合同：窗口句柄、顶部标题、聊天类型、群聊发送者及客户轮次共同构成不可混淆的发送身份。
- 新增单一、非 daemon 的托管发送工作线程；个人和群聊都提交到同一有界队列，由同一个协调器串行执行。
- 托管状态序列统一为 `waiting -> stable -> matching -> judging -> preflight -> sending -> verifying -> recorded/blocked`。
- 自动发送只能使用 `ReplyDecision` 中仍然有效的知识库原文或精确固定答复；匹配、裁决和发送前复核全部在串行工作线程内完成。
- 自动发送不允许抢焦点；只接受当前前台、同一非零 HWND、同一平台、同一标题、同一聊天类型、同一发送者及同一客户轮次的实时观察。
- 发送后重新 OCR 核对己方气泡。只有识别到精确新增己方气泡才记录 `verified`；明确未发送最多完整重试一次；不确定状态永久抑制自动重发并提示人工核对。
- 发送账本严格校验时间、状态和哈希，启动时建立当前可见轮次基线，90 天后只清理无进行中发送的终态记录。
- 群聊 `@` 必须完整命中账号名；系统会话只使用精确白名单，不再用“服务/管理/联系”等宽泛子串分类。
- 退出时停止接受新任务并排空已接受任务；队列已满时停止调用仍受调用方超时约束，不再在放入停止标记前无限阻塞。

## 严格自审结果

- 自动发送器调用点仅剩两个：人工发送显式 `allow_activate=True`；托管协调器显式 `allow_activate=False`。
- `decide_managed()` 的生产调用点只位于串行工作线程适配器内。
- 删除了旧群聊独立发送线程、发送前提前标记 handled、缓存 OCR 预检兜底、托管屏幕截图兜底、托管语音点击转写及不再收集的旧接口测试。
- 删除的旧预检函数曾允许屏幕截图兜底；它虽然已无生产调用方，但保留会形成未来误复用风险，因此本阶段直接移除。
- 自审新发现并修复队列满时 `shutdown(timeout)` 失效的问题。失败测试先证明停止线程超过限定时间仍阻塞，修复后停止请求非阻塞、工作线程按序排空并退出。

## 验证证据

- Phase 3 聚焦回归：`166 passed, 418 deselected, 2 subtests passed`。
- 退出边界回归：`4 passed, 48 deselected`。
- 最终全量：`805 passed, 9 subtests passed, 0 failed`。
- JUnit：`814 tests / 0 failures / 0 errors / 0 skipped`，SHA-256 `562f5fc835a8b1bf9f207c229d41b5bef654100fa5a3aa28272fdcc718ab26f4`。
- `python -m compileall -q .`：exit 0。
- `git diff --check`：无空白错误；仅显示现有 Windows CRLF 转换提示。
- 正式 AppData 与 Phase 2 权威基线逐文件完全一致：`32 -> 32`，`exact_match=true`。当前清单 SHA-256 `e57b7e24a2413d68aec0368f46275bcda545c47ebbd7bdb522d26a09dd3de140`。

证据文件：

- `artifacts/formal-hardening/2026-08-14/phase-03-tests.xml`
- `artifacts/formal-hardening/2026-08-14/phase-03-formal-appdata-current.json`
- `artifacts/formal-hardening/2026-08-14/phase-03-formal-appdata-comparison.json`

## 已知非阻断项

- 全量测试仍有 74 条 Pillow `Image.getdata()` 弃用告警，均来自既有 `conversation_list.py` 两处像素读取，计划在 Phase 4 发布整理中消除并重新做最终全量验证。
- 本阶段只证明自动发送运行时的代码、故障注入和模拟集成安全；真实企业微信观察、受控发送、正式迁移和 EXE 验证属于 Phase 4，尚未执行。
- 按用户要求，Phase 3 仅保留本地提交，不 push；所有任务完成后统一同步远端。
