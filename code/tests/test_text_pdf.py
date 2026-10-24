"""TXT-1 and PDF-1 .. PDF-4."""

import unittest
from datetime import datetime, timezone

from tests.helpers import DATASET, evidence, obs_where, read_evidence, ts

from core.model import ABSENT, PRESENT
from ingest.pdf_documents import extract_text, parse_pdf, rejoin_split_times
from ingest.text_records import parse_text


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class HandoverTextTest(unittest.TestCase):
    """TXT-1: evidence_03 pipe-delimited handover."""

    def setUp(self):
        self.result = parse_text(evidence("evidence_03"), read_evidence("evidence_03"))

    def test_lines_parsed_without_issues(self):
        self.assertEqual(self.result.issues, [])
        self.assertEqual(len(self.result.observations), 41)

    def test_uppercase_subject_normalized(self):
        row = obs_where(self.result.observations, resource_id="account_1810")[0]
        self.assertEqual((row.presence, row.data_category), (ABSENT, "USER_PROFILE"))
        self.assertEqual(row.identifiers["email"], ("choi.jihoon94@gmail.com",))
        self.assertEqual(row.observed_at, ts("2026-10-01T12:00:00Z"))

    def test_identifiable_and_no_direct_identifier(self):
        exposed = obs_where(self.result.observations, resource_id="analytics_2034")[0]
        self.assertEqual((exposed.presence, exposed.attributes["reported_state"]), (PRESENT, "IDENTIFIABLE"))
        self.assertEqual(exposed.identifiers["email"], ("novak.tomasz@icloud.com",))
        anonymized = obs_where(self.result.observations, resource_id="analytics_6830")[0]
        self.assertIsNone(anonymized.attributes["subject_reference"])
        self.assertEqual(anonymized.attributes["subject_raw"], "no direct identifier")
        self.assertEqual(anonymized.attributes["reported_state"], "ANONYMIZED")


def pdf(file_id):
    return parse_pdf(evidence(file_id), DATASET / evidence(file_id).path)


class PdfTest(unittest.TestCase):
    def test_split_time_is_in_the_source_and_rejoined(self):
        raw = extract_text(DATASET / evidence("evidence_58").path)
        self.assertIn("submitted 18 Jun 2026 10\n24 UTC", raw)  # the PDF itself has no colon
        self.assertIn("submitted 18 Jun 2026 10:24 UTC", rejoin_split_times(raw))

    def test_pdf1_delivery_correction_attachment(self):
        result = pdf("evidence_58")
        doc = result.documents[0]
        self.assertEqual(doc.fields["tickets"], ["CS-4827"])
        self.assertEqual(doc.fields["resource_ids"], ["support_001"])
        obs = result.observations[0]
        self.assertEqual((obs.system_id, obs.resource_id, obs.presence), ("support_platform", "support_001", PRESENT))
        self.assertEqual(obs.identifiers["email"], ("priya.raman@gmail.com",))
        self.assertEqual(obs.identifiers["phone"], ("+91 98765 73142",))
        self.assertEqual(obs.attributes["submitted"], utc(2026, 6, 18, 10, 24))
        self.assertEqual(obs.observed_at, ts("2026-09-12T07:30:00Z"))  # manifest capture time

    def test_pdf2_security_retention_extract(self):
        result = pdf("evidence_55")
        obs = result.observations[0]
        self.assertEqual((obs.system_id, obs.resource_id), ("security_service", "audit_record_003"))
        self.assertEqual(obs.identifiers["user_id"], ("user_03",))
        self.assertEqual(obs.attributes["retain_until"], utc(2027, 8, 25, 4, 3))
        self.assertEqual(obs.observed_at, ts("2026-09-05T06:30:00Z"))

    def test_pdf3_legal_register_rows_without_resource_ids(self):
        result = pdf("evidence_27")
        self.assertEqual(result.observations, [])  # no resource ids in the document
        rows = result.documents[0].rows
        self.assertEqual([r[1] for r in rows], ["user_08", "user_12", "user_17", "user_21", "user_24", "user_29"])
        self.assertTrue(all(r[5].startswith("Retain until") and r[5].endswith("2033") for r in rows))

    def test_pdf4_account_history_report(self):
        result = pdf("evidence_18")
        fields = result.documents[0].fields
        self.assertEqual((fields["customer"], fields["user_ids"], fields["emails"], fields["orders"]),
                         ("Ingrid Dahl", ["user_18"], ["ingrid.dahl@outlook.com"], ["ORD-20261260"]))


if __name__ == "__main__":
    unittest.main()
