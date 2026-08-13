# Task 2: Atomic JSON and Five-Version Recovery

Status: complete; 提交=N/A（项目非 Git，未初始化或提交）。
实现: 新增 `atomic_json.py`，提供验证后临时文件 fsync/replace、至多五份轮转备份，以及 primary→bak.1…bak.5 的验证恢复。
错误语义: 所有候选均不可读或验证失败时抛出仅含候选路径与原因的 `JsonDataError`，从不创建空 primary。
RED 命令: `$env:WECHAT_ASSISTANT_DATA_DIR=<临时目录>; .\\.venv\\Scripts\\python.exe -m pytest tests\\test_persistence.py -k "backup or validation" -v`
RED 实际输出: `collected 0 items / 1 error`；预期 `ModuleNotFoundError: No module named 'atomic_json'`。
GREEN 命令: `$env:WECHAT_ASSISTANT_DATA_DIR=<临时目录>; .\\.venv\\Scripts\\python.exe -m pytest tests\\test_persistence.py -k "json or backup or atomic" -v`
GREEN 实际输出: `5 passed, 2 deselected in 0.16s`；完整文件回归为 `7 passed in 0.15s`。
变更文件: `atomic_json.py`、`tests/test_persistence.py`；记录文件: 本报告及 checkpoint。
自检: 覆盖新近有效备份、五份轮转、验证失败原文件不变、替换失败 primary 可读、全部无效不建空文件。
Checkpoint: `artifacts/formal-hardening/2026-08-12/02-atomic-json.sha256.json`。
哈希: atomic_json.py=`584CA252CD11601BDA23D025C006BF7111D788F210C6D40464282B816093A7A5`; tests/test_persistence.py=`1CB8C165A8C7863D5E2CC21845AAB9FBCD2C23041851F71C62562FD43C457627`。
顾虑: `_rotate_backups` 仅把能通过当前 validator 的 primary 备份；既有损坏备份会在后续 load 时被跳过，不会阻塞恢复。

## Fix round 1 (review remediation)
新增测试: `test_atomic_write_retains_older_valid_backup_when_newer_slots_are_corrupt`，覆盖损坏 .bak.1/.3/.4 与较旧有效 .bak.5。
RED 命令: `$env:WECHAT_ASSISTANT_DATA_DIR=<临时目录>; .\\.venv\\Scripts\\python.exe -m pytest tests\\test_persistence.py -k "retains_older_valid" -v`
RED 实际失败: `JSONDecodeError` 读取轮转后仍为 `{broken` 的备份槽，`1 failed, 7 deselected in 0.34s`。
GREEN 命令: 同上；实际输出 `1 passed, 7 deselected in 0.13s`。完整回归：`8 passed in 0.22s`。
修复: 轮转前验证 primary 与全部备份候选；仅将按新旧顺序收集的有效快照写入 .bak.1 起的连续槽位，并删除剩余无效槽位。
当前 checkpoint: `artifacts/formal-hardening/2026-08-12/02-atomic-json.sha256.json`；atomic=`7AA66D59F228BCFE404C471A3F700AA06B53889068A944B686059C8B70874E1B`，tests=`67ED22B7DB3C2EAE16506673ACEA4EDAA0DE78350B266733FE078337627836F6`。
