# Phase 2 Task 1 Independent Review

Date: 2026-08-12  
Assessment: **Needs fixes**  
Findings: **Critical 0 / Important 6 / Minor 1**

## Scope and evidence checked

Read completely: `task-1-brief.md`, `task-1-report.md`, Phase 2 plan Task 1 and Global Constraints, and the design sections covering the knowledge record, organization partition, source traceability, import, migration, and knowledge-specific QA. Reviewed current `knowledge_base.py`, all of `tests/test_knowledge_contract.py`, and `05-knowledge-schema.sha256.json` line by line.

Independent read-only checks:

- `tests/test_knowledge_contract.py`: **62 passed** under an explicit temporary `WECHAT_ASSISTANT_DATA_DIR`.
- Current SHA-256 values exactly match checkpoint 05: `knowledge_base.py` = `c13bc91267fd8c14f5742db1a202c396fc86ca2b5f7b05038a11f0aaf8c4da15`; test file = `b244594ababe1034e22e5f110486ab7411182d63d587905896226c8ffe2debb7`.
- The available formal-AppData before/after manifests both contain 32 files and 346786 bytes and are byte-identical. This is consistent with the report's read-only comparison. The reviewer performed no formal write, network call, Enterprise WeChat start, sender start, or migration.

Passing aspects include exact storage of factory input `question`/`reply`, immutable tuple conversion for factory-provided keywords, deterministic fixed-position JSON serialization without delimiter ambiguity, source-locator participation in `record_id`, stable results for completely identical inputs, and typed factory failures for the covered non-string inputs.

## Important findings

### I1. The public frozen record does not enforce the typed schema

**Location:** `knowledge_base.py:43-54`; factory-only checks at `knowledge_base.py:79-106,129-148`.

**Reproduction:** `KnowledgeRecord(None, 7, [], "bogus", ("ok", 9), None, None, None, None, "enabled")` constructs successfully. `frozen=True` only prevents later attribute assignment; it does not validate constructor values or make an invalid nested value valid.

**Impact:** Task 2 and later repository/import code can directly construct a nominal `KnowledgeRecord` containing null/non-string core fields, illegal scope/status, or non-string keyword members. Downstream code can no longer treat the advertised typed immutable record as a trusted boundary, contrary to the binding schema contract.

**Fix:** add a strict `__post_init__` (using `object.__setattr__` only if canonical tuple conversion is deliberately supported) that validates every field, keyword member, enum value, and required hash shape. Keep `record_from_example` validation for better source-field errors, but make invalid `KnowledgeRecord` construction itself raise `KnowledgeSchemaError`. Add direct-constructor negative tests.

### I2. `answer_hash` cannot prove that the post-judge answer body is unchanged

**Location:** `knowledge_base.py:61-76,169-171`; behavior is positively encoded by `tests/test_knowledge_contract.py:47-60`.

**Reproduction:** replies `"第一行\n- 条件A\n- 条件B"` and `"第一行 - 条件A - 条件B"` receive the same `answer_hash`. NFKC also makes `"item①"` and `"item1"` share the hash. Both records retain different exact `reply` strings.

**Impact:** the design and Task 4 require reloading the answer by ID and using its hash to verify that the answer sent is unchanged after judgement. A repository replacement that changes layout, list structure, full-width/compatibility characters, or other exact body content can pass this hash check. The same hash is currently being asked to serve two incompatible meanings: semantic/dedupe equivalence and exact-send integrity.

**Fix:** separate an exact content hash from a comparison/deduplication key. `answer_hash` used by the managed-send integrity check should hash the exact UTF-8 answer (at most a separately approved line-ending canonicalization); use a distinct normalized comparison hash/key for conflict grouping. Add a mutation-after-judgement test in which only whitespace/layout or an NFKC compatibility character changes and require technical failure.

### I3. `source_hash` hashes a location tuple, not source content, and missing provenance can be active

**Location:** `knowledge_base.py:88-95,140-172`; report claim at `task-1-report.md:99,102,112`.

**Reproduction:** changing a supplied `source_excerpt` from one body to another leaves `source_hash` unchanged because that field is ignored; `source_hash` is only `sha256(JSON[source_file, source_locator])`. A known-scope item with omitted `source_file` and `source_locator` is accepted as `active` and gets the stable hash of `JSON["",""]`.

**Impact:** this cannot support the design's source-integrity audit or Task 5's required source excerpt/hash contract. Source content can change in place without changing `source_hash`, and a record with no auditable source looks structurally hashed and active. Calling a position hash `source_hash` will make later import/migration checks misleading rather than merely incomplete.

**Fix:** define the contract now: hash verified source content/excerpt bytes (and retain locator separately), or rename the current value to `source_identity_hash` and add a required content hash. Require non-empty critical provenance before `active`; unresolved/missing provenance must be `pending` or rejected. Add source-content mutation, empty-source, and structured/unstructured provenance tests before Task 5 builds on this field.

### I4. `record_id` aliases distinct source/organization metadata and leaves M5 unresolved

**Location:** `knowledge_base.py:151-180`; future collapse contract at plan `2026-08-12-02-managed-knowledge-contract-and-migration.md:164`.

**Reproduction:** otherwise identical records at `S!A2:B2` in `a.xlsx` and `b.xlsx` have different `source_hash` values but the same `record_id`. Keywords and the original `organization_type` metadata also do not affect identity. Thus a future `record_id`-keyed mapping or exact-duplicate collapse can keep only one arrival-dependent metadata set.

**Impact:** Phase 1 M5 explicitly deferred deterministic metadata preservation to this schema. The present identity can swallow distinct sources and organization metadata before conflict/source audit, and the single-valued provenance fields provide no defined way to merge both. This weakens traceability and can make Task 2 dedupe order-dependent.

**Fix:** either include canonical source identity (at least source file + locator + source content hash) in `record_id`, or define a separate semantic duplicate key and preserve a deterministic one-to-many provenance collection when collapsing. Explicitly define deterministic keyword/organization metadata merging and test reversed input order, same locator in different files, and same answer with different approved organization/source metadata.

### I5. Scope inference treats negated or incidental marker substrings as explicit labels

**Location:** `knowledge_base.py:109-126`, especially substring checks at `118-125`; incomplete cases at `tests/test_knowledge_contract.py:127-157`.

**Reproduction:** `非社会团体`, `不属于协会`, and `与商会合作的机构` all resolve to `social/active`. `两类机构不均适用` resolves to `both/active` because any occurrence of `两类` plus `均` is accepted. The existing tests cover mixed positive markers but no negation, role/relationship wording, or both-negation boundary.

**Impact:** these are not harmless false negatives: they activate a record in the wrong hard partition, enabling a later candidate path to return an answer for the wrong organization type. This violates the local scope boundary that the model cannot override.

**Fix:** parse only a constrained, explicit organization-label grammar for imported metadata. Reject/quarantine negation, alternatives/questions, transition/relationship wording, and incidental containment as `unknown`; do not infer `both` from loose token co-occurrence. Add the reproduced cases plus positive exact variants for 社会团体/社团/协会/商会, 民办非企业/民非/社会服务机构, and explicit both/common labels.

### I6. Explicit `status="active"` bypasses the unknown-scope quarantine

**Location:** `knowledge_base.py:140-148`; only default behavior is tested at `tests/test_knowledge_contract.py:160-167`.

**Reproduction:** `record_from_example({..., "organization_type":"其他组织", "status":"active"})` returns `org_scope="unknown", status="active"`.

**Impact:** the effective invariant is currently only “unknown defaults pending,” not “unknown is pending.” A legacy/imported explicit status can bypass the intended quarantine, leaving later repository code to rediscover a rule the record contract claims to establish.

**Fix:** enforce the scope/status cross-field invariant in the record validator/factory. Reject `unknown + active` (and any other disallowed combination) with `KnowledgeSchemaError`, or deterministically force it to `pending` under an explicitly documented migration rule. Add all legal and illegal scope/status matrix tests.

## Minor finding

### M1. Hash tests do not pin the public canonical payload

**Location:** `tests/test_knowledge_contract.py:36-89`.

The tests prove repeatability and a few pairwise differences, but never independently calculate a known expected digest for the documented ordered JSON payload. A future accidental field reorder, omission, serializer change, or source-file alias can still satisfy most deterministic assertions while breaking persisted IDs across releases.

**Fix:** add fixed golden vectors calculated independently with canonical UTF-8 JSON, covering every identity component, separator-ambiguous strings, Unicode boundaries, and exact identical records. Keep these distinct from implementation-mirroring helper tests.

## Assessment

**Needs fixes.** Checkpoint and current test evidence are internally consistent, and no Critical issue was found, but the schema is not yet a trustworthy typed/provenance boundary. In particular, I2 and I3 would undermine the later unchanged-answer and source-integrity gates, while I5 and I6 can produce incorrectly active scope records. Resolve all Important findings and rerun focused plus relevant full isolated suites before Task 2 depends on this contract.
