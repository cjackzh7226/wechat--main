# Task 1 — Immutable Knowledge Records and Stable Hashes

Source plan: `docs/superpowers/plans/2026-08-12-02-managed-knowledge-contract-and-migration.md`, Task 1.

## Scope

- Create `knowledge_base.py`.
- Create `tests/test_knowledge_contract.py`.
- Create checkpoint `artifacts/formal-hardening/2026-08-12/05-knowledge-schema.sha256.json`.
- Do not modify formal AppData, existing memory files, or migration targets.

## Binding contract

- `OrgScope`: `social | mfei | both | unknown`.
- `RecordStatus`: `active | conflict | pending | inactive`.
- Immutable `KnowledgeRecord`: record_id, question, reply, org_scope, keywords, source_file, source_locator, source_hash, answer_hash, status.
- Preserve original question/reply content exactly; normalization is only for identity, comparison, scoring, and hashes.
- Stable record ID uses normalized question + scope + normalized answer + source locator.
- Stable answer hash uses normalized answer.
- Explicit labels containing 社会团体/社团/协会/商会 map to social; 民办非企业/民非/社会服务机构 map to mfei; explicit common/both labels map to both; otherwise unknown.
- Unknown scope must never default active; it is pending.
- Reject invalid/non-string core fields and non-string keyword members with a typed schema error; do not stringify arbitrary values.
- No 3,000-record limit, truncation, forced chunking, model call, web search, free answer generation, or formal write.
- Exact no-match constant remains `暂未查到对应答案`, but Task 1 does not implement decision/send behavior.

## TDD and evidence

- Capture true RED for module absence/schema behavior before implementation.
- Cover deterministic hashes, Unicode/whitespace normalization boundaries, immutable tuple keywords, explicit scope precedence, unknown pending, exact content preservation, invalid types, and source locator identity.
- Run focused tests and relevant full existing tests under an explicit temporary `WECHAT_ASSISTANT_DATA_DIR` or the suite isolation fixture.
- Record RED/GREEN commands and results in `task-1-report.md`; include current formal-tree before/after read-only comparison.
- Project is non-Git; do not initialize Git or commit.
- Do not declare review PASS; hand back for independent code review.
