# DECISIONS — Architecture Decision Records and Experiments

Format: each ADR states context, decision, status, evidence and measured results.
Status values: `PROPOSED`, `ACCEPTED`, `REJECTED`, `SUPERSEDED`.

---

## ADR-001 — Deterministic core, model only for interpretation

- Status: **ACCEPTED** (Lead Architect handoff, 2026-10-24)
- Decision: Final statuses and steps come from tested code. Claude is limited to intent
  classification, screenshot extraction and bounded investigation of flagged cases; every
  model-proposed link is verified in code. Key from `ANTHROPIC_API_KEY` only.
- Rationale: grader rewards exact, reproducible steps; model output is non-deterministic and
  hard to defend in interview.

## ADR-002 — Store observations per physical copy; select state per copy

- Status: **PROPOSED** (awaiting Lead Architect approval)
- Context: Lead Architect challenged "latest observation per resource wins": a resource can
  be deleted from the primary but survive in a failover or reporting replica.
- Evidence inspected (2026-10-24):
  - `evidence_54` (`customer_accounts_failover_replica_20260902.sql`): `account_005` is in
    `customer_profiles_failover_replica` but not in `customer_profiles_primary`.
    `activity_3482` DELETE SUCCESS and `activity_3483` SYNC to `account_failover_replica`
    SUCCESS both claim completion. Sample `case_05` = INCOMPLETE,
    `REPORT_REMAINING_RECORD account_service/account_005 evidence_54`.
  - `evidence_05` (`customer_data_read_replica_20260921.sql`): `account_994` only in
    `customer_data_read_replica`. Sample `case_16` = INCOMPLETE citing `evidence_05`.
  - Presence-drift scan over all 12 multi-copy SQL files: 13 resources differ across copies;
    all 13 are `account_*` present in exactly one replica and absent from the primary (in the
    3-copy regional files, also absent from the other regional replica). Spot-checked 6 of them
    (`account_005/994/5389/3150/1035/6539`): each appears in exactly one evidence file and has
    both a DELETE_RECORD and a SYNC_RECORD activity claim.
  - `evidence_54` also proves ABSENT for `account_001` (sample `case_01`) although
    `account_001` appears in no evidence file: absence by omission from a covered copy.
- Decision (proposed):
  1. Key observations by `(system_id, resource_id, copy_id)`; add `Coverage` records per
     `(evidence_id, system_id, copy_id, collection)`.
  2. Select latest eligible fact per copy; aggregate to resource level with
     "any non-compliant copy ⇒ non-compliant".
  3. A `logical` observation does not clear a replica copy (H-2b, to be tested).
  3a. Coverage of a generic `record_json` table = `(system, collection)` pairs present in any
      copy table of the same file (H-2c). `evidence_52` therefore covers only
      security/identity collections and cannot clear `account_005` from the failover copy.
  3b. Copy identity is `(system_id, copy_role)` (e.g. `account_service/failover_replica`), not
      the source file, so a later snapshot of the same copy supersedes an earlier one.
  4. Cite the evidence that established the deciding copy's state.
- Alternatives: (a) latest-per-resource — rejected by `case_05`/`case_16`; (b) "ever present
  in any copy" — over-reports because it ignores later full-coverage snapshots.
- Measured results: none yet (Phase 1 not implemented).

## Experiment log

| ID | Hypothesis | Change | Before | After | Keep/Revert |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
