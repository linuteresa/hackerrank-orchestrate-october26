# PLAN — Ghost Data Still Exists deletion-audit agent

Status: **Phase 1 design checkpoint — revision 2 (2026-10-24).** ADR-002 core accepted; H-2c not approved; awaiting `GO STEP 1`. No implementation code exists yet.
Owner of decisions: Lead Architect (user). Implementation partner: Claude Code.
Companion file: `code/docs/DECISIONS.md` (ADRs and measured experiments).

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

Every Coverage record carries `filter = "unknown"`: no evidence file declares *how* rows were
selected, and row counts (3–89 rows vs 850 users) show all snapshots are subject-filtered extracts.

Omission from a covered enumeration yields `NOT_LISTED(basis, evidence_id)`, never `ABSENT`.
Whether a `NOT_LISTED` fact is admissible proof of absence for a given basis is a **Phase 2
policy decision**, measured against samples (e.g. `case_01` relies on C2, `case_31` on C1).

Generic `record_json` tables produce *collection sightings* only (informational, no coverage).
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

### 5.2 Logs (26 files, ≥6 dialects) — strict trace joins (ACCEPTED)

- Dialects: `key=value`; JSON payload; RFC 5424 syslog; bracketed; pipe-delimited; cloud-runtime
  `message="…"`; line-record (`record_lookup seq=…`). Each tokenizer yields
  `LogEvent(file, line_no, ts, service, trace_id, event, fields)`.
- Group key `(evidence_id, trace_id)`; never across files (measured: 0 ids span files).
- A join is accepted only when the group has exactly one lookup identity and one resource id, the
  start precedes the terminal event, and both share the operation stem. Terminal events include
  `*_completed`, `message_pii_scan`, `deletion_marker_check`, `analytics_profile_read`.
- Repeated starts in one group, interleaved stems, completed-before-started, or multiple
  identities/resources ⇒ `ambiguous`, no link, flagged.
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
Invariant: `observed_at <= captured_at` for every fact (measured: all in-file timestamps later
than capture are `retention_until`/`expires_at`). One `eligible()` function in the store; the
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
| LOG-5 | real started-only trace (one of the 87, chosen at implementation) | Produces no state observation |
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
| TIME-3 | all ingested facts | `observed_at <= captured_at` for every fact |
| STORE-1 | `evidence_54` | Primary NOT_LISTED + failover PRESENT returned as a per-copy vector; never collapsed by the store |
| STORE-2 | `evidence_52`, `evidence_90` | Partial/empty snapshots yield UNKNOWN / NOT_LISTED, never ABSENT |
| STORE-3 | *synthetic* | Equal `observed_at`, different states ⇒ CONFLICT with both evidence ids |
| MAN-1 | `evidence_files.csv` | 94 entries; all paths exist; counts 24/28/26/1/4/11 |
