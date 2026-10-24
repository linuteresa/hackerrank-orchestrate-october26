# DECISIONS — Architecture Decision Records and Experiments

Format: each ADR states context, decision, status, evidence and measured results.
Status values: `PROPOSED`, `ACCEPTED`, `NOT APPROVED`, `WITHDRAWN`, `REJECTED`, `SUPERSEDED`.
Hypotheses are tracked separately from decisions and are never implemented as rules until
evidence and the Lead Architect accept them.

---

## ADR-001 — Deterministic core, model only for interpretation

- Status: **ACCEPTED** (Lead Architect handoff, 2026-10-24)
- Decision: final statuses and steps come from tested code. Claude is limited to intent
  classification, screenshot extraction and bounded investigation of flagged cases; every
  model-proposed link is verified in code. Key from `ANTHROPIC_API_KEY` only.

## ADR-002 — Per-physical-copy observations with separate Coverage records

- Status: **ACCEPTED (core only)** — Lead Architect, 2026-10-24.
- Accepted: key `(system_id, resource_id, copy_key)`; separate `Coverage`; a compliant primary
  must not hide a non-compliant replica; preserve region/instance/database identity where
  evidence distinguishes copies; leave inventory and logical→physical mappings unresolved where
  provenance is unclear; omission is never stored as ABSENT.
- Not accepted as part of ADR-002: H-2b, H-2c, H-2d (below).
- Evidence:
  - `evidence_54`: `account_005` only in `customer_profiles_failover_replica`; `activity_3482`
    DELETE SUCCESS and `activity_3483` SYNC SUCCESS are claims. Sample `case_05` INCOMPLETE,
    `REPORT_REMAINING_RECORD account_005 evidence_54`.
  - `evidence_05`: `account_994` only in `customer_data_read_replica`. Sample `case_16` INCOMPLETE.
  - Presence-drift scan over the 12 multi-copy SQL files: 13 resources differ; all are accounts
    present in exactly one replica and absent from the primary.
- Alternatives rejected: latest-per-resource (fails `case_05`, `case_16`).

## ADR-003 — Timestamp semantics

- Status: **PROPOSED (revision requested by Lead Architect, 2026-10-24)**
- Decision: `observed_at` (time the source asserts the state) governs selection; SQL row
  `updated_at` is `last_modified_at` (attribute only); bundle records use their own snapshot's
  `captured_at`; logs use the outcome line time; ledgers/service exports use per-row times.
  Eligibility: `captured_at <= audit_at` and `observed_at <= audit_at`, inclusive.
- Evidence: inclusive boundary used by samples `case_16`/`evidence_05` and `case_11`/`evidence_03`.
  Bundles mix snapshot times (`evidence_02`: 09-09 … 09-17). Row `updated_at` precedes capture by
  0–3 h in all 888 SQL rows. No resource appears in more than one SQL file, so the
  "later snapshot with older `updated_at`" case is tested synthetically.
- Real boundary case: `case_86` (`audit_at` 2026-09-06T07:00:00Z) — `evidence_08` captured at
  exactly that instant holds `account_8473` FOUND (08-31) then NOT_FOUND (09-06T07:00:00Z).

## ADR-004 — PDF extraction dependency

- Status: **PROPOSED**
- Decision: `pypdf` (pure Python, cross-platform). Alternative `pdftotext` (poppler binary) gives
  better layout but is not available on stock Windows.
- Evidence: all 4 PDFs are ReportLab-generated with extractable text; pypdf 6.17.0, pdfminer and
  pdftotext 24.02 all split `10:24` into `10` / `24 UTC` because the content stream has no colon.

## ADR-005 — Strict log trace joins

- Status: **ACCEPTED** (Lead Architect, 2026-10-24). Design in PLAN §5.2.

---

## Hypotheses (unresolved)

| ID | Hypothesis | Status | Evidence so far |
|---|---|---|---|
| H-2b | A logical API lookup can update a physical copy when a mapping is established (`pool=`, `route=` in the same trace) | **Unresolved** | Logs carry `pool=<system>_primary` / `<system>_replica` (e.g. 20/25 account pools) and `route=primary` after retries; `replica` pools do not name *which* replica |
| H-2c | A generic table covers every `(system, collection)` seen in any copy table of the same file | **NOT APPROVED → WITHDRAWN** | Counterexample below |
| H-2d | The same table name in different snapshot files is the same physical copy | **Unresolved** | Needed for supersession; untested |
| H-COV | `NOT_LISTED` under C1/C2/C3 is admissible proof of absence | **Unresolved (Phase 2)** | Samples `case_01/02/04` (C2, `evidence_54`) and `case_31` (C1, `evidence_51`) cite omission as ABSENT; all snapshots are subject-filtered extracts |

### H-2c counterexample (2026-10-24)

`evidence_52` (`customer_data_failover_replica_20260920.sql`, captured 2026-09-20T12:00Z) has
generic tables with 8 `security_audit_events` + 8 `deleted_customer_references` rows per copy.
H-2c would declare both copies to cover `security_service/security_audit_events`.
`audit_record_2016` (subject `user_43` = `l.fontaine46@yahoo.com`) is not in either table, so H-2c
infers ABSENT in primary and failover at 09-20. But `evidence_94` (captured 09-26T10:38:56Z) shows
`audit_record_read_completed resource_id=audit_record_2016 rows=1 retention_until=2034-09-30`, no
activity row mentions `audit_record_2016`, and sample `case_03` (`audit_at` 09-26T11:00Z) requires
`VERIFY_RECORD_PRESENT security_service audit_record_2016 evidence_94`. With per-copy selection and
H-2b unresolved, the logical 09-26 read cannot overwrite the physical copies, so H-2c would report
a retention-required record as missing. Same pattern: `audit_record_1738` present in `evidence_93`
(09-16) and absent from `evidence_52` with no deletion activity.

## Experiment log

| ID | Hypothesis | Change | Before | After | Keep/Revert |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
