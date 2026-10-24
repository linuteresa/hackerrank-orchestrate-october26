# PLAN — Ghost Data Still Exists deletion-audit agent

Status: **Phase 3 implemented on `main` (2026-10-24, `GO PHASE 3`), live model calls blocked** — intent and
screenshot steps are built, cached and tested, but this container has no Anthropic credentials, so no model
output exists yet; every row is held back from final export. 91 unittest cases pass. PR #1 (Phases 1–2) merged.

---

## 1. Central principle

> Tested code decides; the model reads, understands, investigates and proposes.

Final `deletion_status` values and `steps_json` are produced by deterministic, tested
code. Claude is used only where language or visual interpretation, or bounded investigation
of uncertain evidence, is required. Every model-proposed fact is re-checked in code.

## 2. Pipeline

```
dataset/ ──► ingest/ ──► observation store (+ coverage records) ──► identity resolution
        ──► deterministic investigation ──► [optional Claude investigator, flagged cases only]
        ──► policy engine ──► planner + validator ──► output.csv
```

Side branches:

- Claude structured-output intent classifier (cached).
- Claude screenshot extraction for the 11 PNGs (cached, schema-validated).
- Claude investigator agent, invoked only for flagged uncertain cases.
- Evaluation harness comparing predictions with the 32 labelled samples.
- Final run over all 518 requests, then validation and evaluation reports.

## 3. Dataset facts (measured 2026-10-24; sanity checks only, never classification shortcuts)

| Item | Value |
|---|---|
| Target requests | 518 (`deletion_requests.csv`) |
| Labelled samples | 32 (`sample_deletion_requests.csv` / `sample_output.csv`) |
| Evidence files | 94: 24 SQL, 28 JSON, 26 log, 1 txt, 4 PDF, 11 PNG |
| `system_activity.csv` | 4,011 events, 17 event types, results SUCCESS/FAILED/PARTIAL |
| `user_identities.csv` | 850 users; `user_audit_log.csv` 306 identity changes |
| `deletion_rules.csv` | 15 rules over 15 data categories |

User-supplied (not yet re-measured): reported statuses 254 COMPLETE / 152 PARTIALLY_COMPLETE /
112 NOT_STARTED; 10 non-deletion requests; languages EN/FR/ES/PT/JA/KO; no NOT_APPLICABLE in samples.

## 4. Observation model (ADR-002 core ACCEPTED 2026-10-24; hypotheses listed separately)

### 4.1 Accepted

- Observations are stored separately per **physical copy**: key `(system_id, resource_id, copy_key)`.
- `Coverage` records are stored separately from observations.
- A compliant primary never hides a non-compliant replica: resource-level compliance requires
  every known copy to be compliant.
- Provenance is preserved (source file, table, line, snapshot time, raw values).
- Region / instance / database identity is preserved when the evidence distinguishes copies;
  role alone is not a sufficient copy identity.
- Inventory snapshots, record bundles and logical API lookups keep an **unresolved** copy
  mapping where provenance is unclear.
- Omission from a snapshot is never stored or reported as ABSENT. Uncertainty is preserved.

### 4.2 `Observation` — a positive statement by a source about one copy

| Field | Meaning |
|---|---|
| `system_id`, `resource_id` | Logical system and resource |
| `copy_key` | Physical-copy identity built only from explicit evidence: `{database, role, region, instance}`; unknown parts are `None`. SQL: from the table name (`customer_profiles_failover_replica` → database `customer_profiles`, role `failover_replica`; `customer_data_replica_eu_west` → database `customer_data`, role `replica`, region `eu_west`). Non-copy sources: `{role: "logical"}`, `{role: "inventory"}`, `{role: "records_bundle"}` (unresolved) |
| `read_path` | Log-only: `pool=…`, `route=…` seen in the same trace (metadata; does not set `copy_key`) |
| `data_category`, `collection` | From table/collection/record_type |
| `presence` | `PRESENT` or `ABSENT` — only from explicit statements (row exists; `rows=0`, `404`, `NOT_FOUND`) |
| `identifiers` | `{email, user_id, email_hash, phone, name}`; observed null kept distinct from not observed |
| `attributes` | `enabled`, `restore_allowed`, `archive_present`, `retention_until`, `expires_at`, `account_status`, `last_write`, … |
| `observed_at` | Time at which the source asserts the state held (§5.5). Governs state selection |
| `last_modified_at` | Row `updated_at` (SQL) — attribute only, never used for ordering |
| `captured_at` | Manifest capture time of the evidence file |
| `evidence_id`, `source_kind`, `locator`, `trace_id`, `lookup_keys`, `raw_state`, `link_strength` | Provenance |

### 4.3 `Coverage` — an explicit enumeration declaration (revised; H-2c withdrawn)

A `Coverage` record exists only where participant-visible evidence declares that a source
enumerated a collection. Basis grades:

| Basis | Participant-visible declaration | Collection | Copy |
|---|---|---|---|
| `C1_DECLARED_COLLECTION` | JSON `snapshots[]` entry `{captured_at, data_store, collection, records}`; single-collection JSON captures `{captured_at, collection, records}` | explicit | unresolved (`records_bundle`) |
| `C2_TYPED_COPY_TABLE` | SQL table whose schema is one collection and whose name names the copy (`customer_profiles_primary`, `customer_profiles_failover_replica`) | from table | from table name |
| `C3_SCOPE_TABLE` | SQL `snapshot_scope(origin_system, captured_at)` + typed category tables | from table, system from scope | unresolved (`inventory`) |
| *(none)* | Generic `record_json` tables; logs; lookup ledgers; service lookup exports; text; PDF | — | — |

Every Coverage record carries `filter_scope = "unknown"`: no evidence file declares how rows were
selected. Snapshot size alone (3–89 rows against 850 users) does not establish that a snapshot is
filtered, nor what it would be filtered by; selection scope stays unknown unless evidence states it.

Omission from a covered enumeration yields `NOT_LISTED(basis, evidence_id)`, never `ABSENT`.
Whether a `NOT_LISTED` fact is admissible proof of absence for a given basis is a **Phase 2
policy decision**, measured against samples (e.g. `case_01` relies on C2, `case_31` on C1).

Generic `record_json` tables produce observations only, no coverage.
`evidence_05` still yields `account_994` PRESENT in the read replica, so sample `case_16` does not
depend on any coverage inference for the primary.

### 4.4 State selection per copy (store behaviour)

For cutoff `audit_at`, for each `(system_id, resource_id, copy_key)`:

1. Eligible facts: `captured_at <= audit_at` and `observed_at <= audit_at` (inclusive).
2. Facts: explicit observations plus `NOT_LISTED` derived from Coverage of that exact `copy_key`.
3. The fact with the latest `observed_at` wins. Equal `observed_at` with different states ⇒
   `CONFLICT` (both retained).
4. Result per copy: `PRESENT`, `ABSENT`, `NOT_LISTED(basis)`, `UNKNOWN`, or `CONFLICT`, each with
   the deciding evidence id(s).
5. The store returns the per-copy vector; it does not collapse it to a resource verdict. Collapse
   and admissibility rules belong to the Phase 2 policy engine.
6. A `logical` observation never changes a physical copy's state unless an approved mapping
   exists (H-2b, unresolved).

## 5. Ingestion design (Phase 1)

### 5.1 SQL snapshots (24 files)

- Load with stdlib `sqlite3` (`executescript`, in-memory). No regex parsing of SQL.
- Read header `Captured at` and `Database topology`, trailing `snapshot_completed_at`, and the
  `snapshot_scope` table; test that they agree with the manifest `captured_at`.
- Each table → one `copy_key`; each row → one `Observation` with `observed_at` = snapshot time,
  `last_modified_at` = row `updated_at`. Tables never merge.
- C2 coverage for typed copy tables; C3 for scope-declared inventory tables; none for generic tables.
- Store identity: by default (`strict`) a snapshot copy is scoped to its evidence file, so the same
  table name in two files is not assumed to be one copy (H-2d unresolved); `identity="h2d"` groups by
  `CopyKey` alone so H-2d can be evaluated in Phase 2.

### 5.2 Logs (26 files, ≥6 dialects) — strict trace joins (ACCEPTED)

- Dialects: `key=value`; JSON payload; RFC 5424 syslog; bracketed; pipe-delimited; cloud-runtime
  `message="…"`; line-record (`record_lookup seq=…`). Each tokenizer yields
  `LogEvent(file, line_no, ts, service, trace_id, event, fields)`.
- Group key `(evidence_id, trace_id)`; never across files (measured: 0 ids span files).
- A join is accepted only when the group has exactly one lookup identity and one resource id, the
  start precedes the outcome, no other start lies between them, and the outcome is an allowed
  terminal for that start: the same-stem `*_completed`, or an explicit pair from the
  `TERMINALS` allow-list in `ingest/logs.py` (e.g. `audit_record_query_started` →
  `audit_record_read_completed`, `message_scan_started` → `message_pii_scan`). *Implementation
  refinement:* the supplied logs pair 16 start events with differently-named terminals, so a
  shared-stem requirement alone would have rejected valid joins; the allow-list is explicit and tested.
- Multiple lookup identities or resources, or two starts with no outcome between them ⇒ `ambiguous`,
  no link, flagged. An outcome before any start, or with a non-allowed event pair ⇒ orphan, no link.
  A start whose lookup value is `null` ⇒ counted as `null_lookup`, no link (not ambiguous).
- Lookup fields: `lookup_key`, `match_key`, `scan_key`, `subject_key`, `owner_key`, `subject_ref`,
  `correlation_key` (and `query` in JSON lookup exports).
- Completed-only lines keep same-line identity only; started-only traces produce no state.
- `observed_at` = timestamp of the line carrying the outcome.
- `pool=` / `route=` are recorded as `read_path`; they do not establish a physical copy (H-2b).

### 5.3 JSON / text / PDF

- JSON detected by structure, not file name (bundles, service lookup exports, lookup ledgers,
  single-collection captures).
- Text handover: pipe-delimited lines; per-line time; emails case-normalized.
- PDF: **pypdf** (pure Python, cross-platform; `pdftotext` is a system binary, unavailable on
  stock Windows). Inspected all 4 PDFs: ReportLab-generated, text-extractable. pypdf, pdfminer and
  pdftotext all yield `submitted 18 Jun 2026 10` / `24 UTC` as separate cells because the PDF
  content stream itself has no colon (label/value split at `:` by the generator), so the parser
  must rejoin split `HH`/`MM UTC` times. Final dependency choice is ADR-004 (proposed).
- PNG: Phase 3.

### 5.4 Activity, identities, audit log, rules

CSV loaders. Activity rows are `ActivityClaim`s, never observations of state.

### 5.5 Timestamp semantics

| Source | `observed_at` (governs selection) | Kept as attribute only |
|---|---|---|
| SQL row | snapshot time (header / scope `captured_at`) | row `updated_at` → `last_modified_at` |
| SQL coverage | snapshot time of that table / scope row | — |
| JSON bundle record & coverage | that snapshot's own `captured_at` (varies inside one bundle, e.g. `evidence_02` holds 09-09, 09-15 and 09-17 snapshots) | — |
| JSON single-collection capture | top-level `captured_at` | `created_at`, `expires_at` |
| JSON service lookup export | per-row `observed_at` | `exported_at` = capture |
| JSON lookup ledger | per-row `recorded_at` | — |
| Log | timestamp of the outcome line | `last_write`, `retention_until` |
| Text handover | per-line timestamp | — |
| PDF | manifest `captured_at` (document as captured) | business dates (`recorded`, `retain until`, `issued`) |
| Activity claim | `event_time` | — |

Eligibility: `captured_at <= audit_at` **and** `observed_at <= audit_at`, inclusive (samples
`case_16`/`evidence_05` and `case_11`/`evidence_03` cite evidence captured exactly at `audit_at`).
Expected invariant `observed_at <= captured_at` **does not hold for 2 real facts** (correction of
an earlier claim that all post-capture timestamps were `retention_until`/`expires_at`):
`evidence_13` holds `account_1417` NOT_FOUND at 08-12T09:00 (its capture time) and FOUND recorded
08-14T09:00; `evidence_36` holds `account_5043` NOT_FOUND at 04-09T12:00 and FOUND at 04-11T12:00.
Both coincide with case cutoffs (`case_135` audit 08-12T09:00, `case_383` audit 04-09T12:00). The
facts are kept and flagged `observed_after_capture`; the fact-level gate excludes them at those
cutoffs. Whether they should ever be used is open question 14. One `eligible()` function in the store; the
validator re-checks every citation.

## 6. Build roadmap

| Phase | Scope | Gate |
|---|---|---|
| 1 — Ingestion | SQL, JSON, structured/unstructured logs, text, PDF parsers; observation + coverage store; one test per parser on real evidence | User review |
| 2 — Deterministic baseline | Identity resolver, evidence investigation, policy engine, planner, serializer; compare with 32 samples | User review |
| 3 — Intent and vision | Cached Claude intent classifier; PNG extraction; custom non-deletion tests | User review |
| 4 — Evaluation-driven improvement | One mismatch at a time; hypothesis, change, before/after metric, keep/revert | User review |
| 5 — Investigator agent | Bounded Claude tool loop on flagged cases; code-verified links | User review |
| 6 — Full dataset | 518 cases; schema/step validation; distribution, flagged and adversarial review | User review |
| 7 — Reporting and reproducibility | Evaluation and usage reports; fresh-run determinism; exact CLI | User review |
| 8 — Packaging | `code.zip`, `output.csv`, `log.txt`; final checks; interview prep | User review |

No phase starts without explicit authorization from the Lead Architect.

## 7. Proposed layout

```
code/main.py            CLI entry point
code/ingest/            sql.py, logs.py, json_records.py, text.py, pdf.py, vision.py, manifest.py
code/core/              model.py (Observation, Coverage, ActivityClaim), store.py, identity.py
code/agent/             intent.py, investigator.py, tools.py, prompts/
code/policy.py          rule application
code/planner.py         steps + ordering
code/validate.py        output contract validation
code/eval/              sample comparison, custom cases
code/evaluation/        evaluation_report.md, usage_report.md
code/tests/             pytest-style tests on real evidence
code/docs/              PLAN.md, DECISIONS.md
```

## 8. Model usage

Anthropic Claude; key only from `ANTHROPIC_API_KEY`. Three permitted responsibilities: intent
classification (structured output), screenshot fact extraction, bounded investigation with
tools `search_observations`, `resource_history`, `read_evidence_excerpt`, `resolve_identity`.
Investigator triggers: weak-only identity; replica conflict near cutoff; activity mentions a
resource with no eligible evidence; vision-dependent evidence; look-alike identifier; intent
classifier conflicts with a `CLASSIFY_REQUEST` event.

## 9. Non-negotiable invariants

- NOT_APPLICABLE ⇒ `steps_json=[]`.
- Never cite evidence captured after `audit_at`.
- Never delete policy-retained records; tombstones are redacted, not deleted.
- Disable flows and block restores before dependent record changes.
- Final status from tested policy code, never from the model.
- Participant-facing data only.
- Exact schema, operations, ordering and citation rules from `AGENTS.md` / `problem_statement.md`.

## 10. Open questions and hypotheses (unresolved; never implemented as rules without evidence)

1. When several files prove the same state, which evidence id is cited?
2. Replica disagreement and snapshot scopes — core resolved by ADR-002; coverage admissibility open.
3. What is the policy-compliant output when a security audit record is absent?
4. How are categories absent from the 32 samples handled?
5. When do downstream dependencies override default operation ordering?
6. How is vision-only evidence cited and validated?
7. **H-2b** — when may a logical API lookup (`pool=`, `route=`) update a physical copy?
8. **H-2c** — WITHDRAWN as a rule (counterexample: `evidence_52` vs `case_03`, see DECISIONS).
9. **H-2d** — the same table name in different snapshot files denotes the same physical copy
   (needed for a later snapshot to supersede an earlier one). Unverified.
10. Which `NOT_LISTED` bases (C1/C2/C3) are admissible proof of absence? (Phase 2, sample-measured)
11. Does `expires_at <= audit_at` imply an export is gone? (no, unless evidence shows it)
12. `account_001`-style links that exist only via activity timing near `reported_at` (Phase 2).
13. Are `customer_accounts_failover_replica` (`evidence_54`) and `customer_data_failover_replica`
    (`evidence_52`) the same physical failover? Treated as different copies until shown otherwise.
14. Facts recorded after their own file's capture time (`evidence_13`, `evidence_36`): usable once
    `audit_at` passes their `observed_at`, or never? Currently kept, flagged, and gated per fact.
15. 1,328 observations have no data category (logs and lookup exports state none; inventory
    `support_records` is ambiguous between message and attachment). Category resolution is Phase 2.
16. Name-only document facts (legal register, account history report) have no resource id and are
    kept as `DocumentExtract`s; linking them to resources is Phase 2.

## 11. Phase 1 test plan (ingestion + store only; `code/tests/`, stdlib `unittest`)

Real fixtures unless marked *synthetic*. Synthetic fixtures are only used where the dataset has
no instance of the behaviour (measured: no resource appears in more than one SQL file; no trace id
repeats across files).

| ID | Fixture | Assertions |
|---|---|---|
| SQL-1 | `evidence_54` | 2 copy keys (`customer_profiles/primary`, `customer_profiles/failover_replica`); 2 C2 coverage at 2026-09-02T23:00:00Z; `account_005` PRESENT only in failover, `observed_at` 23:00:00, `last_modified_at` 22:45:00; state at 2026-09-08T13:40Z: failover PRESENT, primary NOT_LISTED(C2); `account_001` NOT_LISTED in both, never ABSENT; `account_9116` email `m.rios.gil@outlook.com` |
| SQL-2 | `evidence_05` | `account_994` PRESENT in `customer_data/read_replica`; primary UNKNOWN (generic table ⇒ no coverage); `search_1007` PRESENT in both copies; captured exactly at `case_16` `audit_at` ⇒ eligible |
| SQL-3 | `evidence_52` | No coverage produced; `audit_record_2016` absent from both tables ⇒ UNKNOWN in both copies (H-2c regression guard) |
| SQL-4 | `evidence_33` | 3 copy keys with regions (`primary`, `replica/eu_west`, `replica/ap_south`); `account_6539` PRESENT only in `eu_west` |
| SQL-5 | `evidence_93` | 8 C3 coverage rows (copy `inventory`, unresolved); `backup_4978` `restore_allowed=1`, email `k.appiah88@gmail.com`; `audit_record_1738` `observed_at` 08:37:39, `last_modified_at` 06:06:38 |
| SQL-6 | `evidence_90` | Only `account_service` in scope; all tables empty ⇒ C3 declared-empty coverage for `account_service`, none for other systems |
| SQL-7 | all 24 SQL | header `Captured at`, `snapshot_completed_at`, `snapshot_scope` times agree with manifest |
| JSON-1 | `evidence_51` | 3 C1 coverage; `account_service/customer_profiles` declared empty; `tombstone_9178` identifiers present; `tombstone_440` identifiers observed-null |
| JSON-2 | `evidence_02` | Each record takes its own snapshot `captured_at` (09-09, 09-15T09, 09-15T12, 09-17), not the file time |
| JSON-3 | `evidence_92` | `account_5334` 200 ⇒ PRESENT with email only; `account_3136` 404 ⇒ ABSENT; per-row `observed_at`; no coverage |
| JSON-4 | `evidence_13`, `evidence_36` | Detected as lookup ledgers despite file names; `evidence_36` row `recorded_at` 2026-04-03 ≠ capture 04-09 |
| JSON-5 | `evidence_59` | `export_001` owner `gabriel.costa@proton.me`; `expires_at` attribute, not `observed_at` |
| LOG-1 | `evidence_94` | `rb-075713-004` joins `search_4262` ↔ `akosua.gyasi@gmail.com`; `account_2064` ABSENT (`rows=0`); `data_flow_4713` `enabled=false` |
| LOG-2 | `evidence_61` | `tr-rp-002` joins `support_002` ↔ `nadia.ionescu@yahoo.com`, `marker_found=false`; untraced `support_5914` line keeps only `nadia.popescu@yahoo.com`, no link to the other subject |
| LOG-3 | `evidence_16` | `tr-4069320` completed-only ⇒ same-line `correlation_key` link; `tr-2053433` start → `message_pii_scan` terminal; `tr-8311761` lookup key typed as resource id; `tr-6555380` uppercase email normalized |
| LOG-4 | `evidence_06/11/16/21/25/94` | One line per dialect parses to identical `LogEvent` fields |
| LOG-5 | *synthetic* (revised: no real started-only trace exists once non-`_completed` terminals are recognised) | Produces no state observation |
| LOG-6 | `evidence_08` | `account_8473` FOUND at 08-31 and NOT_FOUND at 2026-09-06T07:00:00Z; file captured 09-06T07:00:00Z = `case_86` `audit_at` ⇒ eligible and latest is ABSENT; cutoff 06:59:59 ⇒ whole file ineligible |
| LOG-7 | real trace with `pool=` + `route=` | Recorded as `read_path`; `copy_key` stays `logical` |
| LOG-8 | *synthetic* | (a) one trace, two lookup keys ⇒ ambiguous, no link; (b) repeated start/complete pairs interleaved ⇒ ambiguous; (c) completed before started ⇒ no join; (d) same trace id in two files ⇒ never joined; (e) mismatched stems ⇒ no join |
| TXT-1 | `evidence_03` | Pipe lines parsed; `CHOI.JIHOON94@GMAIL.COM` normalized; `analytics_2034` IDENTIFIABLE; `no direct identifier` ⇒ null |
| PDF-1 | `evidence_58` | `support_001`, `CS-4827`, `priya.raman@gmail.com`, `+91 98765 73142`; split time rejoined to 2026-06-18T10:24Z |
| PDF-2 | `evidence_55` | `audit_record_003`, `user_03`, retain-until 2027-08-25T04:03Z as attribute; `observed_at` = manifest capture |
| PDF-3 | `evidence_27` | 6 register rows (`user_08` … `user_29`), retain-until 2033 |
| PDF-4 | `evidence_18` | `user_18`, `ingrid.dahl@outlook.com`, order `ORD-20261260` |
| TIME-1 | `evidence_06` | Lines from 09-10 but captured 09-18 ⇒ ineligible for cutoff 2026-09-15T12:00Z |
| TIME-2 | *synthetic* (anchored on real `audit_record_1738` timings) | Snapshot at T2 lists row with `updated_at` T0; explicit ABSENT log at T1 (T0<T1<T2) ⇒ copy state at T2 is PRESENT |
| TIME-1b | `evidence_13` / `case_135` (added) | At audit 08-12T09:00 only the NOT_FOUND row is eligible; the 08-14 FOUND in the same file is excluded |
| TIME-3 | all ingested facts (revised) | Exactly the 2 known post-capture facts violate `observed_at <= captured_at`; both are flagged, none dropped; no coverage violates it |
| STORE-1 | `evidence_54` | Primary NOT_LISTED + failover PRESENT returned as a per-copy vector; never collapsed by the store |
| STORE-2 | `evidence_52`, `evidence_90` | Partial/empty snapshots yield UNKNOWN / NOT_LISTED, never ABSENT |
| STORE-3 | *synthetic* | Equal `observed_at`, different states ⇒ CONFLICT with both evidence ids |
| MAN-1 | `evidence_files.csv` | 94 entries; all paths exist; counts 24/28/26/1/4/11 |

## 12. Phase 1 implementation (2026-10-24)

| Module | Responsibility |
|---|---|
| `core/model.py` | `CopyKey`, `Observation`, `Coverage`, `TraceJoin`, `DocumentExtract`, `IngestIssue`, `CopyState` |
| `core/vocab.py` | system/collection vocabulary, identifier typing and extraction |
| `core/timeutil.py` | UTC timestamp parsing |
| `core/store.py` | `EvidenceStore`: inclusive `eligible()`, per-copy `copy_states()` (strict / `h2d` identity), `NOT_LISTED` derivation |
| `ingest/manifest.py` | manifest loader |
| `ingest/sql_snapshots.py` | sqlite3 execution; generic / typed-copy / inventory tables |
| `ingest/logs.py` | 6 dialect tokenizers, strict trace joins, log observations |
| `ingest/json_records.py` | bundles, captures, lookup exports, ledgers |
| `ingest/text_records.py` | handover text |
| `ingest/pdf_documents.py` | pypdf extraction, split-time rejoin, document extracts |
| `ingest/csv_inputs.py` | typed CSV loaders; activity kept as claims |
| `ingest/loader.py` | manifest-driven loading into the store |

Run tests from the repository root: `python3 -m unittest discover -s code/tests -t code`
(dependency: `pip install -r code/requirements.txt`, i.e. `pypdf==6.17.0`).

Measured ingestion of the supplied evidence (deterministic across runs):

| Item | Value |
|---|---|
| Observations | 2,896 (log 1,072, SQL 891, JSON 890, text 41, PDF 2) |
| Presence | PRESENT 2,636, ABSENT 245, not stated 15 |
| Coverage | 199 (C1 118, C3 78, C2 3); 8 declared-empty |
| Trace joins | 572 joins in 571 trace groups; 393 completed-only groups; 30 null-lookup groups; 0 ambiguous; 0 orphan outcomes |
| Log lines | 2,211 parsed, 0 unparsed; 1,139 carry no resource state (start lines, cache/connection/retry noise, router/system events) |
| Issues | 11 PNG `unsupported_in_phase1`; 2 `observed_after_capture` |

## 13. Phase 2 baseline (2026-10-24)

Pipeline per request (`code/audit.py`): subject identity → linked resources → category →
per-copy state → policy decision → ordered steps → contract validation. No model calls.

| Module | Responsibility |
|---|---|
| `core/identity.py` | `IdentityIndex` (current + historical email/phone/hash, sha256 hashes of every email), `link_resources` with per-link reasons |
| `core/categories.py` | category from explicit record type → collection → event/record shape → unambiguous id family |
| `core/resource_state.py` | `copy_class` (copy matching), `coverage_admissible` (H-COV), resource presence, exposure / flow / restore signals |
| `policy.py` | `deletion_rules.csv` actions → findings, remediation, verification per resource |
| `planner.py` | phase → operation-table order → `system_id` → `resource_id`; compact sorted-key JSON |
| `validate.py` | output contract checks (fields, phase/state per operation, numbering, order, citations, cutoff) |
| `eval/evaluate.py` | status, step P/R/F1, position-aligned field accuracy, exact sequence |
| `main.py` | `python3 code/main.py [--sample]` |

Resolved Phase 1 hypotheses (sample-measured, see DECISIONS ADR-006..008):

- **H-2b / H-2d → copy classes.** Explicitly named physical copies form one class per
  `(database, role, region, instance)` across files; all other sources form one service view.
  Logical reads update the service view only; they never clear a named physical copy.
- **H-COV → admissible with precedence.** C1/C2/C3 omission establishes absence only for a copy class
  with no explicit eligible observation of the resource.

Sample results (baseline): status 30/32 (0.9375); step precision 0.987, recall 0.782, F1 0.873;
exact sequence 8/32 (0.25). Field accuracy is position-aligned, so one missing early step shifts
all later fields: phase 0.782, system/resource 0.575, operation/state 0.622, evidence 0.788.

## 14. Phase 3: intent, screenshots, usage, export gate (2026-10-24)

| Module | Responsibility |
|---|---|
| `agent/llm.py` | `Claude` call layer: `claude-opus-5-5` only (automatic fallback disabled), JSON-schema structured output; content-addressed cache `code/cache/<purpose>/<sha256>.json`; `Ledger` with live-run vs historical-cached totals and unknown (never zero) cost for unpriced models; `ModelUnavailable` when no credentials |
| `agent/intent.py` | request-intent classification (effort `low`); code validation: label consistency + verbatim `evidence_quote` |
| `agent/vision.py` | screenshot transcription schema (records, identifiers, fields; effort `medium`) |
| `ingest/png_facts.py` | shape-validates every transcribed value; resource-id records → `document`-role observations at manifest capture time; others → document rows |
| `audit.py` | intent gate (NON_DELETION → NOT_APPLICABLE `[]`), `CLASSIFY_REQUEST` conflicts noted, confirmed links vs `activity_window` candidates, `unresolved` tracking, `export_blockers()` |
| `main.py` | provisional output, unresolved and usage reports in `code/eval/`; root `output.csv` only when no row is blocked; `--offline` |

Lead Architect decisions applied (GO PHASE 3):
1. Later-captured evidence is never used for discovery, identity linking or citations (H-DISC rejected).
2. Activity-window timing produces candidates only; exclusion of known owners does not establish ownership.
3. Unsupported COMPLETE rows (empty plan, uncited step, unresolved decision, no verified account) and rows
   with unresolved intent/subject are blocked from final export; they stay provisional, never auto-INCOMPLETE.

Measured (no credentials, so intent and screenshots unresolved): samples status 0.9375, step precision
0.987, recall 0.777, F1 0.870, exact 0.25; 0 sample contract violations. Targets: 518 provisional rows,
10 rows with contract violations (all uncited COMPLETE verifications), 518/518 export-blocked (intent).
Model usage this run: 0 calls (no credentials).

Revision (2026-10-24, second session start): automatic fallback removed; unknown pricing reported as
unknown; live-run and historical cached cost separated. Environment checks at session start:
`GHOST_DATA_ENV_CHECK == "ready"` False, `ANTHROPIC_API_KEY` non-empty False, so live intent and
screenshot calls remain blocked. 94 unittest cases pass.
