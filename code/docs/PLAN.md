# PLAN — Ghost Data Still Exists deletion-audit agent

Status: **Phase 1 design checkpoint (not approved).** No implementation code exists yet.
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

## 4. Observation model (proposed revision — see ADR-002, pending approval)

Two record kinds, not one:

### 4.1 `Observation` — something a source positively reported about one physical copy

| Field | Meaning |
|---|---|
| `system_id` | Logical system (`account_service`, `search_service`, …) |
| `resource_id` | Logical resource (`account_005`) |
| `copy_id` | Physical copy: `primary`, `failover_replica`, `read_replica`, `reporting_replica`, `replica_eu_west`, `replica_ap_south`, `support_replica`, … or `logical` when the source reports the service's answer without naming a copy |
| `data_category` | From collection/table/record_type, e.g. `USER_PROFILE` |
| `presence` | `PRESENT` / `ABSENT` / `UNKNOWN` |
| `identifiers` | `{email, user_id, email_hash, phone, name}` as observed (null = observed null; missing = not observed) |
| `attributes` | Category-specific facts: `enabled`, `restore_allowed`, `archive_present`, `retention_until`, `account_status`, `expires_at`, `download_available`, … |
| `observed_at` | When the source says the state held (row `updated_at`, log line time, `recorded_at`, `observed_at`) |
| `captured_at` | Evidence capture time from `evidence_files.csv` |
| `evidence_id` | `file_id` (or `activity_id` / `audit_id` for CSV-derived facts) |
| `source_kind` | `sql`, `json`, `log`, `text`, `pdf`, `png`, `activity` |
| `locator` | File path + table/line/JSON pointer for audit and excerpting |
| `trace_id`, `lookup_keys` | Log provenance |
| `raw_state` | Original raw fields (e.g. `rows=0 http_status=404`) |
| `link_strength` | How the identity was attached: `in_record`, `trace_join`, `same_line`, `none` |

Ingestion records *facts*, not treatments. Whether a present record with null identifiers counts as
`REDACTED` or `ANONYMIZED` is a policy decision made later from `data_category` + rules.

### 4.2 `Coverage` — what a snapshot was able to see

| Field | Meaning |
|---|---|
| `evidence_id`, `captured_at` | Source snapshot |
| `system_id`, `copy_id`, `collection` | The physical copy and collection the snapshot enumerates in full |
| `scope_source` | `snapshot_scope_table`, `topology_header+table_name`, `bundle_collection` |

Absence of a resource from a covered copy is a provable fact (sample `case_01`: `account_001`
never appears in any file, yet `VERIFY_RECORD_ABSENT` cites `evidence_54`). Absence is derived
at query time from `Coverage`, never materialized for every possible resource id.

Coverage scope rule for generic multi-collection tables (`record_json` tables), hypothesis
**H-2c**: a file's copies jointly cover the set of `(system_id, collection)` pairs that appear
in *any* copy table of that file. Example: in `evidence_05` the primary table has zero
`account_service` rows but the read replica has one, so both copies cover
`account_service/customer_profiles` and the primary's absence of `account_994` is proven.
Counter-example it prevents: `evidence_52` (`customer_data_failover_replica_20260920.sql`)
contains only `security_service` and `identity_service` rows, so it must **not** be read as
proving every account absent from the failover copy.

### 4.3 State selection (per physical copy)

For a case with cutoff `audit_at`:

1. Eligible evidence: `captured_at <= audit_at` **and** `observed_at <= audit_at`.
2. For each `(system_id, resource_id, copy_id)`, the latest eligible fact wins, where a fact is
   an `Observation` of that copy or a `Coverage` record of that copy (implying ABSENT if the
   resource is not listed).
3. A `logical` observation (service API lookup) supersedes only the `logical`/`primary` view. It
   never clears a replica copy. *Hypothesis H-2b, to be tested.*
4. Resource-level state is an aggregation over copies: the resource is compliant only if every
   known copy is compliant. Any non-compliant copy makes the resource non-compliant.
5. The output still names only `(system_id, resource_id)`; `copy_id` drives the decision and the
   citation, not the output row.

## 5. Ingestion design (Phase 1 proposal)

### 5.1 SQL snapshots (24 files)

- Load each file into an in-memory `sqlite3` connection with `executescript` (stdlib; JSON1
  `json_valid` is available). No regex parsing of SQL.
- Read header comments `Captured at` and `Database topology`, trailing `snapshot_completed_at`,
  and the `snapshot_scope` table when present.
- Table name → `copy_id`: `*_primary` → `primary`; `*_failover_replica`, `*_read_replica`,
  `*_reporting_replica`, `*_replica_<region>` → that copy. Inventory files (no topology header,
  per-category tables + `snapshot_scope`) → `copy_id = "inventory"` pending ADR (open question Q2).
- Each table produces its own `Coverage` row; each row produces its own `Observation`.
  Different tables never merge, so a failover copy cannot be overwritten by a primary copy.
- Generic tables (`resource_id, origin_system, collection_name, record_json, updated_at`) are
  decoded by collection-specific field maps; typed inventory tables map columns directly.

### 5.2 Logs (26 files, 6+ dialects)

Dialects seen: `key=value`; JSON payload after timestamp; RFC 5424 syslog; bracketed
`[LEVEL] [service] [trace] event:`; pipe-delimited; cloud-runtime `message="…"` with escaped
inner key=values; plus line-record logs (`record_lookup seq=…`). One tokenizer per dialect,
all yielding `LogEvent(file, line_no, ts, service, trace_id, event, fields)`.

Trace grouping and joins:

- Group key is `(evidence_id, trace_id)`; never across files. (Measured: 0 trace ids span files.)
- Within a group, events are ordered by line number. The lookup identity comes from any of
  `lookup_key`, `match_key`, `scan_key`, `subject_key`, `correlation_key`, `matched_value`;
  the resource and outcome come from `*_completed` or terminal events (`message_pii_scan`,
  `deletion_marker_check`, `analytics_profile_read`, …).
- A join is accepted only if the group has exactly one lookup identity and exactly one
  resource id, the started event precedes the completed event, and both share the operation
  stem (`profile_lookup_started` ↔ `profile_lookup_completed`). Otherwise the group is marked
  `ambiguous` and produces no identity link (flag for investigator).
- Incomplete traces (measured across all logs: 514 started+completed, 365 completed-only,
  87 started-only, 28 neither): completed-only lines keep only same-line identity (e.g.
  `correlation_key`); started-only traces produce no state observation.
- Lines without a trace id are self-contained (e.g. `support_replica.log` line for
  `support_5914 lookup_key=nadia.popescu@yahoo.com`, a look-alike of a sample subject).
- Lookup keys are typed by pattern: email, `user_NN`, resource id, or hash; email comparison is
  case-insensitive (`OBI.ADAEZE@ICLOUD.COM`).
- Connection pools / regions (`pool=search_service_replica`, `region=…`) are metadata, **not**
  copies, unless the emitting service is itself a copy (`service=support_replica`).

### 5.3 JSON / text / PDF

- JSON shapes: snapshot bundles (`snapshots[].data_store/collection/records`, each a `Coverage`),
  service resource exports (`service_exports.<system>.<collection>[]` with per-row
  `observed_at`, `status_code`), record-lookup ledgers (`records[]` with `recorded_at`,
  `result`, `subject_reference`), and collection captures (`captured_at`, `records[]`).
  File names are not trusted for content type (e.g. `connector_configurations_20260812.json`
  contains generic record lookups).
- Text handover: pipe-delimited `time | system | resource | category | result | subject` lines.
- PDF: stdlib-only extraction is not reliable; proposal is `pypdf` (lightweight) or
  `pdftotext` if available, decided at implementation time and recorded as an ADR.
- PNG: Phase 3 (Claude vision), not Phase 1.

### 5.4 Activity, identities, audit log, rules

CSV loaders with typed rows. Activity rows are claims: they become `ActivityClaim` records, not
`Observation`s, except where the evidence rules explicitly allow (e.g. `LINK_RESOURCE_IDENTITY`
as an identity link, `CLASSIFY_REQUEST` as a classification claim).

### 5.5 Time enforcement

- One function `eligible(fact, audit_at)` in the store's query layer; every query from
  identity, investigation, policy and planner goes through it. No module reads raw files later.
- File-level gate: `captured_at <= audit_at` (contract: never cite evidence captured after
  `audit_at`). Fact-level gate: `observed_at <= audit_at`.
- Semantic future timestamps (`retention_until`, `expires_at`) are attributes, not observation
  times. Measured: every in-file timestamp later than its file's `captured_at` is one of these.
- Activity: `event_time <= audit_at`.
- Validator re-checks every cited `evidence_id` against the cutoff before writing output.

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

## 10. Open questions (unresolved; do not convert to rules without evidence)

1. When several files prove the same state, which evidence id is cited?
2. How are replica disagreement and snapshot scopes represented and evaluated? *(ADR-002 proposal)*
3. What is the policy-compliant output when a security audit record is absent?
4. How are categories absent from the 32 samples handled?
5. When do downstream dependencies override default operation ordering?
6. How is vision-only evidence cited and validated?
7. *(new)* Does a later `logical` lookup (API 404) supersede an earlier replica PRESENT? (H-2b)
8. *(new)* What `copy_id` do inventory SQL snapshots and JSON bundles represent?
9. *(new)* Does `expires_at <= audit_at` imply absence of an export, or only that it should be absent?
10. *(new)* Some resources (e.g. `account_001`) are linked to a subject only through
    `system_activity` timing near `reported_at`; is that an admissible identity link? (Phase 2)
