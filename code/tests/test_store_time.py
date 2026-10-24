"""TIME-1 .. TIME-3, STORE-1 .. STORE-3, MAN-1: cutoff semantics and per-copy state selection."""

import unittest
from collections import Counter

from tests.helpers import DATASET, manifest, store, synthetic_file, ts

from core.model import ABSENT, CONFLICT, NOT_LISTED, PRESENT, UNKNOWN, CopyKey
from core.store import H2D, EvidenceStore
from ingest.logs import parse_log
from ingest.sql_snapshots import parse_sql


class CutoffTest(unittest.TestCase):
    def test_time1_file_captured_after_cutoff_is_excluded_even_with_earlier_lines(self):
        cutoff = ts("2026-09-15T12:00:00Z")
        early = [o for o in store().observations if o.evidence_id == "evidence_06" and o.observed_at <= cutoff]
        self.assertTrue(early)  # lines from 09-10 .. 09-15 exist in a file captured 09-18
        self.assertFalse(any(store().eligible(o, cutoff) for o in early))

    def test_time1b_fact_dated_after_cutoff_excluded_from_file_captured_at_cutoff(self):
        # case_135: audit_at 2026-08-12T09:00Z == evidence_13 capture; the same file also holds a
        # FOUND row recorded 2026-08-14 (after its own capture). Only the fact-level gate stops it.
        audit = ts("2026-08-12T09:00:00Z")
        facts = store().observations_for("account_service", "account_1417")
        self.assertEqual(sorted((o.observed_at, o.presence) for o in facts),
                         [(ts("2026-08-12T09:00:00Z"), ABSENT), (ts("2026-08-14T09:00:00Z"), PRESENT)])
        eligible = store().observations_for("account_service", "account_1417", audit)
        self.assertEqual([(o.presence) for o in eligible], [ABSENT])

    def test_time3_observed_after_capture_is_flagged_not_dropped(self):
        violations = store().time_violations()
        self.assertEqual(sorted((v.evidence_id, v.resource_id) for v in violations),
                         [("evidence_13", "account_1417"), ("evidence_36", "account_5043")])
        flagged = {(i.evidence_id, i.locator) for i in store().issues if i.kind == "observed_after_capture"}
        self.assertEqual(flagged, {(v.evidence_id, v.locator) for v in violations})
        self.assertEqual([c for c in store().coverage if c.observed_at > c.captured_at], [])


SNAPSHOT_SQL = """\
-- Captured at: 2026-05-01T12:00:00Z
-- Database topology: read_replica
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO customer_data_read_replica VALUES('account_990','account_service','customer_profiles','{"resource_id":"account_990","email":"later.snapshot@example.com"}','2026-05-01T06:00:00Z');
COMMIT;
"""
ABSENT_LOG = (
    "2026-05-01T09:00:00Z level=INFO service=account_service trace_id=rb-1 "
    "event=profile_lookup_completed resource_id=account_990 rows=0 http_status=404\n"
)
TYPED_SQL = """\
-- Captured at: {ts}
-- Database topology: failover_replica
BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
{rows}
COMMIT;
"""
ROW_991 = "INSERT INTO customer_profiles_failover_replica VALUES('account_991','user_991','A B','a@example.com','+1 555 0100','ACTIVE','2026-05-01T00:00:00Z');"


def store_from(*parts):
    s = EvidenceStore()
    for evidence, result in parts:
        s.add_file(evidence)
        s.add(evidence.file_id, result)
    return s


class SnapshotTimeTest(unittest.TestCase):
    def test_time2_later_snapshot_with_older_updated_at_means_present_at_snapshot_time(self):
        # Constructed (no resource appears in two SQL files in the supplied data). Real anchor:
        # audit_record_1738 in evidence_93 (updated 06:06:38, captured 08:37:39).
        sql_ev = synthetic_file("syn_sql", "sql", "2026-05-01T12:00:00Z")
        log_ev = synthetic_file("syn_log", "log", "2026-05-01T09:30:00Z")
        s = store_from((sql_ev, parse_sql(sql_ev, SNAPSHOT_SQL)), (log_ev, parse_log(log_ev, ABSENT_LOG)))
        row = s.observations_for("account_service", "account_990")
        snapshot = [o for o in row if o.evidence_id == "syn_sql"][0]
        self.assertEqual(snapshot.last_modified_at, ts("2026-05-01T06:00:00Z"))
        self.assertEqual(snapshot.observed_at, ts("2026-05-01T12:00:00Z"))
        audit = ts("2026-05-02T00:00:00Z")
        replica = s.copy_state("account_service", "account_990", CopyKey("read_replica", "customer_data"), audit, "syn_sql")
        self.assertEqual(replica.state, PRESENT)
        self.assertEqual(s.copy_state("account_service", "account_990", CopyKey("logical"), audit).state, ABSENT)
        # Under H-2d grouping the replica copy is still decided by its own snapshot time.
        h2d = s.copy_state("account_service", "account_990", CopyKey("read_replica", "customer_data"), audit, identity=H2D)
        self.assertEqual((h2d.state, h2d.observed_at), (PRESENT, ts("2026-05-01T12:00:00Z")))


class PerCopyStateTest(unittest.TestCase):
    def test_store1_vector_not_collapsed(self):
        states = store().copy_states("account_service", "account_005", ts("2026-09-08T13:40:00Z"))
        e54 = sorted((s.copy_key.label(), s.state) for s in states if s.evidence_scope == "evidence_54")
        self.assertEqual(e54, [("customer_profiles/failover_replica", PRESENT), ("customer_profiles/primary", NOT_LISTED)])
        self.assertGreater(len(states), 2)  # other snapshots are reported, not merged away

    def test_store2_partial_and_empty_snapshots_never_absent(self):
        for system, resource, audit in [
            ("security_service", "audit_record_2016", "2026-09-26T11:00:00Z"),  # evidence_52: no coverage
            ("account_service", "account_4857", "2026-09-15T12:00:00Z"),  # evidence_51: declared-empty
            ("account_service", "account_001", "2026-09-06T00:00:00Z"),  # evidence_90: C3 declared-empty
        ]:
            states = store().copy_states(system, resource, ts(audit))
            self.assertNotIn(ABSENT, {s.state for s in states if s.deciding and s.deciding[0][0] == "coverage"})
        e90 = [s for s in store().copy_states("account_service", "account_001", ts("2026-09-06T00:00:00Z"))
               if s.evidence_scope == "evidence_90"]
        self.assertEqual([(s.state, s.deciding[0][4]) for s in e90], [(NOT_LISTED, "C3_SCOPE_TABLE")])
        e51 = [s for s in store().copy_states("account_service", "account_4857", ts("2026-09-15T12:00:00Z"))
               if s.evidence_scope == "evidence_51"]
        self.assertEqual([(s.state, s.deciding[0][4]) for s in e51], [(NOT_LISTED, "C1_DECLARED_COLLECTION")])
        e52 = store().copy_states("security_service", "audit_record_2016", ts("2026-09-26T11:00:00Z"))
        self.assertFalse(any(s.evidence_scope == "evidence_52" for s in e52))

    def test_store3_equal_time_conflict_keeps_both(self):
        when = "2026-05-01T12:00:00Z"
        a = synthetic_file("syn_a", "sql", when)
        b = synthetic_file("syn_b", "sql", when)
        present = parse_sql(a, TYPED_SQL.format(ts=when, rows=ROW_991))
        listed_elsewhere = parse_sql(b, TYPED_SQL.format(ts=when, rows=""))
        s = store_from((a, present), (b, listed_elsewhere))
        key = CopyKey("failover_replica", "customer_profiles")
        audit = ts("2026-05-02T00:00:00Z")
        state = s.copy_state("account_service", "account_991", key, audit, identity=H2D)
        self.assertEqual(state.state, CONFLICT)
        self.assertEqual(sorted(d[1] for d in state.deciding), ["syn_a", "syn_b"])
        # Strict identity keeps the two files as separate copies: no conflict, no merge.
        strict = {s_.evidence_scope: s_.state for s_ in s.copy_states("account_service", "account_991", audit)}
        self.assertEqual(strict, {"syn_a": PRESENT, "syn_b": NOT_LISTED})

    def test_unknown_when_no_fact(self):
        state = store().copy_state("account_service", "account_005", CopyKey("logical"), ts("2026-09-08T13:40:00Z"))
        self.assertEqual(state.state, UNKNOWN)


class ManifestTest(unittest.TestCase):
    def test_man1_manifest(self):
        files = manifest()
        self.assertEqual(len(files), 94)
        self.assertTrue(all((DATASET / ef.path).exists() for ef in files.values()))
        self.assertEqual(Counter(ef.kind for ef in files.values()),
                         Counter({"json": 28, "log": 26, "sql": 24, "png": 11, "pdf": 4, "text": 1}))
        skipped = sorted(i.evidence_id for i in store().issues if i.kind == "unsupported_in_phase1")
        self.assertEqual(skipped, sorted(fid for fid, ef in files.items() if ef.kind == "png"))


if __name__ == "__main__":
    unittest.main()
