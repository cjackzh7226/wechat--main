# Enterprise Knowledge Base Final Replies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make enterprise annual-report fuzzy retrieval consistent between the test UI and runtime, and ensure managed replies are final answers only.

**Architecture:** `StyleMemory` owns the shared enterprise ranking and display rows. `ReplyEngine` accepts exact or high-confidence local candidates and emits explicit no-answer text for in-scope misses. `main.py` prevents pending candidates from reaching the sender and removes the delayed completion chain.

**Tech Stack:** Python 3.11, unittest, existing local sparse-token retrieval.

## Global Constraints

- Do not modify imported Excel or user-data files.
- Do not require an API Key for high-confidence local enterprise matches.
- Do not create a portable package or ZIP during development.
- Add regression tests before production changes.

---

### Task 1: Lock the current failures in tests

**Files:**
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: `StyleMemory.enterprise_candidates`, `ReplyEngine.generate`, `WeChatStyleReplyAssistant._get_phrasebook_examples`, `_select_managed_reply`
- Produces: failing regression tests for fuzzy historical lookup, test/runtime parity, explicit miss, and no pending auto-send selection.

- [ ] **Step 1: Write failing tests**

```python
query = "怎么查以前的年度报告"
assert memory.enterprise_candidates(query)[0]["source_cell"] == "C11:D11"
assert engine.generate("[对方] " + query) == [expected_c11_reply]
assert ui_rows[0]["source_cell"] == "C11:D11"
assert _select_managed_reply("[对方] 怎么查", ["可以，我确认下"]) == "暂时未查到对应答案。"
```

- [ ] **Step 2: Run the focused tests and verify they fail for the existing divergent ranking/pending behavior.**

- [ ] **Step 3: Commit**

No git repository is present in this workspace; retain the changed test file for the next task.

### Task 2: Centralize enterprise ranking and local confidence

**Files:**
- Modify: `memory.py`
- Modify: `main.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `StyleMemory.enterprise_scored_examples(query, limit)` and enriched `enterprise_candidates` rows with local score, reasons, conflict count, and confidence marker.
- Consumes: `main._get_phrasebook_examples` with the enterprise source key.

- [ ] **Step 1: Add semantic intent groups for historical/current time, view/fill/modify/print/login actions, and annual-report topics.**
- [ ] **Step 2: Incorporate matching tags and conflict penalties into `enterprise_candidates`, then annotate the leading candidate only when score and separation are reliable.**
- [ ] **Step 3: Implement `enterprise_scored_examples` by adapting those same candidates for the UI.**
- [ ] **Step 4: Route enterprise phrasebook test queries through `enterprise_scored_examples`.**
- [ ] **Step 5: Run the focused tests and verify they pass.**

### Task 3: Return only final answers

**Files:**
- Modify: `reply_engine.py`
- Modify: `main.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `ENTERPRISE_NO_ANSWER_REPLY`, `NO_DIRECT_ANSWER_REPLY`, and only final managed-send selections.
- Consumes: enriched enterprise candidate confidence and enterprise-query scope detection.

- [ ] **Step 1: Make `ReplyEngine` direct-return exact or high-confidence local enterprise candidates without an API Key.**
- [ ] **Step 2: For enterprise-scope misses, return the enterprise no-answer text; for ordinary questions with only pending fallback candidates, return the generic no-answer text.**
- [ ] **Step 3: Filter pending candidates before managed selection and remove the delayed completion/“核实后回复” sender path.**
- [ ] **Step 4: Run the focused tests and verify they pass.**

### Task 4: Verify the full behavior

**Files:**
- Test: `tests/test_core.py`

- [ ] **Step 1: Run the complete unit test suite.**
- [ ] **Step 2: Reproduce the three real portable-database queries against the source code in no-Key combined mode.**
- [ ] **Step 3: Inspect the diff and report the actual test counts and any remaining limitation.**
