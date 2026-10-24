"""SQL-1 .. SQL-7: SQL snapshot parsing and per-copy provenance on real evidence."""

import re
import unittest

from tests.helpers import DATASET, evidence, manifest, obs_where, read_evidence, store, ts

from core.model import C2_TYPED_COPY_TABLE, C3_SCOPE_TABLE, INVENTORY, NOT_LISTED, PRESENT, UNKNOWN, CopyKey
from ingest.sql_snapshots import parse_sql

PRIMARY_PROFILES = CopyKey("primary", "customer_profiles")
FAILOVER_PROFILES = CopyKey("failover_replica", "customer_profiles")


def parsed(file_id):
    return parse_sql(evidence(file_id), read_evidence(file_id))


class SqlFailoverTest(unittest.TestCase):
    """SQL-1: evidence_54 - primary absent while the failover replica still holds account_005."""

    def setUp(self):
        self.result = parsed("evidence_54")

    def test_two_copies_two_typed_coverage_records(self):
        self.assertEqual(self.result.copy_keys, {PRIMARY_PROFILES, FAILOVER_PROFILES})
        self.assertEqual({(c.copy_key, c.basis) for c in self.result.coverage},
                         {(PRIMARY_PROFILES, C2_TYPED_COPY_TABLE), (FAILOVER_PROFILES, C2_TYPED_COPY_TABLE)})
        for cov in self.result.coverage:
            self.assertEqual(cov.observed_at, ts("2026-09-02T23:00:00Z"))
            self.assertEqual(cov.filter_scope, "unknown")

    def test_account_005_only_in_failover_with_separate_times(self):
        rows = obs_where(self.result.observations, resource_id="account_005")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].copy_key, FAILOVER_PROFILES)
        self.assertEqual(rows[0].observed_at, ts("2026-09-02T23:00:00Z"))
        self.assertEqual(rows[0].last_modified_at, ts("2026-09-02T22:45:00Z"))
        self.assertEqual(rows[0].identifiers["email"], ("leila.haddad@yahoo.com",))

    def test_state_at_case_05_cutoff_is_per_copy(self):
        audit = ts("2026-09-08T13:40:00Z")
        failover = store().copy_state("account_service", "account_005", FAILOVER_PROFILES, audit, "evidence_54")
        primary = store().copy_state("account_service", "account_005", PRIMARY_PROFILES, audit, "evidence_54")
        self.assertEqual(failover.state, PRESENT)
        self.assertEqual(primary.state, NOT_LISTED)
        self.assertEqual(primary.deciding[0][4], C2_TYPED_COPY_TABLE)

    def test_account_001_never_observed_is_not_listed_never_absent(self):
        states = store().copy_states("account_service", "account_001", ts("2026-09-03T11:30:00Z"))
        e54 = {s.copy_key: s.state for s in states if s.evidence_scope == "evidence_54"}
        self.assertEqual(e54, {PRIMARY_PROFILES: NOT_LISTED, FAILOVER_PROFILES: NOT_LISTED})
        self.assertNotIn("ABSENT", {s.state for s in states})

    def test_look_alike_account_kept_as_its_own_record(self):
        rows = obs_where(self.result.observations, resource_id="account_9116")
        self.assertEqual({r.identifiers["email"] for r in rows}, {("m.rios.gil@outlook.com",)})


class SqlReadReplicaTest(unittest.TestCase):
    """SQL-2: evidence_05 - generic tables give no coverage; account_994 only in the read replica."""

    def test_no_coverage_from_generic_tables(self):
        self.assertEqual(parsed("evidence_05").coverage, [])

    def test_account_994_replica_present_primary_unknown(self):
        audit = ts("2026-09-21T10:00:00Z")  # case_16 audit_at == evidence_05 captured_at
        self.assertEqual(evidence("evidence_05").captured_at, audit)
        states = {(s.copy_key, s.evidence_scope): s.state
                  for s in store().copy_states("account_service", "account_994", audit)}
        self.assertEqual(states[(CopyKey("read_replica", "customer_data"), "evidence_05")], PRESENT)
        self.assertEqual(states[(CopyKey("primary", "customer_data"), "evidence_05")], UNKNOWN)

    def test_search_1007_present_in_both_copies(self):
        rows = obs_where(parsed("evidence_05").observations, resource_id="search_1007")
        self.assertEqual({r.copy_key.role for r in rows}, {"primary", "read_replica"})
        self.assertTrue(all(r.presence == PRESENT for r in rows))


class SqlInsufficientCoverageTest(unittest.TestCase):
    """SQL-3: evidence_52 - no coverage, so omission of audit_record_2016 proves nothing."""

    def test_no_coverage_and_unknown_state(self):
        self.assertEqual(parsed("evidence_52").coverage, [])
        audit = ts("2026-09-26T11:00:00Z")
        for role in ("primary", "failover_replica"):
            state = store().copy_state("security_service", "audit_record_2016", CopyKey(role, "customer_data"),
                                       audit, "evidence_52")
            self.assertEqual(state.state, UNKNOWN)


class SqlRegionalReplicaTest(unittest.TestCase):
    """SQL-4: evidence_33 - three copies with region identity preserved."""

    def test_region_identity_and_single_replica_presence(self):
        result = parsed("evidence_33")
        self.assertEqual(result.copy_keys, {
            CopyKey("primary", "customer_data"),
            CopyKey("replica", "customer_data", "eu_west"),
            CopyKey("replica", "customer_data", "ap_south"),
        })
        rows = obs_where(result.observations, resource_id="account_6539")
        self.assertEqual([r.copy_key for r in rows], [CopyKey("replica", "customer_data", "eu_west")])


class SqlInventoryTest(unittest.TestCase):
    """SQL-5 / SQL-6: snapshot_scope inventories (copy unresolved)."""

    def test_evidence_93_scope_and_rows(self):
        result = parsed("evidence_93")
        self.assertEqual(len(result.coverage), 8)
        self.assertTrue(all(c.basis == C3_SCOPE_TABLE and c.copy_key == INVENTORY for c in result.coverage))
        backup = obs_where(result.observations, resource_id="backup_4978")[0]
        self.assertIs(backup.attributes["restore_allowed"], True)
        self.assertEqual(backup.identifiers["email"], ("k.appiah88@gmail.com",))
        audit = obs_where(result.observations, resource_id="audit_record_1738")[0]
        self.assertEqual(audit.observed_at, ts("2026-09-16T08:37:39Z"))
        self.assertEqual(audit.last_modified_at, ts("2026-09-16T06:06:38Z"))

    def test_evidence_90_declared_empty_only_for_scoped_system(self):
        result = parsed("evidence_90")
        self.assertEqual(result.observations, [])
        self.assertEqual([(c.system_id, c.declared_empty) for c in result.coverage], [("account_service", True)])


class SqlSnapshotTimesTest(unittest.TestCase):
    """SQL-7: header, completion marker and snapshot_scope times agree with the manifest."""

    def test_all_sql_times_agree(self):
        sql_files = [ef for ef in manifest().values() if ef.kind == "sql"]
        self.assertEqual(len(sql_files), 24)
        for ef in sql_files:
            result = parse_sql(ef, (DATASET / ef.path).read_text(encoding="utf-8"))
            mismatches = [i for i in result.issues if i.kind == "snapshot_time_mismatch"]
            self.assertEqual(mismatches, [], ef.file_id)
            self.assertEqual([i for i in result.issues], [], ef.file_id)
            text = (DATASET / ef.path).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"Captured at:|snapshot_completed_at=", text), ef.file_id)


if __name__ == "__main__":
    unittest.main()
