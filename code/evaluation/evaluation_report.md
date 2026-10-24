# Evaluation Report — Ghost Data Still Exists

All numbers below were measured, not estimated. Sources: GitHub Actions workflow "Deletion audit run"
run #3 (id 40111949889, commit `a6c63f8`, live model calls) and run #4 (id 40113277715, commit `72832f6`,
the same inputs replayed from the model-output cache with identical results), plus local offline runs
for the history table.

## How it is measured

`code/eval/evaluate.py` compares predictions with `dataset/sample_output.csv` (32 labelled samples):

| Metric | Definition |
|---|---|
| Case-level status accuracy | `deletion_status` equal to the label |
| Step precision / recall / F1 | multiset match of whole steps (`phase, system_id, resource_id, operation, state, evidence_id`; `step_no` ignored) over all cases |
| Field accuracy | per field, position-aligned agreement between predicted and labelled step lists; unmatched positions count as wrong |
| Exact-sequence accuracy | the whole ordered `steps_json` array and the status both equal the label |

Run: `python3 code/main.py --sample` (live or cached) or `python3 code/main.py --sample --offline`.

## Final measured results (32 samples, run #3, intent prompt v2)

| Metric | Value |
|---|---|
| Status accuracy | **0.969** (31 / 32) |
| Step precision | **1.000** (155 predicted steps, all correct) |
| Step recall | **0.803** (155 / 193 labelled steps) |
| Step F1 | **0.891** |
| Exact-sequence accuracy | **0.281** (9 / 32) |
| Field accuracy — phase | 0.803 |
| Field accuracy — system_id | 0.591 |
| Field accuracy — resource_id | 0.591 |
| Field accuracy — operation | 0.642 |
| Field accuracy — state | 0.642 |
| Field accuracy — evidence_id | 0.803 |
| Intent | 32 / 32 classified as deletion requests (all labels are deletion cases) |
| Contract violations | 0 |

Every predicted step is in the labelled answer: the misses are omissions, never wrong claims.
Position-aligned field accuracy is lower than recall because an omitted step early in a list shifts
every later step out of alignment.

The single status error is `case_10` (predicted COMPLETE, labelled INCOMPLETE): `support_002` is linked
by a trace-joined lookup with the subject's historical email and is PRESENT, but its record is a legacy
contact-index entry whose data category (support message → REDACT vs attachment → DELETE) is not stated
by any evidence. The decision stays unresolved; the export gate refuses to export that COMPLETE row.

### Remaining sample omissions by cause (38 steps)

| Steps | Cause | Cases | Why not fixed |
|---:|---|---|---|
| 20 | account record tied to the subject only by deletion-worker activity timing | case_01, 02, 06–10, 18, 20–23, 25–32 | Decision: timing is a candidate, never ownership. Measured (P3-6): even excluding resources owned by other known subjects, timing recovers 2 of 35 labelled resources and makes 36 wrong unique picks. |
| 7 | anonymized analytics profile tied only by deletion-worker timing | case_03, 04, 21, 25–28 | same |
| 7 | retained audit record seen only in evidence captured after `audit_at`, or in no text evidence | case_09, 18, 20, 23, 24, 29, 30 | Decision: later-captured evidence is never used for discovery or citation (H-DISC rejected). |
| 3 | linked record with unresolved category | case_10 | no evidence states the category |
| 1 | resource (`account_004`) absent from all text and screenshot evidence | case_04 | nothing to link |

## Target dataset (518 requests, run #3 / #4)

| Outcome | Count |
|---|---:|
| COMPLETE | 166 |
| INCOMPLETE | 342 |
| NOT_APPLICABLE | 10 |
| Rows passing the export gate | 403 |
| Rows blocked by the export gate | 115 |

Non-deletion intents (NOT_APPLICABLE with `[]`): case_533 CORRECTION, case_534 MARKETING_OPT_OUT,
case_535 EXPORT, case_536 OTHER, case_537 CORRECTION, case_538 MARKETING_OPT_OUT, case_539 ACCESS,
case_540 OTHER, case_541 CORRECTION, case_542 ACCESS. Each text states the non-deletion purpose
explicitly (for example "Please stop the promotional emails … I am not asking for deletion").

Blocked rows (no `output.csv` is written while any row is blocked):

| Blocker | Rows |
|---|---:|
| COMPLETE row with no confirmed account record (account-proof guard) | 114 |
| COMPLETE row with an unresolved policy decision | 7 |
| COMPLETE row with no steps | 5 |
| COMPLETE row with an uncited step / contract violation | 1 |

The 7 unresolved decisions: case_39 `data_flow_1121` (flow state only after the cutoff), case_46
`tombstone_17` (treatment state unknown), case_50 `analytics_7438`, case_53 `support_1034`, case_61
`object_19` (catalog link only; no evidence of the record's category or state before the cutoff),
case_55 `backup_9287` (restore state unknown), case_63 `marketing_001` (catalog link only; presence
unknown). In a local offline analysis without screenshot facts (126 account-guard rows), 78 had only
a retained audit record (`subject_reference`) before the cutoff and 93 had five or more candidate
account deletions in their request window.

## Participant-created evaluation cases

`code/evaluation/custom_cases.py` builds five constructed requests from participant data with
expectations derived by hand from the evidence (not from this system's output). Results from run #3
and #4 (identical):

| Case | Construction | Expectation | Result |
|---|---|---|---|
| CUST-1 identity invariance | `case_30` re-submitted with the subject's other (current) email | same output as `case_30`; labelled answer applies | **pass**; status matches label, step F1 0.923 vs label |
| CUST-2 cutoff before proof | `case_03` with `audit_at` 10:00, before `evidence_94` (captured 10:38:56) which holds every proof | nothing captured after the cutoff cited; unproven COMPLETE not exportable | **pass**; 0 steps, blocked (`complete_without_steps`, account guard) |
| CUST-3 surname decoy | `user_44` (Freja Nyström; surname shared with another person's records `lars-nystrom@…`) in `case_21`'s window | no linked resources, no steps, blocked | **pass**; deletion-worker activity in the window stays candidates only |
| CUST-4 email normalisation | `case_03` email in mixed case with surrounding spaces | same output as `case_03` | **pass**; status matches label, step F1 0.8 |
| CUST-5 non-deletion text | `case_01`'s identity and timeline with `case_533`'s correction request text | NOT_APPLICABLE, `[]` | **pass** |

**5 / 5 pass.** Run: `python3 code/evaluation/custom_cases.py` (cached model outputs; `--live` allows
model calls on cache misses).

## Failures found and improvements made

| Stage | Failure found | Improvement | Effect (samples) |
|---|---|---|---|
| P2-0 | First end-to-end baseline | — | status 0.906, P 0.866, R 0.668, exact 0.031; 11 contract violations |
| P2-0b | Future remediation / verification steps cited evidence | blank `evidence_id` for future steps in INCOMPLETE cases | 0 contract violations |
| P2-1 | A copy-omission signal overrode an explicit observation of the same copy (`case_24` account_5334 wrongly ABSENT) | omission counts only for copy classes with no explicit observation | fixed |
| P2-2 | A lookup made months before the request linked an unrelated account (`case_17`) | lookup links require `observed_at >= requested_at` | P 0.987, R 0.782, exact 0.25 |
| P3-1 | Activity-window timing was auto-linking accounts | timing became a candidate that never produces steps | R 0.777 (accepted loss) |
| P3-3 | Explicit `[REDACTED]` free-text was not treated as redaction proof | redaction marker counts as treated | 6 target COMPLETE rows gained cited proof |
| P3-4 | `available=true` export checks were ignored | export presence from download checks | 8 target contract violations removed |
| P3-5 | An `email_opt_in` record stayed category-unresolved (`case_12` wrongly COMPLETE) | marketing-consent field → MARKETING_PROFILE; `marketing_` family | status 0.969, P 1.0, R 0.803, exact 0.281 |
| CI run #2 | Live intent prompt v1 classified seven labelled deletion follow-ups ("verify my account was deleted") as non-deletion | intent prompt v2 names follow-ups that ask to verify, confirm or finish a deletion | live status 0.75 → 0.969 |
| CI | Every run re-paid all model calls; artifacts could not be downloaded into the development container | Actions cache for validated model outputs; job log lists every blocked case and per-purpose usage | replay run #4: 0 live calls, $0 |

Rejected experiments (kept out because they trade correctness for score): discovery from evidence
captured after `audit_at` (P2-3, +1 point recall); wide or narrow activity-window linking with owner
exclusion (P2-4, P2-5, P3-6).

## Regression checks

- `python3 -m unittest discover -s code/tests -t code`: 98 tests pass (offline; constructed fake model
  client, no credentials).
- Output contract (`code/validate.py`) runs on every row of every run: allowed status, exact step
  fields, consecutive step numbers, known operations with their allowed phase/state, phase order,
  NOT_APPLICABLE with `[]`, COMPLETE with verification steps only, INCOMPLETE with findings, findings
  and COMPLETE checks citing evidence captured at or before `audit_at`, future steps uncited.
