# Task 1 Report: Centralize AppData Paths and Test Isolation

## Status

DONE. This is a non-Git workspace; no repository initialization or commit was performed.

## Implementation

- Added `app_paths.py` as the single resolver for the application data root and config, memory, vector, credential, ledger, and log paths.
- `WECHAT_ASSISTANT_DATA_DIR` is an explicit override. Without it, both source and frozen runs use `%APPDATA%\WeChatStyleReplyAssistant`; the existing home-directory fallback remains for an unset `APPDATA`.
- Changed `settings.default_config_path()` and `memory.default_memory_path()` to delegate to `app_paths`.
- Added focused persistence tests for explicit isolated roots and frozen-mode AppData behavior.
- With explicit user authorization, updated the one conflicting existing test in `tests/test_core.py`: it now checks that frozen execution also resolves to the AppData root instead of EXE-adjacent `user_data`.

## TDD Evidence

### RED

Command (with isolated temporary root):

```powershell
$taskDataRoot = Join-Path $env:TEMP 'wechat-assistant-task1-red-isolated'; $env:WECHAT_ASSISTANT_DATA_DIR = $taskDataRoot; .\.venv\Scripts\python.exe -m pytest tests\test_persistence.py -k "data_root" -v
```

Result: expected failure during collection: `ModuleNotFoundError: No module named 'app_paths'`. This demonstrated the missing resolver before production code was added. (The `-k data_root` selection still imports the new module before selecting tests.)

### GREEN

Focused frozen-path regression command (with isolated temporary root):

```powershell
$taskDataRoot = Join-Path $env:TEMP 'wechat-assistant-task1-regression-isolated'; $env:WECHAT_ASSISTANT_DATA_DIR = $taskDataRoot; .\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "frozen" -v
```

Output: `3 passed, 248 deselected in 1.13s`.

Required focused/existing-path command (with isolated temporary root):

```powershell
$taskDataRoot = Join-Path $env:TEMP 'wechat-assistant-task1-green-isolated'; $env:WECHAT_ASSISTANT_DATA_DIR = $taskDataRoot; .\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_core.py -k "path or frozen or config" -v
```

Output: `7 passed, 244 deselected in 0.97s`.

## Changed Files

- `app_paths.py` (new)
- `settings.py`
- `memory.py`
- `tests/test_persistence.py` (new)
- `tests/test_core.py` (one explicitly authorized conflicting test and its `os` import)
- `artifacts/formal-hardening/2026-08-12/01-paths.sha256.json` (new checkpoint)

## Checkpoint

SHA-256 checkpoint generated with the required command after creating the previously absent `artifacts/formal-hardening/2026-08-12` directory:

```powershell
Get-FileHash app_paths.py,settings.py,memory.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\01-paths.sha256.json -Encoding UTF8
```

## Self-Check

- All resolver functions return direct children of `data_dir()`.
- Tests use `WECHAT_ASSISTANT_DATA_DIR` set to a temporary directory; no test command uses the real AppData directory.
- Frozen execution no longer branches to EXE-adjacent `user_data`.
- No later persistence, credential, ledger, logging, or release-hardening tasks were implemented.

## Remaining Risks

- This task intentionally only centralizes current default settings and memory paths. Existing callers that construct custom paths remain unchanged by design.
- The AppData fallback is retained for environments where `APPDATA` is unset; Windows source and frozen runs use the mandated AppData location when it is available.

## Fix Round 1

**Covered file:** `artifacts/formal-hardening/2026-08-12/01-paths.sha256.json`.

The required non-Git checkpoint was regenerated and read back. No production code or tests were changed in this fix round.

Validation command:

```powershell
Get-FileHash app_paths.py,settings.py,memory.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\01-paths.sha256.json -Encoding UTF8; Get-Content -LiteralPath artifacts\formal-hardening\2026-08-12\01-paths.sha256.json
```

Actual output:

```text
app_paths.py  F8F5FF09720FACC8B5384D302AE1F6A9083042A573CEA5ECBDE2060D1A530440
settings.py   A984D012D77E05433FD900C3174BB094EEC60633FFA1DE3EFC056355C5E13830
memory.py     1A03CE8B389A97A8F850207B01E9508231C71C62697113EFEB7518FA690E7822
```
