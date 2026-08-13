# Release Validation and EXE Implementation Plan

> **Workflow override (2026-08-13, user-approved):** Use focused validation during implementation and consolidate full test/build/E2E/review work at release milestones and final delivery. Avoid repeated commands and routine hashes unless they protect release determinism or artifact integrity.

**Goal:** Prove the hardened assistant against isolated, fault-injected and real WeCom scenarios; then replace the 3 old records with the audited 449-record source and deliver a directly launchable onedir EXE with rollback evidence.

**Architecture:** First make test isolation automatic and run the full regression/data matrix. Build a versioned package from pinned dependencies, validate it without private data, run observation and controlled sends against an isolated staging data root, and only then apply the formal AppData migration and perform final packaged smoke tests.

**Tech Stack:** Python 3.11.9, pytest 9.1.1, unittest, Pillow 12.3.0, RapidOCR 1.4.4, OpenCV 5.0.0.93, PyInstaller 6.21.0, PowerShell, Windows WeCom 5.x UI automation.

## Global Constraints

- Production AppData hashes must remain unchanged through automated, build, dry-run and staging-live tests.
- Real automatic sends are limited to `- @微信` and test group `-、阳光、琳、-` until final acceptance.
- Known-match inbound test text is `如果账号密码忘记怎么办？` and must produce the unchanged knowledge answer `进入福建省网上办事大厅用户登录页面，找到“忘记密码”按操作找回。`.
- No-match inbound test text is `这是一个知识库中不存在的测试问题XYZ20260812` and must produce exactly `暂未查到对应答案`.
- User/external coordination is required to create inbound messages; the implementation pauses at that step and asks the user to send the exact listed messages.
- Uncertain-send behavior is fault-injected and must not risk a duplicate real message.
- Formal migration is blocked unless the source count is 449, old target count is 3, backup hash is verified and all previous gates pass.
- Package format is windowed onedir with one user-facing `聊小智.exe`.
- The package contains no settings, memory, vectors, ledger, credentials, logs or `user_data`.
- The repository is not Git-backed; evidence is tests, reports, SHA-256 manifests, backups and the dated development log.

---

## File Structure

- Create `tests/conftest.py`: session-wide isolated data/temp/cache roots before test collection.
- Create `tests/test_formal_acceptance.py`: cross-component business and production-hash invariants.
- Create `tests/fixtures/wecom/README.md`: fixture provenance/redaction rules.
- Create `tools/knowledge_audit.py`: machine JSON plus Chinese summary for all records.
- Create `release_manifest.py`: build/runtime manifest and private-file scan.
- Create `version.py`: release version constant.
- Create `requirements-runtime.lock.txt`, `requirements-dev.lock.txt`: pinned direct dependencies.
- Modify `requirements.txt`: point maintainers to locked installation and include OpenCV explicitly.
- Modify `self_check.py`: structured package/runtime checks and nonzero exit on blocking failures.
- Modify `main.py`: `--self-check`, `--package-check` and `--managed-mode` diagnostic CLI gates.
- Modify `聊小智.spec`: include calibration and manifest assets; exclude private data.
- Modify `build_release.py`: deterministic versioned onedir build, refusal to overwrite, manifest/hash generation.
- Create `tools/create_desktop_shortcut.ps1`: optional shortcut creation to the versioned EXE.
- Create `docs/正式上线验收清单_20260812.md`: observation/control/full rollout record.
- Append `.workbuddy/memory/2026-08-12.md`: complete audit/development/test/release log at task completion.

### Task 1: Enforce Test Isolation Before Collection

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_formal_acceptance.py`
- Modify: existing tests only where they hard-code AppData

**Interfaces:**
- Produces session environment: `WECHAT_ASSISTANT_DATA_DIR`, `TEMP`, `TMP`, `PYTHONPYCACHEPREFIX` under one generated test root.
- Produces: `production_file_hashes() -> dict[str, str]` and session-end unchanged assertion.

- [ ] **Step 1: Write the failing production-hash invariant test**

```python
def test_full_test_run_uses_isolated_data_root(isolated_data_root):
    assert app_paths.data_dir() == isolated_data_root
    assert str(isolated_data_root).startswith(tempfile.gettempdir())
    assert app_paths.data_dir() != Path(os.environ["APPDATA"]) / "WeChatStyleReplyAssistant"

def test_no_runtime_files_appear_in_repository_root():
    forbidden = {"memory.json", "settings.json", "vector_memory.json", "send_ledger.json", "managed_send.log"}
    assert forbidden.isdisjoint({item.name for item in PROJECT_ROOT.iterdir()})
```

- [ ] **Step 2: Run a collection-only probe and verify isolation is not yet automatic**

Run: `.\.venv\Scripts\python.exe -m pytest --collect-only -q`

Expected: collection succeeds but the new invariant fails when run because no session data-root fixture exists.

- [ ] **Step 3: Configure isolation before test modules import application code**

```python
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="liaoxiaozhi-tests-"))
os.environ["WECHAT_ASSISTANT_DATA_DIR"] = str(_SESSION_ROOT / "appdata")
os.environ["PYTHONPYCACHEPREFIX"] = str(_SESSION_ROOT / "pycache")
os.environ["TEMP"] = str(_SESSION_ROOT / "temp")
os.environ["TMP"] = str(_SESSION_ROOT / "temp")
```

Create the directories in `pytest_configure`, capture SHA-256 for production `settings.json`, `memory.json`, `vector_memory.json`, `send_ledger.json` and `credentials.dat` if present, then compare at `pytest_unconfigure`. A mismatch raises a failing terminal summary and preserves the test root for inspection.

- [ ] **Step 4: Run the entire existing and new suite twice**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: both runs pass; no production hash changes; no production `managed_send.log` mtime change.

- [ ] **Step 5: Record checkpoint evidence**

Run: `.\.venv\Scripts\python.exe -m pytest --junitxml=artifacts\formal-hardening\2026-08-12\full-isolated-baseline.xml`

Expected: exit code 0 with at least the prior 297 tests plus all new tests.

### Task 2: Add Data, Fault and Real-Screenshot Regression Gates

**Files:**
- Create: `tools/knowledge_audit.py`
- Create: `tests/fixtures/wecom/README.md`
- Modify: `tests/test_formal_acceptance.py`
- Modify: `conversation_list.py`, `capture_ocr.py` Pillow pixel iteration calls

**Interfaces:**
- Produces CLI: `python tools/knowledge_audit.py --memory PATH --json PATH --summary PATH`.
- Produces fixture rules: real WeCom version, resolution, crop coordinates, expected chat type/role, redaction confirmation and SHA-256.

- [ ] **Step 1: Write failing formal business/fault tests**

```python
def test_all_active_answers_have_record_and_source_hashes(audited_449_repository):
    assert audited_449_repository.input_count == 449
    assert all(r.record_id and r.answer_hash and r.source_hash for r in audited_449_repository.active_records)

@pytest.mark.parametrize("fault", [
    "ocr_empty", "ocr_exception", "identity_changed", "window_background",
    "settings_corrupt", "memory_corrupt", "vector_mismatch", "api_timeout",
    "api_invalid_json", "send_uncertain",
])
def test_fault_matrix_never_sends(fault, formal_runtime_fixture):
    outcome = formal_runtime_fixture.run_with_fault(fault)
    assert outcome.sent is False
    assert formal_runtime_fixture.sender.calls == [] or fault == "send_uncertain"
    assert outcome.ledger_status != "verified"
```

- [ ] **Step 2: Run and verify missing audit/fault coverage**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_formal_acceptance.py -v`

Expected: FAIL until audit CLI, 449 fixture and fault adapters are complete.

- [ ] **Step 3: Implement knowledge audit and screenshot fixture protocol**

The audit report contains input/output counts, scope counts, active/pending/conflict/inactive counts, exact duplicates, same-scope conflicts, cross-scope differing answers, missing fields, source/hash integrity and recall outcomes.

For screenshot regression, capture only controlled test conversations. Crop away the conversation list and unrelated contacts; redact names except the test title required for identity assertion. Store PNGs under `tests/fixtures/wecom/` only after visual inspection confirms no unrelated private content. Test current-contact title, personal/group classification, other/own message roles, group @ extraction and outgoing-bubble verification.

Replace deprecated Pillow `getdata()` calls with:

```python
def flattened_pixels(image):
    getter = getattr(image, "get_flattened_data", None)
    return list(getter()) if callable(getter) else list(image.getdata())
```

- [ ] **Step 4: Run knowledge audit, fault matrix, screenshot regressions and warning gate**

Run:

```powershell
.\.venv\Scripts\python.exe tools\knowledge_audit.py `
  --memory "dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json" `
  --json "artifacts\formal-hardening\2026-08-12\knowledge-audit.json" `
  --summary "artifacts\formal-hardening\2026-08-12\knowledge-audit-zh.md"
.\.venv\Scripts\python.exe -m pytest tests\test_formal_acceptance.py -W error::DeprecationWarning -v
```

Expected: exit code 0; input 449; no missing question/reply; 17 conflict groups/36 records are ineligible; no Pillow deprecation warning.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash tools\knowledge_audit.py,tests\test_formal_acceptance.py,tests\fixtures\wecom\* -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\15-acceptance-fixtures.sha256.json -Encoding UTF8`

### Task 3: Pin Dependencies and Build Structured Self-Checks

**Files:**
- Create: `version.py`
- Create: `requirements-runtime.lock.txt`
- Create: `requirements-dev.lock.txt`
- Modify: `requirements.txt`
- Modify: `self_check.py:11-171`
- Modify: `main.py:1258-1270`
- Create: `release_manifest.py`
- Test: `tests/test_formal_acceptance.py`
- Modify: `tests/test_core.py:4551-4565`

**Interfaces:**
- Produces version `2026.08.12.1`.
- Produces CLI exit codes: 0 pass, 2 blocking package/runtime failure.
- Produces `run_package_check(app_dir) -> CheckReport`, `run_runtime_check(data_dir) -> CheckReport`, `write_release_manifest(release_dir) -> Path`, `scan_private_files(release_dir) -> list[str]`.

- [ ] **Step 1: Write failing self-check and private-package tests**

```python
def test_package_check_requires_calibration_and_ocr_assets(tmp_path):
    report = run_package_check(tmp_path)
    assert report.ok is False
    assert "wecom_5_calibration.json" in report.missing

def test_private_file_scan_blocks_user_data(tmp_path):
    (tmp_path / "user_data").mkdir()
    (tmp_path / "user_data" / "settings.json").write_text("{}", encoding="utf-8")
    assert scan_private_files(tmp_path) == ["user_data/settings.json"]

def test_runtime_check_is_not_satisfied_by_model_memory_mode(valid_data_dir):
    write_settings(valid_data_dir, reply_source_mode="model_memory")
    report = run_runtime_check(valid_data_dir)
    assert report.ok is False
```

- [ ] **Step 2: Run and confirm current self-check/build tests are insufficient**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_formal_acceptance.py tests\test_core.py -k "package_check or private_file or self_check or BuildRelease" -v`

Expected: FAIL because current self-check checks obsolete multi-platform paths and can report success without exercising the formal knowledge mode.

- [ ] **Step 3: Add exact direct-dependency locks and structured reports**

Write `requirements-runtime.lock.txt` with:

```text
requests==2.34.2
PyAutoGUI==0.9.54
pyperclip==1.11.0
pywin32==312
Pillow==12.3.0
rapidocr-onnxruntime==1.4.4
onnxruntime==1.28.0
xlrd==2.0.2
pypdf==6.14.2
opencv-python==5.0.0.93
```

Write `requirements-dev.lock.txt` with `-r requirements-runtime.lock.txt`, `pytest==9.1.1`, and `pyinstaller==6.21.0`. `requirements.txt` remains a readable direct dependency list but uses exact pins and includes OpenCV.

Package check validates imported modules, OCR assets, calibration JSON schema, application files and absence of private files. Runtime check validates settings schema/mode, memory/hash, conflict quarantine, vector consistency, ledger, credential readability and WeCom window/calibration. API check sends a minimal candidate-judge request and validates strict JSON; it cannot fall back to a no-op model-memory path.

Add argparse handling so `main.py --package-check --report PATH` and `main.py --self-check --report PATH` run without starting the UI/monitor.

- [ ] **Step 4: Run dependency and self-check tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests\test_formal_acceptance.py tests\test_core.py -k "self_check or manifest or private or requirements" -v
.\.venv\Scripts\python.exe main.py --package-check --report artifacts\formal-hardening\2026-08-12\source-package-check.json
```

Expected: all commands exit 0; the report lists exact Python/application/dependency versions.

- [ ] **Step 5: Record checkpoint hashes**

Run: `Get-FileHash version.py,requirements*.txt,self_check.py,release_manifest.py,main.py -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\16-selfcheck-locks.sha256.json -Encoding UTF8`

### Task 4: Build a Versioned Private-Data-Free Onedir EXE

**Files:**
- Modify: `聊小智.spec:1-76`
- Modify: `build_release.py:1-230`
- Create: `tools/create_desktop_shortcut.ps1`
- Test: `tests/test_formal_acceptance.py`
- Modify: `tests/test_core.py:4551-4565`

**Interfaces:**
- Produces: `dist\聊小智_2026.08.12.1\聊小智\聊小智.exe`.
- Produces: `release-manifest.json`, `SHA256SUMS.txt`, `BUILD-INFO.txt`.
- Consumes exact locks and `version.__version__`.

- [ ] **Step 1: Write failing spec/manifest/output tests**

```python
def test_spec_includes_calibration_and_excludes_user_data():
    text = Path("聊小智.spec").read_text(encoding="utf-8")
    assert "wecom_5_calibration.json" in text
    assert "user_data" not in text

def test_build_refuses_to_overwrite_existing_release(tmp_path):
    release = tmp_path / "聊小智_2026.08.12.1"
    release.mkdir()
    with pytest.raises(FileExistsError):
        prepare_release_dir(release)
```

- [ ] **Step 2: Run and confirm stale build behavior**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_formal_acceptance.py tests\test_core.py -k "spec or build or release" -v`

Expected: FAIL because calibration is absent from datas, build installs latest packages and old output handling can target the wrong folder.

- [ ] **Step 3: Make build deterministic and non-destructive**

Add `('wecom_5_calibration.json', '.')` to `datas`; retain required OCR/OpenCV collections. `build_release.py` must:

1. verify Python 3.11.9 and direct dependency versions;
2. refuse to install or upgrade packages;
3. refuse to overwrite an existing version directory;
4. invoke PyInstaller with a version-specific `--distpath` and `--workpath`;
5. run private-file scan;
6. write manifest and SHA-256 files;
7. execute packaged `聊小智.exe --package-check`;
8. leave older release folders untouched.

The shortcut script accepts an exact EXE path, resolves it, confirms it is inside the selected version directory and creates `聊小智.lnk` on the current user desktop through `WScript.Shell`.

- [ ] **Step 4: Build and validate the EXE**

Run:

```powershell
.\.venv\Scripts\python.exe build_release.py --version 2026.08.12.1
& "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" --package-check `
  --report "artifacts\formal-hardening\2026-08-12\packaged-check.json"
```

Expected: both commands exit 0. `scan_private_files` returns an empty list; calibration exists; manifest hashes match actual files; the EXE starts without a console window.

- [ ] **Step 5: Record package evidence**

Run: `Get-FileHash "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" -Algorithm SHA256 | Format-List | Out-File artifacts\formal-hardening\2026-08-12\exe-sha256.txt -Encoding UTF8`

### Task 5: Observation and Controlled Real-WeCom Validation

**Files:**
- Create: `docs/正式上线验收清单_20260812.md`
- Create at runtime: isolated staging data root under `artifacts\formal-hardening\2026-08-12\staging-data`
- Create only after redaction review: controlled screenshots under `tests/fixtures/wecom/`

**Interfaces:**
- Consumes packaged EXE, 449-record migrated staging data, test contact `- @微信`, test group `-、阳光、琳、-`.
- Produces signed-off observation/controlled checklist with timestamps, decision IDs, expected/actual text and screenshot hashes.

- [ ] **Step 1: Prepare isolated staging data and observation mode**

Run:

```powershell
.\.venv\Scripts\python.exe tools\migrate_formal_knowledge.py `
  --source "dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json" `
  --target "artifacts\formal-hardening\2026-08-12\staging-data\memory.json" `
  --report "artifacts\formal-hardening\2026-08-12\staging-migration.json" `
  --apply --expected-source-count 449 --expected-old-target-count 0 --staging
$env:WECHAT_ASSISTANT_DATA_DIR = (Resolve-Path "artifacts\formal-hardening\2026-08-12\staging-data")
& "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" --managed-mode observe
```

Expected: the packaged UI reports observation mode, 449 source records migrated, 17 conflict groups disabled and no sender calls.

- [ ] **Step 2: Ask for the exact inbound messages and validate observation results**

Pause and ask the user to send to `- @微信`, in order:

1. `如果账号密码忘记怎么办？`
2. `这是一个知识库中不存在的测试问题XYZ20260812`

Then ask for the group message:

3. `@程正恒 如果账号密码忘记怎么办？`
4. `如果账号密码忘记怎么办？` without an @ mention.

Expected in observation UI: item 1 selects the exact known answer; item 2 selects exact fallback; item 3 produces the known group candidate; item 4 is ineligible and produces no reply decision. No real outgoing message appears.

- [ ] **Step 3: Run controlled sends and manual-switch/background/restart checks**

Set controlled allowlist to normalized `- @微信` and `-、阳光、琳、-`, then switch `managed_mode` to `controlled`.

Repeat known-match and no-match inbound messages. During the configured delay, manually switch from `- @微信` to another chat; expected: no outgoing message in either chat. Put WeCom in the background before another inbound message; expected: no focus steal and no send. Return to test chat, let it stabilize and submit a fresh message; expected: one verified reply. Restart the EXE with the last message still visible; expected: the startup baseline prevents a duplicate.

For the group, repeat the exact @ message and one no-@ message; expected: exactly one verified @ reply for the first and none for the second. Send the same @ text once from a different group member if available; expected: it is a distinct turn key.

- [ ] **Step 4: Verify actual bubbles, logs, ledger and no duplicate instance**

For each real send, compare the actual own bubble against the expected text character-for-character. Check the ledger state is `verified`; no outcome may rely only on an Enter keypress. Inject `uncertain` through the test adapter, not a real message, and verify there is no retry.

While the packaged app is running, double-click `聊小智.exe` again. Expected: the second process displays “程序已在运行” and exits; only one mutex-owning runtime remains.

Record screenshot hashes and redact/crop them before moving any to test fixtures.

- [ ] **Step 5: Complete the controlled gate report**

Fill every row in `docs/正式上线验收清单_20260812.md` with `PASS` or `FAIL`, actual time, expected/actual behavior and evidence path. Any `FAIL` returns to the responsible earlier task; do not proceed to formal migration.

### Task 6: Apply the Formal 449-Record Migration and Verify Rollback

**Files/Data:**
- Read: `dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json`
- Replace only after checks: `%APPDATA%\WeChatStyleReplyAssistant\memory.json`
- Preserve: both pre-change ZIP backups listed in the design.
- Produce: `artifacts\formal-hardening\2026-08-12\formal-migration.json`

**Interfaces:**
- Consumes migration CLI and required backup SHA-256 `CAA8AC151861BF2D289CD6CE0F57FCB1A287A96167896B775B55C3F61560563A`.
- Produces formal AppData containing the audited migrated source and no legacy `chat_zip_training` records.

- [ ] **Step 1: Reverify backups and stop all assistant instances**

Run:

```powershell
Get-FileHash "D:\Vibe C\客服\备份\wechat--main_pre_formal_audit_20260812_010125.zip" -Algorithm SHA256
Get-FileHash "D:\Vibe C\客服\备份\WeChatStyleReplyAssistant_runtime_data_pre_formal_audit_20260812_012425.zip" -Algorithm SHA256
Get-Process | Where-Object { $_.ProcessName -like '*聊小智*' -or $_.Path -like '*wechat--main*main.py*' }
```

Expected hashes: project `522DB2E0A6EA93BD1AF289A75053C3DE9161265C9F05F81DC00FE003EF37D12A`; runtime `CAA8AC151861BF2D289CD6CE0F57FCB1A287A96167896B775B55C3F61560563A`. No assistant process remains. Do not stop WeCom.

- [ ] **Step 2: Re-run formal dry run and verify target count 3**

Run:

```powershell
.\.venv\Scripts\python.exe tools\migrate_formal_knowledge.py `
  --source "dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json" `
  --target "$env:APPDATA\WeChatStyleReplyAssistant\memory.json" `
  --report "artifacts\formal-hardening\2026-08-12\formal-migration-dry-run-final.json" `
  --dry-run --expected-source-count 449 --expected-old-target-count 3
```

Expected: `applied=false`, source 449, target 3, conflicts 17/36, and no target hash change.

- [ ] **Step 3: Apply atomic replacement, deleting the 3 old records**

Run:

```powershell
.\.venv\Scripts\python.exe tools\migrate_formal_knowledge.py `
  --source "dist\聊小智_开发测试_20260803_v4\聊小智\user_data\memory.json" `
  --target "$env:APPDATA\WeChatStyleReplyAssistant\memory.json" `
  --report "artifacts\formal-hardening\2026-08-12\formal-migration.json" `
  --apply --expected-source-count 449 --expected-old-target-count 3 `
  --required-backup "D:\Vibe C\客服\备份\WeChatStyleReplyAssistant_runtime_data_pre_formal_audit_20260812_012425.zip" `
  --required-backup-sha256 CAA8AC151861BF2D289CD6CE0F57FCB1A287A96167896B775B55C3F61560563A
```

Expected: atomic apply succeeds, report records old target count 3 and confirms zero retained old `chat_zip_training` records. The 449 input records are represented in the archive/report; exact duplicates, if any, follow the approved collapse rule.

- [ ] **Step 4: Run packaged runtime self-check and rollback rehearsal**

Run:

```powershell
& "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" --self-check `
  --report "artifacts\formal-hardening\2026-08-12\formal-runtime-check.json"
```

Expected: exit 0; knowledge, conflict quarantine, vector, ledger, credentials and calibration pass. Rehearse rollback only into a temporary directory by extracting the runtime ZIP there and verifying its files/hashes; do not overwrite the newly migrated formal AppData during rehearsal.

- [ ] **Step 5: Record formal data hashes**

Run: `Get-FileHash "$env:APPDATA\WeChatStyleReplyAssistant\memory.json","$env:APPDATA\WeChatStyleReplyAssistant\vector_memory.json" -Algorithm SHA256 | ConvertTo-Json | Set-Content artifacts\formal-hardening\2026-08-12\formal-data.sha256.json -Encoding UTF8`

### Task 7: Final Launch, Shortcut, Documentation and Dated Development Log

**Files:**
- Create: desktop shortcut through `tools/create_desktop_shortcut.ps1`
- Modify: `docs/项目工作提示词_给新会话.md`
- Modify: `.workbuddy/memory/MEMORY.md`
- Add or append: `.workbuddy/memory/2026-08-12.md`
- Finalize: `docs/正式上线验收清单_20260812.md`

**Interfaces:**
- Produces the supported user entry point `聊小智.exe`/desktop shortcut and complete no-context handoff.

- [ ] **Step 1: Update stale documentation facts**

Replace old threshold 20 references with the verified threshold 30 and margin 8. Remove documentation that describes free model generation/fallback as part of automatic send. Document the exact fallback, organization partition, AppData source of truth, managed modes, single-instance behavior, self-check command, backup paths and rollback boundary.

- [ ] **Step 2: Create and verify the desktop shortcut**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File tools\create_desktop_shortcut.ps1 `
  -ExePath "D:\Vibe C\客服\wechat--main\dist\聊小智_2026.08.12.1\聊小智\聊小智.exe"
```

Expected: the shortcut resolves to the exact versioned EXE; double-click starts the windowed program with no console and passes the single-instance test. `启动.bat` remains marked development-only.

- [ ] **Step 3: Run final verification from a fresh process**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q -x "(\\.venv|dist|build|artifacts)" .
.\.venv\Scripts\python.exe -m pip check
& "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" --package-check `
  --report "artifacts\formal-hardening\2026-08-12\final-package-check.json"
& "dist\聊小智_2026.08.12.1\聊小智\聊小智.exe" --self-check `
  --report "artifacts\formal-hardening\2026-08-12\final-runtime-check.json"
```

Expected: all exit 0; no deprecation warnings treated as errors; production hashes change only where formal migration/key re-encryption/ledger initialization explicitly requires it.

- [ ] **Step 4: Append the required dated development log using `apply_patch`**

Write `.workbuddy/memory/2026-08-12.md` with: backup paths/hashes, audit findings, approved design decisions, every created/modified file, migration input/output/scope/conflict counts, automated test count/result, real WeCom matrix, EXE path/version/hash, package scan, self-check results, rollback rehearsal, unresolved inactive conflicts and operating instructions.

Do not include API keys, complete private messages or unredacted screenshots.

- [ ] **Step 5: Produce the final delivery manifest**

Create `artifacts\formal-hardening\2026-08-12\FINAL-DELIVERY.md` listing clickable/absolute locations for EXE, design, plans, acceptance checklist, development log, release manifest, test report, knowledge audit, migration report and both backups. Recompute hashes and compare them with all recorded manifests before reporting completion.
