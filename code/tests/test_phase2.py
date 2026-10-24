"""Phase 2: identity, categories, resource state, policy, planning, validation, evaluation."""

import csv
import functools
import json
import unittest

from tests.helpers import DATASET, store, ts

from audit import Auditor
from core.categories import resolve_category
from core.model import ABSENT, PRESENT
from core.resource_state import resource_state
from eval.evaluate import evaluate, load_answers
from ingest.csv_inputs import load_requests
from planner import to_json
from policy import Step
from validate import validate_row


@functools.lru_cache(maxsize=1)
def auditor():
    return Auditor(DATASET, offline=True)


@functools.lru_cache(maxsize=1)
def samples():
    return {r.case_id: r for r in load_requests(DATASET, "sample_deletion_requests.csv")}


class IdentityTest(unittest.TestCase):
    def test_current_and_historical_resolution(self):
        index = auditor().index
        self.assertEqual(index.resolve("amina.elmasri@gmail.com").match, "current")
        historical = index.resolve(samples()["case_10"].email)
        self.assertEqual((historical.user_id, historical.match), ("user_10", "historical"))
        upper = index.resolve("CHOI.JIHOON94@GMAIL.COM")
        self.assertIsNotNone(upper)

    def test_every_request_email_resolves(self):
        index = auditor().index
        for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
            self.assertTrue(all(index.resolve(r.email) for r in load_requests(DATASET, name)), name)

    def test_link_reasons_are_preserved(self):
        result = auditor().audit(samples()["case_11"])
        link = result.links[("search_service", "search_052")]
        self.assertEqual(link.reasons[0][:2], ("identifier", "evidence_03"))

    def test_lookup_before_request_lifecycle_is_not_a_link(self):
        # account_6523 was looked up with case_17's email on 2026-03-10, months before the request.
        result = auditor().audit(samples()["case_17"])
        self.assertNotIn(("account_service", "account_6523"), result.links)
        self.assertIn(("account_service", "account_4092"), result.links)

    def test_look_alike_email_not_linked(self):
        result = auditor().audit(samples()["case_02"])  # mateo.rios@ vs m.rios.gil@ (account_9116)
        self.assertNotIn(("account_service", "account_9116"), result.links)


class CategoryTest(unittest.TestCase):
    def test_explicit_beats_family_and_ambiguous_family_stays_unresolved(self):
        audit = ts("2026-10-01T12:00:00Z")
        obs = store().observations_for("support_platform", "support_4218", audit)
        self.assertEqual(resolve_category("support_4218", obs), ("SUPPORT_ATTACHMENT", "explicit"))
        obs = store().observations_for("analytics_platform", "analytics_9914", ts("2026-09-15T12:00:00Z"))
        self.assertEqual(resolve_category("analytics_9914", obs)[0], None)

    def test_pdf_attachment_fields_resolve_category(self):
        obs = store().observations_for("support_platform", "support_001", ts("2026-09-12T08:00:00Z"))
        self.assertEqual(resolve_category("support_001", obs), ("SUPPORT_ATTACHMENT", "shape"))


class ResourceStateTest(unittest.TestCase):
    def test_replica_presence_is_not_hidden_by_primary(self):
        state = resource_state(store(), "account_service", "account_005", ts("2026-09-08T13:40:00Z"))
        self.assertEqual((state.presence, state.evidence_id), (PRESENT, "evidence_54"))

    def test_admissible_omission_gives_absence_when_nothing_explicit(self):
        state = resource_state(store(), "account_service", "account_001", ts("2026-09-03T11:30:00Z"))
        self.assertEqual((state.presence, state.evidence_id), (ABSENT, "evidence_54"))

    def test_explicit_presence_beats_later_omission(self):
        # case_24: 200 at 09:41 in evidence_92; evidence_51 declares an empty profile collection at 12:00.
        state = resource_state(store(), "account_service", "account_5334", ts("2026-09-15T12:00:00Z"))
        self.assertEqual((state.presence, state.evidence_id), (PRESENT, "evidence_92"))

    def test_explicit_absence_cited_before_omission(self):
        state = resource_state(store(), "account_service", "account_3136", ts("2026-09-15T12:00:00Z"))
        self.assertEqual((state.presence, state.evidence_id), (ABSENT, "evidence_92"))


class CandidateTest(unittest.TestCase):
    def test_timing_matches_are_candidates_not_links(self):
        result = auditor().audit(samples()["case_21"])
        self.assertNotIn(("account_service", "account_6440"), result.links)
        self.assertIn(("account_service", "account_6440"), result.candidates)
        self.assertEqual(result.candidates[("account_service", "account_6440")].reasons[0][0], "activity_window")
        self.assertIn(("account_link", "no confirmed account_service link"), result.unresolved)

    def test_unsupported_complete_rows_are_blocked_not_converted(self):
        result = auditor().audit(samples()["case_01"])  # no confirmed resources at all
        self.assertEqual((result.status, result.steps), ("COMPLETE", []))
        self.assertIn("complete_without_steps", result.export_blockers())
        self.assertIn("unresolved:intent", result.export_blockers())  # offline: intent never guessed


class PolicyTest(unittest.TestCase):
    def steps(self, case_id):
        return [(s.phase, s.operation, s.resource_id, s.evidence_id) for s in auditor().audit(samples()[case_id]).steps]

    def test_tombstone_exposed_vs_redacted(self):
        self.assertIn(("FINDING", "REPORT_EXPOSED_PII", "tombstone_9178", "evidence_51"), self.steps("case_32"))
        self.assertIn(("VERIFICATION", "VERIFY_RECORD_REDACTED", "tombstone_440", "evidence_51"), self.steps("case_31"))

    def test_flow_and_restore_findings_are_ordered_first(self):
        steps = self.steps("case_11")
        remediation = [s[1] for s in steps if s[0] == "REMEDIATION"]
        self.assertEqual(remediation, ["DISABLE_DATA_FLOW", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"])

    def test_incomplete_future_steps_cite_nothing(self):
        for phase, _, _, evidence in self.steps("case_11"):
            if phase != "FINDING":
                self.assertEqual(evidence, "")


class PlannerValidateTest(unittest.TestCase):
    def test_serialization_matches_sample_style(self):
        text = to_json([Step("VERIFICATION", "account_service", "account_001", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_54")])
        with open(DATASET / "sample_output.csv", newline="", encoding="utf-8") as handle:
            first = next(csv.DictReader(handle))
        self.assertEqual(text, first["steps_json"])

    def test_validator_accepts_every_sample_answer(self):
        with open(DATASET / "sample_output.csv", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.assertEqual(validate_row(row["case_id"], row["deletion_status"], row["steps_json"]), [], row["case_id"])

    def test_validator_rejects_bad_rows(self):
        self.assertTrue(validate_row("x", "NOT_APPLICABLE", '[{"a":1}]'))
        bad_future = json.dumps([
            {"step_no": 1, "phase": "FINDING", "system_id": "s", "resource_id": "r", "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "e"},
            {"step_no": 2, "phase": "REMEDIATION", "system_id": "s", "resource_id": "r", "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": "e"},
        ])
        self.assertIn("step 2: future step cites e", validate_row("x", "INCOMPLETE", bad_future))


class SampleRegressionTest(unittest.TestCase):
    """Guards the measured baseline; predictions never read sample_output.csv."""

    def test_baseline_floor(self):
        predicted = {}
        for case_id, request in samples().items():
            result = auditor().audit(request)
            predicted[case_id] = (result.status, json.loads(to_json(result.steps)))
        metrics, _ = evaluate(predicted, load_answers(DATASET / "sample_output.csv"))
        self.assertGreaterEqual(metrics["status_accuracy"], 0.93)
        self.assertGreaterEqual(metrics["step_precision"], 0.98)
        # 0.782 -> 0.777 when timing-only account links became candidates (Phase 3 decision).
        self.assertGreaterEqual(metrics["step_recall"], 0.77)


if __name__ == "__main__":
    unittest.main()
