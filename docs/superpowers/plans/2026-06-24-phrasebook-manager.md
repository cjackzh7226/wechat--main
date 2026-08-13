# 话术库管理模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在聊小智设置中增加“话术库管理”入口，让用户查看不同话术库、查看命中分数、编辑推荐回复，并保存回本地记忆/向量库。

**Architecture:** 第一版不重构现有记忆文件格式，只在 `memory.py` 增加话术库分组、样例列表、命中评分和样例更新方法。`main.py` 暴露 UI 回调，`float_ui.py` 在设置页增加入口并打开独立管理弹窗。

**Tech Stack:** Python, Tkinter, existing `StyleMemory`, JSON `memory.json`, sparse vector `vector_memory.json`, unittest.

---

### Task 1: Memory API

**Files:**
- Modify: `memory.py`
- Test: `tests/test_core.py`

- [ ] Add tests for listing libraries, scoring examples, and updating replies.
- [ ] Implement `library_summaries()`, `examples_for_library()`, `score_examples()`, and `update_example()`.
- [ ] Ensure saving rebuilds `vector_memory.json`.

### Task 2: Main Callbacks

**Files:**
- Modify: `main.py`
- Test: `tests/test_core.py`

- [ ] Add callbacks `get_phrasebook_libraries`, `get_phrasebook_examples`, `save_phrasebook_example`.
- [ ] Reload memory and reply engine after saves.

### Task 3: Settings UI Entry and Manager Dialog

**Files:**
- Modify: `float_ui.py`

- [ ] Add “话术库管理” button next to import button.
- [ ] Add independent Toplevel dialog with left library list, middle rule list, right editor.
- [ ] Add query box for testing命中分数.
- [ ] Save edited cue/reply through callback.

### Task 4: Verification

**Commands:**
- `python -m unittest discover -s tests -v`
- `python -m compileall -q .`

**Expected:** all tests pass and settings dialog opens without syntax errors.
