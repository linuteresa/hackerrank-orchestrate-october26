"""JSON-1 .. JSON-5: JSON evidence detected by structure, with per-record times."""

import unittest

from tests.helpers import evidence, obs_where, read_evidence, ts

from core.model import ABSENT, C1_DECLARED_COLLECTION, LOGICAL, PRESENT, RECORDS_BUNDLE
from ingest.json_records import parse_json


def parsed(file_id):
    return parse_json(evidence(file_id), read_evidence(file_id))


class BundleTest(unittest.TestCase):
    """JSON-1: evidence_51 bundle - C1 coverage, declared-empty collection, tombstone identifiers."""

    def setUp(self):
        self.result = parsed("evidence_51")

    def test_three_declared_collections_one_empty(self):
        self.assertEqual(len(self.result.coverage), 3)
        self.assertTrue(all(c.basis == C1_DECLARED_COLLECTION and c.copy_key == RECORDS_BUNDLE
                            for c in self.result.coverage))
        empty = [(c.system_id, c.collection) for c in self.result.coverage if c.declared_empty]
        self.assertEqual(empty, [("account_service", "customer_profiles")])

    def test_tombstone_identifiers_present_vs_observed_null(self):
        exposed = obs_where(self.result.observations, resource_id="tombstone_9178")[0]
        self.assertEqual(exposed.identifiers["email"], ("addo.kofi@gmail.com",))
        self.assertEqual(exposed.identifiers["user_id"], ("user_542",))
        self.assertEqual(exposed.data_category, "ACCOUNT_TOMBSTONE")
        redacted = obs_where(self.result.observations, resource_id="tombstone_440")[0]
        for kind in ("email", "phone", "email_hash", "name"):
            self.assertEqual(redacted.identifiers[kind], (), kind)  # observed null, not missing
        self.assertEqual(redacted.identifiers["user_id"], ("user_541",))


class BundleSnapshotTimesTest(unittest.TestCase):
    """JSON-2: evidence_02 - each record takes its own snapshot's captured_at, not the file time."""

    def test_record_times_follow_snapshot(self):
        result = parsed("evidence_02")
        times = {o.resource_id: o.observed_at for o in result.observations}
        self.assertEqual(times["account_5684"], ts("2026-09-09T12:00:00Z"))
        self.assertEqual(times["search_8168"], ts("2026-09-15T09:00:00Z"))
        self.assertEqual(times["account_5334"], ts("2026-09-15T12:00:00Z"))
        self.assertEqual(times["account_3866"], ts("2026-09-17T12:00:00Z"))
        self.assertTrue(all(o.captured_at == ts("2026-09-17T12:00:00Z") for o in result.observations))


class LookupExportTest(unittest.TestCase):
    """JSON-3: evidence_92 - per-row observed_at, explicit 200/404, no coverage."""

    def setUp(self):
        self.result = parsed("evidence_92")

    def test_status_codes_and_times(self):
        present = obs_where(self.result.observations, resource_id="account_5334")[0]
        self.assertEqual(present.presence, PRESENT)
        self.assertEqual(present.identifiers["email"], ("keiko.nakamura@gmail.com",))
        self.assertEqual(present.identifiers["name"], ())
        self.assertEqual(present.observed_at, ts("2026-09-15T09:41:33Z"))
        absent = obs_where(self.result.observations, resource_id="account_3136")[0]
        self.assertEqual(absent.presence, ABSENT)
        self.assertEqual(absent.copy_key, LOGICAL)

    def test_search_query_is_a_lookup_key(self):
        row = obs_where(self.result.observations, resource_id="search_5577")[0]
        self.assertEqual([k.value for k in row.lookup_keys], ["keiko.nakamura@gmail.com"])

    def test_no_coverage(self):
        self.assertEqual(self.result.coverage, [])


class LedgerTest(unittest.TestCase):
    """JSON-4: ledgers detected by structure despite misleading file names."""

    def test_connector_named_file_is_a_lookup_ledger(self):
        result = parsed("evidence_13")
        self.assertEqual(result.stats.get("shape:ledger"), 1)
        self.assertEqual(result.coverage, [])
        row = obs_where(result.observations, resource_id="account_1084")[0]
        self.assertEqual((row.system_id, row.data_category, row.presence), ("account_service", "USER_PROFILE", ABSENT))

    def test_row_time_differs_from_capture(self):
        result = parsed("evidence_36")
        row = obs_where(result.observations, resource_id="account_7618")[0]
        self.assertEqual(row.observed_at, ts("2026-04-03T12:00:00Z"))
        self.assertEqual(row.captured_at, ts("2026-04-09T12:00:00Z"))


class CaptureTest(unittest.TestCase):
    """JSON-5: evidence_59 single-collection capture; expires_at is an attribute."""

    def test_export_capture(self):
        result = parsed("evidence_59")
        self.assertEqual([(c.system_id, c.collection, c.basis) for c in result.coverage],
                         [("export_service", "customer_export_jobs", C1_DECLARED_COLLECTION)])
        row = obs_where(result.observations, resource_id="export_001")[0]
        self.assertEqual(row.identifiers["email"], ("gabriel.costa@proton.me",))
        self.assertEqual(row.observed_at, ts("2026-09-14T15:40:00Z"))
        self.assertEqual(row.attributes["expires_at"], "2026-10-04T16:11:00Z")


if __name__ == "__main__":
    unittest.main()
