# Formal-Use Hardening Roadmap

> **Workflow override (2026-08-13, user-approved):** Execute in coherent batches of roughly 3–4 related changes. Do not require per-subtask dual review, repeated full-suite/build runs, or routine source hashes. Use focused validation during development; reserve independent review and complete validation for high-risk boundaries, runnable milestones, and final delivery. This project-local rule overrides conflicting process-skill defaults.

**Goal:** Safely convert the current WeCom assistant into a knowledge-only, auditable, directly launchable EXE without risking the formal 449-record knowledge base or sending to the wrong chat.

**Architecture:** Execute four separately testable plans in dependency order. Persistence and strict configuration form the base; the managed knowledge contract consumes that base; the serialized send runtime consumes both; release validation packages only the verified system and applies the formal migration at the final gated stage.

**Tech Stack:** Python 3.11.9, dataclasses, JSON, Windows DPAPI and named mutex, unittest/pytest, Pillow/RapidOCR/OpenCV, pywin32, PyInstaller 6.21.0, PowerShell.

## Global Constraints

- The only automatic reply source is an existing knowledge-base record; the LLM may only rank candidates and judge consistency.
- The exact no-match reply is `暂未查到对应答案`.
- OCR, chat identity, foreground state, data integrity, or send-verification failures must not send any message.
- Organization scope is a hard local partition: `social`, `mfei`, `both`, or `unknown`; the LLM cannot override it.
- Long text remains intact and is never force-split; over-limit input fails explicitly without truncation.
- Source and EXE runs share `%APPDATA%\WeChatStyleReplyAssistant\`.
- Existing formal data is not mutated until all isolated tests, dry-run migration checks, observation-mode checks, and controlled-send checks pass.
- The 3 old AppData records are deleted at the final migration gate and are not merged into the 449-record source.
- Formal delivery is a windowed onedir `聊小智.exe`; `启动.bat` is development-only.
- The repository is not Git-backed. Replace commit steps with test evidence, SHA-256 file manifests, and dated `.workbuddy\memory\YYYY-MM-DD.md` checkpoints.
- Preserve the two verified pre-change ZIP backups and never package API keys, user data, chat content, or runtime logs.

---

## Execution Order

### Phase 1: Persistence and Configuration

Execute [2026-08-12-01-persistence-and-settings-hardening.md](./2026-08-12-01-persistence-and-settings-hardening.md).

Exit gate:

- Source and frozen modes resolve the same AppData paths.
- Tests cannot touch production AppData.
- Critical JSON writes are atomic with five recoverable versions.
- Corrupt settings or memory cannot be silently replaced with empty data.
- Keys are DPAPI-protected and absent from JSON/logs.
- Runtime configuration cannot enable free generation or web search.

### Phase 2: Managed Knowledge Contract and Migration

Execute [2026-08-12-02-managed-knowledge-contract-and-migration.md](./2026-08-12-02-managed-knowledge-contract-and-migration.md).

Exit gate:

- Organization scope is resolved locally before scoring.
- Conflict and inactive records cannot become candidates.
- LLM responses are strict typed JSON and never provide the answer body.
- Final text is reloaded by record ID and answer hash.
- All no-match paths return the exact fallback.
- The 449-record dry run reports the expected scope and conflict baseline.
- Long unstructured input is rejected explicitly when over limit and is never split or truncated.

### Phase 3: Serialized Safe-Send Runtime

Execute [2026-08-12-03-serialized-safe-send-runtime.md](./2026-08-12-03-serialized-safe-send-runtime.md).

Exit gate:

- Personal and group messages use one serialized coordinator.
- Startup baselines existing visible messages.
- Chat identity contains normalized top title and chat type, not only the WeCom HWND/title.
- Cached OCR cannot satisfy send preflight.
- Background WeCom is never activated by monitoring.
- Post-send verification determines ledger state and uncertain sends never auto-retry.
- Duplicate launches are rejected by a Windows named mutex.

### Phase 4: Release Validation and EXE

Execute [2026-08-12-04-release-validation-and-exe.md](./2026-08-12-04-release-validation-and-exe.md).

Exit gate:

- Full isolated automated suite passes and leaves production hashes unchanged.
- Knowledge/data/fault/screenshot regression reports pass.
- Observation mode passes on `- @微信` and `-、阳光、琳、-`.
- Controlled real sends verify known match, exact no-match fallback, switching, background, restart, group @, group no-@, and uncertain-send behavior.
- Formal migration replaces the old 3 records with the audited 449-record source.
- Onedir EXE starts by double-click, remains single-instance, contains calibration and no private data, and passes packaged self-check.
- Release manifest, SHA-256, rollback verification, and dated development log are complete.

## Stop Conditions

Stop the rollout and keep automatic sending disabled if any of the following occurs:

- A reply body is not byte-for-byte sourced from an active knowledge record or the exact fallback.
- Social and MFEI records cross partitions.
- Chat identity changes between generation, preflight, send, and verification.
- A send is recorded successful without a verified own-message bubble.
- Tests or build commands modify production AppData.
- The migration input is not exactly 449 records or the target still contains any of the 3 old records.
- The package contains `settings.json`, `memory.json`, `vector_memory.json`, `credentials.dat`, runtime logs, or `user_data`.

## Evidence Directory

Each plan writes machine-readable reports under an isolated run directory such as:

`D:\Vibe C\客服\wechat--main\artifacts\formal-hardening\2026-08-12\`

This directory may contain test XML/JSON, data-audit reports, build manifests and hashes, but never API keys or complete private chat text.
