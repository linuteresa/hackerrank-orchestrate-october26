# Evaluation Report — Ghost Data Still Exists

All numbers below were measured, not estimated. Source of record: GitHub Actions workflow
"Deletion audit run" run #8 (id 40115185798, commit `dbfac7f`), which replayed every model output
from the cache (0 live calls). The same cache was rebuilt locally from that run's log (409 entries,
each verified against its digest-derived file name) and local offline runs reproduce run #8 exactly.
Live model outputs were produced in run #3 (id 40111949889).

## How it is measured

`code/eval/evaluate.py` compares predictions with `dataset/sample_output.csv` (32 labelled samples):

| Metric | Definition |
|---|---|
| Case-level status accuracy | `deletion_status` equal to the label |
| Step precision / recall / F1 | multiset match of whole steps (`phase, system_id, resource_id, operation, state, evidence_id`; `step_no` ignored) over all cases |
| Field accuracy | per field, position-aligned agreement between predicted and labelled step lists; unmatched positions count as wrong |
| Exact-sequence accuracy | the whole ordered `steps_json` array and the status both equal the label |

Run: `python3 code/main.py --sample` (live or cached) or `python3 code/main.py --sample --offline`.

## Final measured results (32 samples, run #8)

| Metric | Value |
|---|---|
| Status accuracy | **0.969** (31 / 32) |
| Step precision | **0.975** (155 of 159 predicted steps are in the labels) |
| Step recall | **0.803** (155 / 193 labelled steps) |
| Step F1 | **0.881** |
| Exact-sequence accuracy | **0.250** (8 / 32) |
| Field accuracy — phase | 0.781 |
| Field accuracy — system_id | 0.577 |
| Field accuracy — resource_id | 0.577 |
| Field accuracy — operation | 0.617 |
| Field accuracy — state | 0.628 |
| Field accuracy — evidence_id | 0.786 |
| Intent | 32 / 32 classified as deletion requests (all labels are deletion cases) |
| Contract violations | 0 |

Position-aligned field accuracy is lower than recall because an omitted step early in a list shifts
every later step out of alignment.

**Status error.** `case_10` (predicted COMPLETE, labelled INCOMPLETE): `support_002` is linked by a
trace-joined lookup with the subject's historical email and is PRESENT, but it is a legacy
contact-index entry and no evidence states whether it is a support message (REDACT) or an attachment
(DELETE). The decision stays unresolved and the strict gate refuses to export that row.

**Extra steps (4).** Both come from screenshots captured months *before* the request, now correctly
integrated:

- `case_07`: the object-storage screen (`evidence_46`, 13 Feb) shows `delivery_confirmation_19.pdf`,
  which the data catalog maps to `object_19` and assigns only to the subject; the request was made on
  31 Aug and no later evidence exists, so the record is reported as remaining (3 steps). The label
  does not include it.
- `case_17`: the customer-reference screen (`evidence_42`, 10 Mar) shows the retained reference
  `user_17`, which the catalog maps to `tombstone_17`; no name or email is displayed, which does not
  prove redaction, so a future redaction check is planned (1 step). The label does not include it.

Measured experiment, **not adopted** (ADR-019): requiring state evidence to be captured at or after
`requested_at` removes both (precision 1.0, exact 0.281) but leaves one more target row blocked
(112 instead of 111) and one more contract violation (2 instead of 1). A blanket date filter derived
only from the sample pattern would discard real evidence, so pre-request states are kept under an
explicit persistence assumption and labelled instead (ADR-020, below).

### Remaining sample omissions by cause (38 steps)

| Steps | Cause | Cases | Why not fixed |
|---:|---|---|---|
| 20 | account record tied to the subject only by deletion-worker activity timing | case_01, 02, 06–10, 18, 20–23, 25–32 | Decision: timing is a candidate, never ownership. Measured (P3-6): even excluding resources owned by other known subjects, timing recovers 2 of 35 labelled resources and makes 36 wrong unique picks. |
| 7 | anonymized analytics profile tied only by deletion-worker timing | case_03, 04, 21, 25–28 | same |
| 7 | retained audit record seen only in evidence captured after `audit_at`, or in no text evidence | case_09, 18, 20, 23, 24, 29, 30 | Decision: later-captured evidence is never used for discovery or citation. |
| 3 | linked record with unresolved category | case_10 | no evidence states the category |
| 1 | resource (`account_004`) absent from all text and screenshot evidence | case_04 | nothing to link |

## Target dataset (518 requests, run #8)

| Outcome | Count |
|---|---:|
| COMPLETE | 162 |
| INCOMPLETE | 346 |
| NOT_APPLICABLE | 10 |
| Rows passing the strict export gate | 407 |
| Rows blocked by the strict gate | 111 |

Non-deletion intents (NOT_APPLICABLE with `[]`): case_533 CORRECTION, case_534 MARKETING_OPT_OUT,
case_535 EXPORT, case_536 OTHER, case_537 CORRECTION, case_538 MARKETING_OPT_OUT, case_539 ACCESS,
case_540 OTHER, case_541 CORRECTION, case_542 ACCESS. Each text states the non-deletion purpose
explicitly.

Strict-gate blockers (a row can have several):

| Blocker | Rows |
|---|---:|
| COMPLETE row with no confirmed account record (account-proof guard) | 110 |
| COMPLETE row with an unresolved policy decision | 2 (case_46 `tombstone_17`, case_55 `backup_9287`) |
| COMPLETE row with no steps | 2 (case_55, case_63) |
| COMPLETE row with an uncited step (contract violation) | 1 (case_46) |

### Screenshot integration results (resolved by evidence, not by dropping resources)

| Case | Before | After | Evidence |
|---|---|---|---|
| case_39 | `data_flow_1121` flow state unresolved | INCOMPLETE: REPORT_ACTIVE_DATA_FLOW | `evidence_14`: connector for the subject's selector, mode "Continuous", recent deliveries |
| case_50 | `analytics_7438` category unresolved | INCOMPLETE: REPORT_EXPOSED_PII | `evidence_19`: analytics profile showing `user_20` and the subject's email |
| case_53 | `support_1034` category unresolved | INCOMPLETE: REPORT_EXPOSED_PII | `evidence_28`: support conversation showing the subject's email |
| case_61 | `object_19` category unresolved | INCOMPLETE: REPORT_REMAINING_RECORD | `evidence_46` row `delivery_confirmation_19.pdf`, joined by the catalog's exact file name within object storage only (a same-named attachment `support_3802` of another person in the support platform is not joined) |
| case_63 | `marketing_001` presence unknown | link refused (reported as contradicted) | `evidence_60` shows Soo-jin Park's email and phone, not the requester's; the catalog claim for the requester is not trusted over the record itself |

Still unresolved, deliberately: `backup_9287` (case_55) — the screen shows the subject's archive with
encryption, checksum, last drill and retention end, none of which establishes that restore is blocked
or possible; `tombstone_17` (case_46) — the retained reference shows no name or email, which does not
prove those fields were removed.

### Best-effort export (all 518 rows)

`python3 code/main.py --best-effort` exports every row, but only if each one passes the same
mandatory contract validation (schema, ordering, citation and cutoff checks). It adds nothing; the only
change to a gated row is removing verification steps that cite no evidence from a COMPLETE row. Every
row is classified in `code/eval/export_classes_target.json` and in the run log:

| Class | Rows | Meaning |
|---|---:|---|
| verified (COMPLETE) | 51 | every COMPLETE check cites evidence captured after the request and at or before `audit_at`; account proof present (post-deletion verification) |
| verified (INCOMPLETE) | 345 | findings cite evidence captured after the request; remediation and checks are future work |
| verified (NOT_APPLICABLE) | 10 | non-deletion request, `[]` |
| persistence_assumed (INCOMPLETE) | 1 | case_33: the `export_1260` finding cites `evidence_23`, captured 2026-06-18, before the request; the record is assumed to persist to `audit_at` (not post-deletion verification); also `export_1842` presence unknown, no finding reported |
| **best_effort (COMPLETE)** | **111** | exported only in best-effort mode; **not verified** |

Assumptions behind the 111 best-effort COMPLETE rows:

| Rows | Assumption |
|---:|---|
| 110 | no account record of the subject appears in evidence captured by `audit_at`; account deletion is assumed, not verified |
| 2 | a linked resource's category or state is unresolved; COMPLETE does not cover it (case_46 `tombstone_17`, case_55 `backup_9287`) |
| 2 | no evidenced resource is left to verify; COMPLETE carries no verification step (case_55, case_63) |
| 1 | an uncited verification step was removed (case_46: VERIFY_RECORD_REDACTED `tombstone_17`) |

The complete list of best-effort case ids with their assumptions is printed by every best-effort run
and stored in `export_classes_target.json`; the strict mode (default) still refuses to write
`output.csv` while any row is blocked.

### State persistence vs post-deletion verification (ADR-020)

A state observed in evidence captured **after** the request and at or before `audit_at` is treated as
the state after the deletion workflow: findings report it and COMPLETE checks verify it. A state
observed only in evidence captured **before** the request can say nothing about what the deletion
did; it is used under an explicit persistence assumption (the record still exists unless later
eligible evidence says otherwise) and is never presented as post-deletion verification. In the
submitted target output this affects one cited step (case_33, labelled `persistence_assumed`). In the
samples it explains the 4 extra steps above (case_07 `object_19`, case_17 `tombstone_17`), which
are findings and future checks, not verifications.

## Submitted output (best-effort submission candidate)

Produced by `python3 code/main.py --best-effort` from the cached model outputs of run #3 (prompt
versions `intent-v2`, `vision-v1`); the strict mode would refuse to write it.

| Property | Value |
|---|---|
| File | `output.csv` (repository root; git-ignored; canonical copy in the CI artifact `submission-candidate-<run>`) |
| Rows | 518 data rows + header `case_id,deletion_status,steps_json`, sorted by `case_id` |
| SHA-256 | `43cec51e2e0bb41cceaf170ae7d3cf6e45a6356a9d38aebf7ed4a0b9b554aa9d` |
| Statuses | 162 COMPLETE, 346 INCOMPLETE, 10 NOT_APPLICABLE |
| Validation | every row passes `validate_row` (schema, step numbering, phase order, allowed operation/state, citations captured at or before `audit_at`, future steps uncited) |
| Classes | 406 verified, 1 persistence_assumed, 111 best_effort (see the table above and `export_classes_target.json`) |

Limitations of the submitted file:

- 111 COMPLETE rows are **not verified deletions**: 110 lack any account record of the subject in
  evidence before the cutoff (the account step the labels expect is identified only by
  deletion-worker timing, which this system does not accept as ownership); case_46 and case_55 also
  carry an unresolved record (`tombstone_17`, `backup_9287`) that the COMPLETE status does not cover.
- Expected step recall is limited by the same rules (sample recall 0.803; 27 of 38 missing sample
  steps are timing-only resources, 7 come from evidence captured after `audit_at`).
- One cited finding relies on pre-request evidence (case_33, persistence assumed).
- The file's hash is reproducible from the repository at the packaged commit plus the model cache;
  `code.zip` does not contain the model cache, so a run from `code.zip` alone needs
  `ANTHROPIC_API_KEY` and may differ if the model's answers differ.

## Participant-created evaluation cases

`code/evaluation/custom_cases.py` builds five constructed requests from participant data with
expectations derived by hand from the evidence (not from this system's output). Results (run #8 and
local, identical):

| Case | Construction | Expectation | Result |
|---|---|---|---|
| CUST-1 identity invariance | `case_30` re-submitted with the subject's other (current) email | same output as `case_30`; labelled answer applies | **pass**; status matches label, step F1 0.923 vs label |
| CUST-2 cutoff before proof | `case_03` with `audit_at` 10:00, before `evidence_94` (captured 10:38:56) which holds every proof | nothing captured after the cutoff cited; unproven COMPLETE not exportable | **pass**; 0 steps, blocked |
| CUST-3 surname decoy | `user_44` (Freja Nyström; surname shared with another person's records `lars-nystrom@…`) in `case_21`'s window | no linked resources, no steps, blocked | **pass** |
| CUST-4 email normalisation | `case_03` email in mixed case with surrounding spaces | same output as `case_03` | **pass**; status matches label, step F1 0.8 |
| CUST-5 non-deletion text | `case_01`'s identity and timeline with `case_533`'s correction request text | NOT_APPLICABLE, `[]` | **pass** |

**5 / 5 pass.** Run: `python3 code/evaluation/custom_cases.py`.

## Failures found and improvements made

| Stage | Failure found | Improvement | Effect |
|---|---|---|---|
| P2-0 | First end-to-end baseline | — | samples: status 0.906, P 0.866, R 0.668, exact 0.031; 11 contract violations |
| P2-0b | Future remediation / verification steps cited evidence | blank `evidence_id` for future steps in INCOMPLETE cases | 0 contract violations |
| P2-1 | A copy-omission signal overrode an explicit observation of the same copy | omission counts only for copy classes with no explicit observation | `case_24` fixed |
| P2-2 | A lookup made months before the request linked an unrelated account | lookup links require `observed_at >= requested_at` | P 0.987, R 0.782, exact 0.25 |
| P3-1 | Activity-window timing was auto-linking accounts | timing became a candidate that never produces steps | R 0.777 (accepted loss) |
| P3-3 | Explicit `[REDACTED]` free-text was not treated as redaction proof | redaction marker counts as treated | 6 target COMPLETE rows gained cited proof |
| P3-4 | `available=true` export checks were ignored | export presence from download checks | 8 target contract violations removed |
| P3-5 | An `email_opt_in` record stayed category-unresolved | marketing-consent field → MARKETING_PROFILE; `marketing_` family | samples: status 0.969, P 1.0, R 0.803, exact 0.281 |
| CI run #2 | Intent prompt v1 classified seven labelled deletion follow-ups as non-deletion | intent prompt v2 | live status 0.75 → 0.969 |
| P4-1 | Screenshot record kinds "analytics profile", "support ticket" mapped to no category | kind keywords for those categories | case_50, case_53 resolved |
| P4-2 | Screen control fields (`field:Mode`, `field:Status`) were never read | closed vocabulary normalized into the flow/restore readers; health, encryption, retention never read as control state | case_39 resolved |
| P4-3 | `marketing_` / `object_` ids rejected by `RESOURCE_ID_RE` | ids and systems added | marketing_001 observed |
| P4-4 | A catalog claim assigned a record that visibly shows another person's identifiers | catalog-only links refuted by the record's own identifiers are dropped and reported | case_63 no longer claims Soo-jin Park's profile |
| P4-5 | A catalog alias (file name) could not reach the screen row | exact, unique, same-system alias join; captured time = later of screen and claim | case_61 resolved |
| P4-6 | A transcribed screen kind ("support ticket") overrode a structured attachment scan (`case_34`) | screen kinds rank below structured event/field shapes | case_34 finding kept |
| CI | Artifacts cannot be downloaded into the development container | cache entries printed to the job log on request and rebuilt locally with digest verification | local runs reproduce CI exactly |

Rejected experiments: discovery from evidence captured after `audit_at` (P2-3); activity-window
linking with owner exclusion (P2-4, P2-5, P3-6). Open: request-lifecycle lower bound for state evidence
(ADR-019, measured above).

## Regression checks

- `python3 -m unittest discover -s code/tests -t code`: 108 tests pass (offline and hermetic: an empty
  model cache, constructed fake model clients, constructed screenshot extractions).
- The best-effort test checks every exported row against the contract and that no row gains a step.
- Output contract (`code/validate.py`) runs on every row of every run: allowed status, exact step
  fields, consecutive step numbers, known operations with their allowed phase/state, phase order,
  NOT_APPLICABLE with `[]`, COMPLETE with verification steps only, INCOMPLETE with findings, findings
  and COMPLETE checks citing evidence captured at or before `audit_at`, future steps uncited.
