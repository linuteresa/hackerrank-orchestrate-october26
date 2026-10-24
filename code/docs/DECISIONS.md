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

- Status: **ACCEPTED** (Lead Architect, `GO STEP 1`, 2026-10-24)
- Decision: `observed_at` (time the source asserts the state) governs selection; SQL row
  `updated_at` is `last_modified_at` (attribute only); bundle records use their own snapshot's
  `captured_at`; logs use the outcome line time; ledgers/service exports use per-row times.
  Eligibility: `captured_at <= audit_at` and `observed_at <= audit_at`, inclusive.
- Evidence: inclusive boundary used by samples `case_16`/`evidence_05` and `case_11`/`evidence_03`.
  Bundles mix snapshot times (`evidence_02`: 09-09 … 09-17). Row `updated_at` precedes capture by
  0–3 h in all 888 SQL rows. No resource appears in more than one SQL file, so the
  "later snapshot with older `updated_at`" case is tested synthetically.
- Correction (Phase 1 implementation): two ledger facts are recorded *after* their file's capture
  time (`evidence_13` `account_1417` 08-14 vs capture 08-12; `evidence_36` `account_5043` 04-11 vs
  capture 04-09). An earlier statement that every post-capture timestamp was `retention_until` /
  `expires_at` was wrong for these two. They are kept, flagged `observed_after_capture`, and
  excluded at the coinciding cutoffs (`case_135`, `case_383`) by the fact-level gate. Open: H-TIME.
- Real boundary case: `case_86` (`audit_at` 2026-09-06T07:00:00Z) — `evidence_08` captured at
  exactly that instant holds `account_8473` FOUND (08-31) then NOT_FOUND (09-06T07:00:00Z).

## ADR-004 — PDF extraction dependency

- Status: **ACCEPTED** (Lead Architect, `GO STEP 1`, 2026-10-24). Pinned `pypdf==6.17.0` in `code/requirements.txt`.
- Decision: `pypdf` (pure Python, cross-platform). Alternative `pdftotext` (poppler binary) gives
  better layout but is not available on stock Windows.
- Evidence: all 4 PDFs are ReportLab-generated with extractable text; pypdf 6.17.0, pdfminer and
  pdftotext 24.02 all split `10:24` into `10` / `24 UTC` because the content stream has no colon.

## ADR-005 — Strict log trace joins

- Status: **ACCEPTED** (Lead Architect, 2026-10-24). Design in PLAN §5.2.

## ADR-006 — Identity links require a strong identifier; timing needs exclusive corroboration

- Status: **IMPLEMENTED (Phase 2 baseline)**, awaiting review.
- Links: record identifiers or lookup keys (email current/historical, phone digits, email hash
  incl. sha256 of every known email, user id); `LINK_RESOURCE_IDENTITY` activity; request-window
  account DELETE only if exactly one window account is unowned by anyone else and no account is
  linked otherwise. Names never link alone. Each link keeps `(kind, evidence_id, locator, detail)`.
- Refinement P2-2: a lookup-only link must be observed inside the request lifecycle
  (`observed_at >= requested_at`).

## ADR-007 — Category resolution order

- Status: **IMPLEMENTED (Phase 2 baseline)**. Explicit record type → collection → event/record shape
  (e.g. `attachment_pii_scan`, PDF `file`/`uploader` fields) → unambiguous id family. `analytics_*`
  and `support_*` families stay unresolved without other evidence. Conflicts stay unresolved.

## ADR-008 — Copy classes and omission precedence (resolves H-2b, H-2d, H-COV for the baseline)

- Status: **IMPLEMENTED (Phase 2 baseline)**, awaiting review.
- `copy_class`: named physical copies (`primary`, `*_replica`) → one class per
  `(database, role, region, instance)`; everything else → one service view. So a positive logical
  read shows the record remains available without naming a replica; an unmapped logical read cannot
  clear a named physical copy; a later snapshot of the same named table supersedes an earlier one.
- `coverage_admissible`: C1, C2, C3. Omission counts only for a class with no explicit eligible
  observation of the resource (P2-1).
- Resource PRESENT if any class's latest fact is PRESENT; ABSENT if every class with facts ends absent
  or not-listed (explicit absence cited before omission); else UNKNOWN.

## ADR-009 — Model call layer

- Status: **IMPLEMENTED (Phase 3)**. `claude-opus-5-5`; JSON-schema structured output; server-side
  refusal fallback `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`); refusal / truncation
  never parsed. Cache keyed by `sha256(model, purpose, prompt_version, input digest)`; each entry stores
  its usage so cached reruns can still report cost. Pricing constant: $4 / $20 per MTok in/out, $0.20
  cache read, $5 cache write (first-party list price, 2026-10). Credentials only from the environment.
- Missing credentials raise `ModelUnavailable` once per run; the dependent decision stays unresolved.

## ADR-010 — Intent classification

- Status: **IMPLEMENTED, not yet run live**. Model proposes `{authorizes_deletion, request_type,
  language, evidence_quote, confidence}`; code rejects inconsistent labels and non-verbatim quotes.
  NON_DELETION → `NOT_APPLICABLE` with `[]`. `CLASSIFY_REQUEST` activity is a claim: a conflict is
  recorded as a note, not resolved.

## ADR-011 — Screenshot extraction

- Status: **IMPLEMENTED, not yet run live**. Generic transcription schema; code keeps only shape-valid
  identifiers and resource ids; observations are a `document`-role copy at manifest capture time, so
  both approved cutoffs apply. Screen record kinds map to categories by keyword (one match only).

## ADR-012 — Confirmed links vs uncertain candidates (reconciles the Phase 2 discrepancy)

- Status: **IMPLEMENTED (Lead Architect decision 2)**. The Phase 2 code still adopted an exclusive
  window account as a link (`_window_account`) although the report called the experiment unadopted;
  that path is removed. Activity-window matches are `candidates` with reason `activity_window`, never
  produce steps, and become links only through an independent confirmed link. Measured: recall
  0.7824 → 0.7772 (one sample step lost), precision unchanged.

## ADR-013 — Export gate

- Status: **IMPLEMENTED (Lead Architect decision 3)**. `CaseResult.export_blockers()`: unresolved
  intent or subject; COMPLETE with no steps, an uncited step, an unresolved decision, or no verified
  account; any contract violation. Blocked rows stay in `code/eval/provisional_*.csv`; the root
  `output.csv` is written only when no row is blocked.

---

## Hypotheses (unresolved)

| ID | Hypothesis | Status | Evidence so far |
|---|---|---|---|
| H-2b | A logical API lookup can update a physical copy when a mapping is established (`pool=`, `route=` in the same trace) | **Baseline: no** (ADR-008; logical reads update the service view only) | Logs carry `pool=<system>_primary` / `<system>_replica` (e.g. 20/25 account pools) and `route=primary` after retries; `replica` pools do not name *which* replica |
| H-2c | A generic table covers every `(system, collection)` seen in any copy table of the same file | **NOT APPROVED → WITHDRAWN** | Counterexample below |
| H-2d | The same table name in different snapshot files is the same physical copy | **Baseline: yes for named physical copies** (ADR-008; no resource appears in two SQL files, so samples cannot falsify it) | Needed for supersession. Store default is `strict` (copies scoped per evidence file); `identity="h2d"` mode exists for Phase 2 evaluation (STORE-3 shows the two modes diverge) |
| H-COV | `NOT_LISTED` under C1/C2/C3 is admissible proof of absence | **Baseline: yes, below explicit facts** (ADR-008, P2-1) | Samples `case_01/02/04` (C2, `evidence_54`) and `case_31` (C1, `evidence_51`) cite omission as ABSENT. No evidence declares any snapshot's selection scope; snapshot size alone does not establish filtering, so `filter_scope` is recorded as unknown |
| H-TIME | Facts recorded after their own file's capture time may be used once `audit_at >= observed_at` | **Unresolved; no sample decision affected** |
| H-DISC | Resource *discovery* may use facts with `observed_at <= audit_at` from files captured later (citations unchanged) | **REJECTED** by the Lead Architect (GO PHASE 3) despite +2 sample steps: both cutoffs stay for discovery, linking and citation | 2 real instances (`evidence_13`, `evidence_36`); both sit at case cutoffs where the fact-level gate excludes them |

### H-2c counterexample (2026-10-24; framing corrected at `GO STEP 1`)

The counterexample shows that H-2c's absence inference is **unsupported by coverage evidence**; it
does not establish that the record existed at the earlier snapshot.

`evidence_52` (`customer_data_failover_replica_20260920.sql`, captured 2026-09-20T12:00Z) has
generic tables holding 8 `security_audit_events` and 8 `deleted_customer_references` rows per copy.
Nothing in the file declares that either copy table enumerates the whole collection, or how its
rows were selected. H-2c would nevertheless treat both copies as covering
`security_service/security_audit_events` and infer `audit_record_2016` (subject `user_43`)
ABSENT from both at 09-20 purely from omission.

Why that inference is unsafe:
- The omission has no declared coverage behind it, so it supports no conclusion about
  `audit_record_2016` at 09-20 — neither absent nor present.
- No activity row mentions `audit_record_2016`, so there is also no claimed deletion to corroborate.
- Sample `case_03` (`audit_at` 09-26T11:00Z) requires `VERIFY_RECORD_PRESENT security_service
  audit_record_2016 evidence_94`, citing a logical read on 09-26 (`rows=1`). That later read does not
  prove presence at 09-20. But with H-2b unresolved it also cannot overwrite a physical-copy state,
  so an unsupported ABSENT inferred from `evidence_52` would persist and contradict the required
  verification of a retention-required record.

The same unsupported inference would apply to `audit_record_1738` (listed in `evidence_93` at 09-16,
omitted from `evidence_52`). Result: H-2c withdrawn; generic tables produce no coverage, and the
affected copies are `UNKNOWN` (regression test SQL-3).

## Experiment log

| ID | Hypothesis | Change | Before | After | Keep/Revert |
|---|---|---|---|---|---|
| P1-1 | Shared-stem start/terminal pairing is sufficient for joins | Replaced by explicit `TERMINALS` allow-list (same stem still allowed) | stem-only: `message_scan_started`→`message_pii_scan` etc. unjoinable | 572 joins, 0 ambiguous, 0 orphan outcomes on supplied logs | Keep (tested by LOG-3, LOG-8e) |
| P1-2 | `lookup_key=null` starts are ambiguous traces | Counted as `null_lookup`, no link, no issue | 36 false "ambiguous" issues (30 null + 6 `owner_key` not yet recognised) | 0 ambiguous; `owner_key` added as lookup field | Keep |
| P1-3 | Every in-file timestamp after capture is a semantic date | Store flags `observed_after_capture` | assumption untested | 2 real violations found and flagged (TIME-3) | Keep flagging; usage open (H-TIME) |
| P2-0 | First end-to-end baseline | — | — | status 0.906, P 0.866, R 0.668, exact 0.031; 11 contract violations (future steps cited evidence) | Fix bug |
| P2-1 | Omission must not override an explicit observation of the same class | NOT_LISTED skipped for classes with explicit facts | `case_24` account_5334 ABSENT (wrong) | PRESENT, cites evidence_92 | Keep |
| P2-2 | Lookup links outside the request lifecycle are not this request's | lookup-only links need `observed_at >= requested_at` | `case_17` extra account_6523 (March lookup) | removed | Keep |
| P2-0b | INCOMPLETE future steps must not cite | blank evidence for remediation/verification when findings exist | 11 violations | 0 violations; with P2-1, P2-2: status 0.9375, P 0.987, R 0.782, F1 0.873, exact 0.25 | Keep |
| P2-3 | Discovery by record time (H-DISC) | `discoverable = observed_at <= audit_at` (experiment only) | R 0.7824 | R 0.7927, P 0.9871 (`case_09` gains `account_3960`, `audit_record_4902`) | Not adopted — needs your decision |
| P2-4 | Wide request-window linking with exclusion | all deletion-worker targets in `[requested_at, reported_at]` minus resources owned by others | — | 44 right vs 336 wrong candidates | Rejected |
| P2-5 | Narrow batch window (3 h before `reported_at`) + exclusion, accounts only | experiment only | — | 8 right, 0 wrong, 19 ambiguous, 5 none | Rejected: exclusion is not ownership (decision 2) |
| P3-1 | Timing-only account link must not be a confirmed link | `_window_account` removed; timing → candidates | R 0.7824 | R 0.7772, P 0.9868 | Keep (decision 2) |
| P3-2 | Unsupported COMPLETE rows must not be exported | export gate | output.csv written unconditionally | samples 32/32, targets 518/518 blocked (intent unresolved without credentials); rows blocked for other reasons: samples 11, targets 129 | Keep |

## Phase 1 decision record (2026-10-24)

Lead Architect `GO STEP 1` approved: separate observations per evidenced physical copy; internal
`NOT_LISTED` / `UNKNOWN` / `CONFLICT`; snapshot observation time separate from row
`last_modified_at`; inclusive cutoffs; pypdf; the 32-test plan. Documentation corrections required
and applied: (1) selection/filter scope is unknown unless evidence establishes it — snapshot size
does not prove filtering; (2) the H-2c counterexample is framed as insufficient coverage evidence
and unsupported absence inference, not as proof of historical presence. H-2b, H-2d and H-COV stay
unresolved; ingestion preserves `copy_key`, `read_path`, `evidence_scope`, coverage basis and
`filter_scope` so Phase 2 can evaluate them. `NOT_LISTED` is never converted to `ABSENT`.

Measured: 62 unittest cases pass (`python3 -m unittest discover -s code/tests -t code`), covering
all 32 plan IDs plus TIME-1b, an UNKNOWN-state check and CSV-1.
