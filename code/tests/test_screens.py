"""Screenshot integration: category keywords, control-field normalization, catalog alias join,
catalog-link contradiction.

The extractions below are constructed by hand from what each screenshot visibly shows (checked
against the images); they are not copies of model output. Screens not listed are "unavailable".
"""

import functools
import unittest

from tests.helpers import DATASET, manifest

from agent.llm import Ledger, ModelUnavailable, digest
from audit import Auditor
from ingest.csv_inputs import load_requests
from ingest.png_facts import control_signals


def record(resource_id, kind, emails=(), user_ids=(), phones=(), names=(), fields=(), presence="SHOWN"):
    return {"resource_id": resource_id, "system_hint": "", "record_kind": kind, "presence": presence,
            "user_ids": list(user_ids), "emails": list(emails), "phones": list(phones), "names": list(names),
            "email_hashes": [], "related_resource_ids": [], "fields": [{"name": k, "value": v} for k, v in fields]}


SCREENS = {
    "evidence_14": [record("data_flow_1121", "connector", emails=["jain.aditi@gmail.com"],
                           fields=[("Selector", "jain.aditi@gmail.com"), ("Mode", "Continuous"), ("Status", "Healthy"),
                                   ("Sync mode", "Continuous sync")]),
                    record("data_flow_9846", "connector", emails=["lucia-torres@gmail.com"], fields=[("Mode", "Continuous")])],
    "evidence_19": [record("analytics_7438", "analytics profile", emails=["darwish.omar@icloud.com"], user_ids=["user_20"],
                           names=["Omar Darwish"])],
    "evidence_28": [record("support_1034", "support ticket", emails=["kagyeman8947@fastmail.com"],
                           names=["Kofi Agyeman", "Maya Chen"]),
                    record("", "conversation message", names=["Kofi Agyeman"])],
    "evidence_32": [record("backup_9287", "backup", emails=["areed1433@gmail.com"],
                           fields=[("Encryption", "AES-256"), ("Retention end", "26 Jul 2026"), ("Last drill", "29 Mar 2026")])],
    "evidence_42": [record("user_17", "customer reference", user_ids=["user_17"], fields=[("Badge", "Retained reference")])],
    "evidence_46": [record("delivery_confirmation_19.pdf", "storage object", fields=[("Uploader", "support-service")]),
                    record("delivery_confirmation_20.pdf", "storage object")],
    "evidence_60": [record("marketing_001", "marketing profile", emails=["sj.park@naver.com"],
                           phones=["+82 10-5837-9451"], names=["Soo-jin Park"], fields=[("Email opt-in", "Yes")])],
}


class ScreenClaude:
    """Intent: every request is a deletion request. Vision: the constructed screens above."""

    def __init__(self):
        self.ledger = Ledger()
        paths = {ef.file_id: DATASET / ef.path for ef in manifest().values() if ef.kind == "png"}
        self.by_digest = {digest(paths[fid].read_bytes()): fid for fid in paths}

    def call(self, purpose, prompt_version, input_digest, content, schema, **_):
        if purpose == "intent":
            text = content.split("<request_text>\n", 1)[1].split("\n</request_text>", 1)[0]
            return {"authorizes_deletion": True, "request_type": "DELETION", "language": "en",
                    "evidence_quote": text[:12], "confidence": "high"}, "cache"
        file_id = self.by_digest.get(input_digest)
        if file_id not in SCREENS:
            raise ModelUnavailable("not constructed")
        return {"screen_title": "", "application": "", "visible_timestamp": "", "records": SCREENS[file_id]}, "cache"


@functools.lru_cache(maxsize=1)
def auditor():
    return Auditor(DATASET, claude=ScreenClaude())


@functools.lru_cache(maxsize=1)
def targets():
    return {r.case_id: r for r in load_requests(DATASET, "deletion_requests.csv")}


def steps(case_id):
    result = auditor().audit(targets()[case_id])
    return result, [(s.phase, s.operation, s.resource_id, s.evidence_id) for s in result.steps]


class ScreenIntegrationTest(unittest.TestCase):
    def test_connector_run_mode_is_an_active_flow(self):
        result, found = steps("case_39")
        self.assertEqual(result.status, "INCOMPLETE")
        self.assertIn(("FINDING", "REPORT_ACTIVE_DATA_FLOW", "data_flow_1121", "evidence_14"), found)

    def test_analytics_profile_screen_exposes_the_subject(self):
        result, found = steps("case_50")
        self.assertIn(("FINDING", "REPORT_EXPOSED_PII", "analytics_7438", "evidence_19"), found)

    def test_support_ticket_screen_is_a_support_message(self):
        result, found = steps("case_53")
        self.assertIn(("FINDING", "REPORT_EXPOSED_PII", "support_1034", "evidence_28"), found)

    def test_catalog_alias_joins_the_storage_object_by_exact_file_name(self):
        result, found = steps("case_61")
        self.assertIn(("FINDING", "REPORT_REMAINING_RECORD", "object_19", "evidence_46"), found)
        alias = [o for o in auditor().store.observations if o.resource_id == "object_19"]
        self.assertEqual(len(alias), 1)  # only the object-storage screen row; support_3802's file is another system
        self.assertEqual((alias[0].data_category, str(alias[0].captured_at)),
                         ("SUPPORT_ATTACHMENT", "2026-02-13 08:07:01+00:00"))  # knowable once the claim exists

    def test_catalog_claim_refuted_by_the_record_is_not_linked(self):
        result, _ = steps("case_63")  # catalog says Kwame; the screen shows Soo-jin Park's identifiers
        self.assertNotIn(("marketing_platform", "marketing_001"), result.links)
        self.assertIn("catalog_link_contradicted:marketing_platform/marketing_001:evidence_60", result.notes)

    def test_encryption_and_retention_do_not_establish_restore_blocking(self):
        result, _ = steps("case_55")
        self.assertIn(("decision", "backup_9287:restore_state"), result.unresolved)

    def test_undisplayed_identifiers_do_not_establish_tombstone_redaction(self):
        result, _ = steps("case_46")
        self.assertTrue(any(k == "decision" and d.startswith("tombstone_17:") for k, d in result.unresolved))
        self.assertEqual(result.status, "COMPLETE")
        self.assertIn("complete_with_unresolved:decision", result.export_blockers())


class BestEffortExportTest(unittest.TestCase):
    def test_every_row_is_valid_classified_and_never_gains_steps(self):
        import json

        import main
        from validate import validate_row

        rows, results, _, blockers = main.run("deletion_requests.csv", auditor())
        export, classes, failures = main.best_effort(rows, results, blockers, auditor())
        self.assertEqual((len(export), failures), (518, {}))
        self.assertEqual(set(classes), {case_id for case_id, _, _ in rows})
        original = {case_id: json.loads(steps) for case_id, _, steps in rows}
        key = lambda s: (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])
        for case_id, status, steps_json in export:
            self.assertEqual(validate_row(case_id, status, steps_json, main.citation_checker(auditor(), results[case_id].audit_at)), [])
            self.assertLessEqual({key(s) for s in json.loads(steps_json)}, {key(s) for s in original[case_id]})
            if classes[case_id]["class"] == "best_effort":
                self.assertTrue(blockers[case_id] and classes[case_id]["assumptions"])
            else:
                self.assertNotIn(case_id, blockers)


class ControlSignalTest(unittest.TestCase):
    def test_vocabulary_is_closed_and_conflicts_give_nothing(self):
        self.assertEqual(control_signals("integration_service", {"Mode": "Continuous"}), {"enabled": True})
        self.assertEqual(control_signals("integration_service", {"Status": "Paused"}), {"enabled": False})
        self.assertEqual(control_signals("integration_service", {"Status": "Healthy"}), {})
        self.assertEqual(control_signals("integration_service", {"Mode": "Continuous", "Status": "Paused"}), {})
        self.assertEqual(control_signals("backup_service", {"Encryption": "AES-256", "Retention end": "26 Jul"}), {})
        self.assertEqual(control_signals("backup_service", {"Restore allowed": "No"}), {"restore_allowed": False})
        self.assertEqual(control_signals("backup_service", {"Restore block": "Yes"}), {"restore_allowed": False})
        self.assertEqual(control_signals("account_service", {"Status": "Paused"}), {})


if __name__ == "__main__":
    unittest.main()
