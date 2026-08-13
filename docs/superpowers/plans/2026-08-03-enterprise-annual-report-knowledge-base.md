# 企业年报知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持自动识别复杂企业年报 Excel、保留来源元数据，并以大模型重排实现高置信官方答案命中。

**Architecture:** 使用标准库 XML 解析复杂 XLSX，避免新运行时依赖。解析器输出统一样例及来源元数据；内存层提供企业库候选；回复引擎以候选重排和双阈值保护直接返回原始答案。

**Tech Stack:** Python 3.11、`zipfile`、`xml.etree.ElementTree`、现有 `StyleMemory`、现有文本生成 API。

## Global Constraints

- 不绑定文件名、绝对路径、用户名或机器环境。
- 不修改用户原始 Excel。
- 企业知识库答案命中后原样返回，不做截断或模型改写。
- 不新增第三方依赖；打包时必须包含新模块。
- 失败或空导入不得清空已有企业库。

---

### Task 1: 复杂企业年报 XLSX 识别与解析

**Files:**
- Create: `enterprise_kb.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `detect_enterprise_annual_report_workbook(name: str, raw: bytes) -> bool`
- Produces: `parse_enterprise_annual_report_workbook(name: str, raw: bytes, source: str = "enterprise_annual_report_kb") -> list[dict[str, str]]`

- [ ] **Step 1: Write failing tests**

```python
def test_complex_annual_report_xlsx_preserves_merged_metadata_and_two_org_columns(self):
    examples = parse_enterprise_annual_report_workbook("annual.xlsx", complex_xlsx_bytes())
    self.assertEqual(len(examples), 3)
    self.assertEqual(examples[0]["module"], "年检")
    self.assertEqual(examples[0]["organization_type"], "社会团体")
    self.assertEqual(examples[-1]["organization_type"], "民办非企业单位")
    self.assertEqual(examples[-1]["source_cell"], "D3")
```

- [ ] **Step 2: Run the targeted test and verify it fails because the module/function does not exist.**

Run: `.venv\\Scripts\\python.exe -m unittest tests.test_core.ChatTrainingTests.test_complex_annual_report_xlsx_preserves_merged_metadata_and_two_org_columns`

- [ ] **Step 3: Implement minimal XLSX XML reader and parser.**

```python
def parse_enterprise_annual_report_workbook(name: str, raw: bytes, source: str = ENTERPRISE_SOURCE) -> list[dict[str, str]]:
    workbook = _read_xlsx_workbook(raw)
    if not _is_enterprise_annual_report(workbook):
        return []
    return _extract_question_answer_examples(workbook, name, source)
```

- [ ] **Step 4: Run targeted parsing tests and verify they pass.**

- [ ] **Step 5: Commit.**

Skip commit because this project has no Git repository; preserve the pre-change ZIP backup instead.

### Task 2: 导入集成、来源隔离与原子替换

**Files:**
- Modify: `chat_training.py`
- Modify: `memory.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: enterprise examples from `_parse_phrasebook_file` before generic CSV flattening.
- Produces: metadata persistence for `module`, `subcategory`, `organization_type`, `source_sheet`, `source_cell`, `source_file`.

- [ ] **Step 1: Write failing tests.**

```python
def test_enterprise_import_uses_dedicated_source_and_does_not_replace_generic_training(self):
    # Import one generic example, then one enterprise workbook.
    # Assert both sources remain, enterprise source has extracted examples.

def test_empty_enterprise_import_keeps_existing_enterprise_library(self):
    # Seed enterprise source, import malformed workbook, assert seed survives.
```

- [ ] **Step 2: Run tests and verify expected failures.**

- [ ] **Step 3: Add early enterprise parser routing and only replace a source after at least one valid example exists.**

```python
enterprise_examples = parse_enterprise_annual_report_workbook(name, raw)
if enterprise_examples:
    return enterprise_examples
```

- [ ] **Step 4: Persist approved source metadata in `StyleMemory`.**

- [ ] **Step 5: Run targeted import tests and verify they pass.**

### Task 3: 企业知识库候选召回与模型重排

**Files:**
- Modify: `memory.py`
- Modify: `reply_engine.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `StyleMemory.enterprise_candidates(conversation_text: str, limit: int = 6) -> list[dict[str, str]]`
- Produces: `ReplyEngine._select_enterprise_answer(conversation_text: str) -> dict[str, str] | None`

- [ ] **Step 1: Write failing tests.**

```python
def test_enterprise_candidates_include_semantically_relevant_question(self):
    # Similar wording must recall the source question and preserve its source metadata.

def test_model_reorder_returns_verbatim_enterprise_answer_when_confident(self):
    # Mock selector JSON with index/confidence and assert the returned reply equals source text.

def test_low_confidence_selector_does_not_return_enterprise_answer(self):
    # Mock confidence below threshold and assert ordinary generation path remains available.
```

- [ ] **Step 2: Run tests and verify expected failures.**

- [ ] **Step 3: Implement candidate filtering and strict JSON selector parsing.**

```python
payload = {"index": index, "confidence": confidence}
if confidence >= ENTERPRISE_MIN_CONFIDENCE and local_score >= ENTERPRISE_MIN_LOCAL_SCORE:
    return candidate
return None
```

- [ ] **Step 4: Route high-confidence enterprise matches before generic model generation.**

- [ ] **Step 5: Run targeted retrieval tests and verify they pass.**

### Task 4: 长答案显示、状态说明与发布内容

**Files:**
- Modify: `reply_engine.py`
- Modify: `memory.py`
- Modify: `float_ui.py`
- Modify: `README.md`
- Modify: `build_release.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: enterprise answer as one uncapped reply and a source label containing library/category/source cell.
- Produces: packaging verification that imports `enterprise_kb`.

- [ ] **Step 1: Write failing tests.**

```python
def test_enterprise_answer_bypasses_general_short_reply_prompt_rule(self):
    # A 100+ character official answer must return unchanged.

def test_release_requires_enterprise_knowledge_base_module(self):
    # Packaging import list must include the parser module.
```

- [ ] **Step 2: Run tests and verify expected failures.**

- [ ] **Step 3: Set enterprise source label and retain full answer text; leave ordinary model prompt length rules unchanged.**

- [ ] **Step 4: Update user-facing documentation to explain automatic detection, source traceability and fallback.**

- [ ] **Step 5: Run all tests and validate actual provided workbook parses 90 entries.**

### Task 5: Final verification and deliverable package

**Files:**
- Modify only if verification exposes a defect.

- [ ] **Step 1: Run full regression suite.**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p 'test_*.py'`

- [ ] **Step 2: Run real-workbook import into a temporary memory file.**

Expected: one file, 90 examples, 74 social-organization examples, 16 private-non-enterprise examples.

- [ ] **Step 3: Run the release builder and validate the generated package contains the updated program and dependency check passes.**

- [ ] **Step 4: Record test output, package location, and any intentional limitations in the handoff.**
