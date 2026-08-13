# Task 1 Report — Immutable Knowledge Records and Stable Hashes

Date: 2026-08-12

Review state: implementation evidence recorded; no review PASS is claimed. Independent review is still required.

## Scope and constraints observed

- Created only `knowledge_base.py`, `tests/test_knowledge_contract.py`, and the required SHA-256 checkpoint/report artifacts.
- Did not initialize Git or make a commit.
- Did not start Enterprise WeChat, sender code, network access, model access, migration, or formal writes.
- Every test command set an explicit temporary `WECHAT_ASSISTANT_DATA_DIR`; the existing session/per-test fixtures also isolated each test.

## Formal AppData read-only comparison

Formal root: `C:\Users\14913\AppData\Roaming\WeChatStyleReplyAssistant`

The manifest digest below hashes each relative path, byte length, UTC mtime ticks, and file SHA-256 in sorted path order.

| Observation | Files | Directories | Bytes | Manifest SHA-256 |
| --- | ---: | ---: | ---: | --- |
| Before implementation | 32 | 1 | 346786 | `26e717e0cfa6a10d7413aa1a92bd09ad124ba53fb1819de10b03c3758a30ae65` |
| After all tests | 32 | 1 | 346786 | `26e717e0cfa6a10d7413aa1a92bd09ad124ba53fb1819de10b03c3758a30ae65` |

Result: the before/after read-only manifests are identical.

## TDD evidence

### RED

Command:

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = (Join-Path $env:TEMP 'wechat-assistant-phase2-task1-red')
.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_contract.py -k 'record_id or unknown_status' -v
```

Result: exit code 1; collection stopped with one expected error and zero tests executed. The failure was `ModuleNotFoundError: No module named 'knowledge_base'`, confirming the planned module/schema did not exist before production code was added.

### GREEN — focused plan selection

Command:

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = (Join-Path $env:TEMP 'wechat-assistant-phase2-task1-green')
.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_contract.py -k 'record or hash or scope' -v
```

Result: exit code 0; 21 passed, 41 deselected.

### GREEN — complete Task 1 contract

Command:

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = (Join-Path $env:TEMP 'wechat-assistant-phase2-task1-green-full')
.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_contract.py -v
```

Result: exit code 0; 62 passed.

The tests cover immutable records and keyword tuples, deterministic IDs/hashes, NFKC and whitespace normalization boundaries, exact original question/reply preservation, meaningful-answer separation, source-locator identity, typed invalid field/keyword/scope/status failures, explicit scope precedence, explicit organization labels, ambiguous mixed-label quarantine, and unknown-to-pending defaulting.

## Regression and compile evidence

Related full-file regression:

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = (Join-Path $env:TEMP 'wechat-assistant-phase2-task1-regression')
.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_contract.py tests\test_persistence.py tests\test_core.py -q
```

Result: exit code 0; 448 passed and 6 subtests passed.

Full test suite:

```powershell
$env:WECHAT_ASSISTANT_DATA_DIR = (Join-Path $env:TEMP 'wechat-assistant-phase2-task1-full-suite')
.\.venv\Scripts\python.exe -m pytest tests -q
```

Result: exit code 0; 606 passed and 6 subtests passed. There were 74 existing Pillow `Image.Image.getdata` deprecation warnings in `conversation_list.py`; no test failed.

Compile check:

```powershell
.\.venv\Scripts\python.exe -m py_compile knowledge_base.py tests\test_knowledge_contract.py
```

Result: exit code 0 with no output.

## Implementation contract

- `KnowledgeRecord` is a frozen dataclass with the ten specified fields.
- `OrgScope` and `RecordStatus` expose only their specified literal values.
- Questions and replies are stored unchanged. NFKC plus whitespace collapsing is used only for identity/comparison/hash inputs.
- `answer_hash` hashes only the normalized answer.
- `record_id` hashes normalized question, resolved scope, normalized answer, and the original source locator.
- `source_hash` hashes the stable pair of source file and source locator.
- Explicit valid `org_scope` takes precedence over organization labels. Invalid explicit scope or status raises `KnowledgeSchemaError`.
- Explicit common/both labels resolve to `both`; strings containing conflicting social and MFEI markers without an explicit both construction resolve to `unknown`, whose default status is `pending`.
- Missing source metadata is represented as an empty string for compatibility with the plan's minimal input example; supplied non-string metadata is rejected.

## Checkpoint

- `knowledge_base.py`: `c13bc91267fd8c14f5742db1a202c396fc86ca2b5f7b05038a11f0aaf8c4da15`
- `tests/test_knowledge_contract.py`: `b244594ababe1034e22e5f110486ab7411182d63d587905896226c8ffe2debb7`
- Checkpoint file: `artifacts/formal-hardening/2026-08-12/05-knowledge-schema.sha256.json`

## Review considerations

- The plan fixes the fields required in `source_hash` but does not prescribe its exact serialization. This implementation uses a deterministic JSON tuple of `source_file` and `source_locator`; independent review should confirm that payload before later migration work depends on it.
- Organization-label handling deliberately recognizes only explicit both forms and sends ambiguous mixed-marker labels to `unknown`; Task 2 can build conflict auditing on this contract without changing the Task 1 boundary.
