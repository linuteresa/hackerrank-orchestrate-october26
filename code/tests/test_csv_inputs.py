"""CSV-1 (beyond the 32-test plan): typed CSV loaders; activity rows stay claims."""

import unittest
from collections import Counter

from tests.helpers import DATASET, store

from ingest.csv_inputs import load_activity, load_identities, load_identity_changes, load_requests, load_rules


class CsvInputsTest(unittest.TestCase):
    def test_counts_used_as_sanity_checks(self):
        self.assertEqual(len(load_requests(DATASET)), 518)
        self.assertEqual(len(load_requests(DATASET, "sample_deletion_requests.csv")), 32)
        self.assertEqual(len(load_identities(DATASET)), 850)
        self.assertEqual(len(load_identity_changes(DATASET)), 306)
        self.assertEqual(len(load_rules(DATASET)), 15)

    def test_reported_status_distribution_matches_handoff(self):
        counts = Counter(r.reported_status for r in load_requests(DATASET))
        self.assertEqual(counts, Counter({"COMPLETE": 254, "PARTIALLY_COMPLETE": 152, "NOT_STARTED": 112}))

    def test_activity_rows_are_claims_not_observations(self):
        activity = load_activity(DATASET)
        self.assertEqual(len(activity), 4011)
        claim = next(a for a in activity if a.activity_id == "activity_3483")
        self.assertEqual((claim.event_type, claim.resource_id, claim.reported_result),
                         ("SYNC_RECORD", "account_005", "SUCCESS"))
        self.assertFalse(any(o.evidence_id.startswith("activity_") for o in store().observations))


if __name__ == "__main__":
    unittest.main()
