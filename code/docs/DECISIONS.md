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

- Status: **IMPLEMENTED (Phase 3); revised 2026-10-24 per Lead Architect**. `claude-opus-5-5` only,
  plain `messages.create` with JSON-schema structured output. **Automatic model fallback disabled**
  (no `fallbacks`, no fallback beta): a refusal, a response served by any other model, a truncated
  response or unparseable JSON is billed and recorded in the ledger, then rejected
  (`ModelOutputUnusable`) and the dependent decision stays unresolved; rejected output is never cached.
- Cache keyed by `sha256(model, purpose, prompt_version, input digest)`; each entry stores the usage
  of the call that produced it.
- Cost: `Usage.cost()` returns `None` for a model without a known price; any aggregate containing an
  unpriced call reports `cost_usd: null` with `unpriced_calls`, never zero or a partial sum.
  `Ledger.totals()` separates `live_run` (calls made in this run) from `historical_cached` (the
  recorded cost of cache entries reused by this run). Price constant: $4 / $20 per MTok in/out,
  $0.20 cache read, $5 cache write (first-party list price, 2026-10).
- Credentials only from the environment; missing credentials raise `ModelUnavailable` once per run.

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

## ADR-014 — Manually triggered CI run

- Status: **IMPLEMENTED** (`.github/workflows/audit-run.yml`, `workflow_dispatch` only, `contents: read`).
- `ANTHROPIC_API_KEY` comes only from the repository Actions secret and is injected into the environment
  of the credential check, smoke test, sample run and target run; never printed, no header or environment
  dumps, no `set -x`.
- Order: offline unit tests → boolean credential check → live smoke test (intent + cache reuse + one
  screenshot) → samples → targets. Later steps run only if the smoke test passes.
- Export gate unchanged: `main.py` exit 2 is reported as "export blocked"; artifacts upload with
  `if: always()` from an explicit path list, including `output.csv` only when the gate wrote it.
- Revision (approved): the upload also includes `code/cache/intent/*.json` and `code/cache/vision/*.json`
  (model outputs + usage metadata); `code/cache/` is git-ignored. A pre-upload scan of exactly the
  selected files fails the upload on the secret value or credential-like strings (file names only are
  printed). Local test with fake values found and fixed a bug: a missing `output.csv` made `grep` exit 2
  and masked a real match; the scan now lists only existing files.
- Verified locally: YAML parses, embedded Python compiles, secret referenced only by name in five steps;
  smoke script without credentials exits 1 with sanitized reasons and writes no cache.
- GitHub run #1 (2026-10-24T16:07Z, commit `efcd923`, triggered by the repository owner): unit tests passed;
  credential check passed (secret present, masked); live smoke test failed with
  `credentials rejected: AuthenticationError` for both intent and vision - 0 live calls, $0. The API
  rejected the stored key; no code change can fix this. Sample and target steps were skipped as designed.
- GitHub run #2 (id 40110467404, 2026-10-24T16:12Z, commit `03453eb`, triggered by the repository owner):
  all steps succeeded. Smoke: intent live then cached, screenshot extracted. Samples (live intent v1):
  status 0.75, P 1.0, R 0.679, exact 0.219 - seven labelled deletion follow-ups (`case_12`, `case_15`,
  `case_21`, `case_25`-`case_28`: "verify that my account was deleted", "check nothing was left behind")
  were classified non-deletion. Targets: 0 unresolved intents; 115 rows blocked (114
  `complete_without_account_verification`, 7 `complete_with_unresolved`, 6 `complete_without_steps`).
  Usage: samples 41 live calls / $0.5372; targets 366 live calls, 285,674 in / 21,712 out tokens, $1.5769
  (+163 cache hits worth $1.0956 recorded earlier in the same run). Credential scan: 418 files clean.
  The artifact zip could not be downloaded into the development container (artifact storage host denied
  by the container's network policy), so per-case outputs were not inspected locally.
- Revision after run #2: `code/cache/` is restored from and saved to the Actions cache (key per run, restore
  by prefix, saved only after the credential scan passes), so unchanged inputs are not paid for again; the
  smoke test accepts cache hits; `main.py` prints status counts and the non-deletion case list; the custom
  evaluation cases run after the target step.

## ADR-015 — Intent prompt v2 (deletion follow-ups)

- Status: **IMPLEMENTED**, measured in the next CI run. Evidence: run #2 above - the v1 prompt listed only
  new deletion requests as DELETION, so follow-ups asking to verify, confirm, audit or finish the
  requester's own deletion fell to OTHER. The labelled samples treat all of them as deletion cases.
- v2 states that such follow-ups (and requests to remove remaining copies) carry the deletion through,
  and that a text asking to keep the account or saying it is not a deletion request never is.
- `PROMPT_VERSION` is `intent-v2`, so v1 cache entries are not reused for decisions. Code-side validation
  (label consistency, verbatim quote) is unchanged.
- Measured in CI run #3 (id 40111949889, commit `a6c63f8`): samples status 0.969, P 1.0, R 0.803, exact
  0.281, 32/32 deletion; targets 166 COMPLETE / 342 INCOMPLETE / 10 NOT_APPLICABLE (case_533-542, each
  an explicit correction, opt-out, export, access or other request), 0 unresolved intents, 115 rows
  blocked. Run #4 (id 40113277715, commit `72832f6`) replayed every model output from the Actions cache:
  0 live calls, $0, identical results, and listed every blocked case in the job log.

## ADR-016 — Export blocked: decision needed (PROPOSED, not implemented)

115 target rows block the export (run #4):

- 108 COMPLETE rows whose only blocker is the account-proof guard: no account record of the subject
  appears in any evidence captured by `audit_at`. The account ID is known only from deletion-worker
  activity timing, which was measured unreliable (P3-6). In the local offline analysis without
  screenshot facts (126 such rows), 78 had only a retained audit record linked by `subject_reference`
  and 93 had five or more candidate accounts in the request window.
- 6 rows with the guard plus an unresolved decision: case_39 `data_flow_1121` (flow state),
  case_50 `analytics_7438` (category), case_53 `support_1034` (category), case_55 `backup_9287`
  (restore state), case_61 `object_19` (category), case_63 `marketing_001` (presence; also an uncited
  check). Each of these resources is linked only by a data-catalog claim or has no state evidence
  before the cutoff.
- 1 row with an account link but an unresolved decision: case_46 `tombstone_17` (treatment unknown).

Options, smallest first:

1. Keep the guard (current): no `output.csv`.
2. Allow a COMPLETE row without an account step when no account record of the subject appears in any
   eligible evidence, exporting only the cited checks for evidenced resources and listing the missing
   account proof in the report. Unblocks 108 rows; the 7 decision rows stay blocked. Risk: an account
   copy nobody captured could still exist; the labels include a timing-identified account step in such
   cases, so step recall stays lower.
3. Additionally, for a resource linked only by a catalog claim with no eligible evidence of its
   category or state, emit no step (as the labels do for `case_07` `object_19`) instead of an
   unresolved decision. Unblocks case_50, 53, 61, 63 (with option 2); case_39, 46, 55 stay blocked.

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
| P3-3 | An explicit `[REDACTED]` free-text field (no direct identifiers present) proves redaction | `exposure()` returns `treated` on a redaction marker; ANONYMIZE still requires no user id | 6 target COMPLETE rows with uncited REDACTED verifications | cited (e.g. `case_112` support_2859 → evidence_10); samples unchanged | Keep |
| P3-4 | `export_download_check_completed available=true` states the export exists | presence PRESENT; `available=false` stays attribute-only | export presence unknown in `case_372`, `case_420` | REMAINING record findings citing evidence_34 / evidence_39 | Keep |
| P3-5 | A record carrying an email marketing opt-in flag is a marketing profile; `marketing_*` maps to the one marketing rule category | `FIELD_CATEGORY` (`email_opt_in`) in the shape level; `marketing_` added to `UNAMBIGUOUS_FAMILY` | `case_12` analytics_9914 category unresolved → COMPLETE (wrong); samples status 0.9375, P 0.987, R 0.777, exact 0.25 | `case_12` INCOMPLETE with REMAINING record citing evidence_01; samples status 0.969, P 1.0, R 0.803, exact 0.281; target `marketing_001` (case_63) moves from category-unknown to presence-unknown (still blocked) | Keep |
| P3-6 | Timing + known-owner exclusion could recover the samples' missing account/analytics links | analysis only (no code): per sample, window candidates minus resources with another owner's identifier | — | of 35 gold resources missing from samples, 2 become a unique pick, 22 stay ambiguous, 36 unique picks are wrong | Rejected again (decision 2 holds on the data) |
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
