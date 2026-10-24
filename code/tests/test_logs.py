"""LOG-1 .. LOG-8: log dialects, strict trace joins, observations."""

import unittest

from tests.helpers import DATASET, evidence, manifest, obs_where, read_evidence, store, synthetic_file, ts

from core.model import ABSENT, LINK_SAME_LINE, LINK_TRACE_JOIN, LOGICAL, PRESENT, CopyKey
from ingest.logs import parse_log, tokenize_line


def parsed(file_id):
    return parse_log(evidence(file_id), read_evidence(file_id))


def one(observations, **criteria):
    rows = obs_where(observations, **criteria)
    assert len(rows) == 1, (criteria, rows)
    return rows[0]


class ReadbackJoinTest(unittest.TestCase):
    """LOG-1: evidence_94 readback log (kv dialect, rb- traces)."""

    def setUp(self):
        self.result = parsed("evidence_94")

    def test_trace_join_links_lookup_to_resource(self):
        row = one(self.result.observations, resource_id="search_4262")
        joined = [k for k in row.lookup_keys if k.link == LINK_TRACE_JOIN]
        self.assertEqual([(k.value, k.kind, k.field) for k in joined], [("akosua.gyasi@gmail.com", "email", "lookup_key")])
        self.assertEqual(row.trace_id, "rb-075713-004")

    def test_explicit_absence_and_flow_state(self):
        account = one(self.result.observations, resource_id="account_2064")
        self.assertEqual((account.presence, account.raw_state["rows"]), (ABSENT, "0"))
        flow = one(self.result.observations, resource_id="data_flow_4713")
        self.assertIs(flow.attributes["enabled"], False)
        self.assertEqual(flow.identifiers["user_id"], ("user_49",))


class SupportReplicaTest(unittest.TestCase):
    """LOG-2: evidence_61 - replica-named service, deletion marker, look-alike decoy line."""

    def setUp(self):
        self.result = parsed("evidence_61")

    def test_join_and_replica_copy_identity(self):
        row = one(self.result.observations, resource_id="support_002", attributes={"event": "contact_query_completed",
                                                                                   "emitter": "support_replica", "rows": "1"})
        self.assertEqual(row.presence, PRESENT)
        self.assertEqual(row.copy_key, CopyKey("replica", "support_replica", instance="support-eu-03"))
        joined = [k.value for k in row.lookup_keys if k.link == LINK_TRACE_JOIN]
        self.assertEqual(joined, ["nadia.ionescu@yahoo.com"])

    def test_deletion_marker_is_attribute_only(self):
        rows = [o for o in obs_where(self.result.observations, resource_id="support_002")
                if o.attributes["event"] == "deletion_marker_check"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].presence)
        self.assertIs(rows[0].attributes["marker_found"], False)
        self.assertEqual(rows[0].attributes["last_write"], "2026-09-07T10:01:00Z")

    def test_look_alike_line_not_linked(self):
        row = one(self.result.observations, resource_id="support_5914")
        self.assertIsNone(row.trace_id)
        self.assertEqual([(k.value, k.link) for k in row.lookup_keys], [("nadia.popescu@yahoo.com", LINK_SAME_LINE)])


class IncompleteTraceTest(unittest.TestCase):
    """LOG-3: evidence_16 - completed-only, non-completed terminals, typed lookup keys."""

    def setUp(self):
        self.result = parsed("evidence_16")

    def test_completed_only_keeps_same_line_identity(self):
        row = one(self.result.observations, resource_id="account_4289")
        self.assertEqual([(k.value, k.link) for k in row.lookup_keys], [("erik.h9688@outlook.com", LINK_SAME_LINE)])

    def test_scan_terminal_joins_without_completed_suffix(self):
        row = one(self.result.observations, resource_id="support_2322")
        self.assertEqual([k.value for k in row.lookup_keys if k.link == LINK_TRACE_JOIN], ["menon.arjun@gmail.com"])
        self.assertEqual(row.identifiers["email"], ("menon.arjun@gmail.com",))

    def test_resource_id_lookup_key_is_typed(self):
        row = one(self.result.observations, resource_id="analytics_6980")
        joined = [k for k in row.lookup_keys if k.link == LINK_TRACE_JOIN]
        self.assertEqual([(k.value, k.kind) for k in joined], [("analytics_6980", "resource_id")])
        self.assertEqual(row.identifiers["email"], ())  # email="null": observed null

    def test_uppercase_email_normalized(self):
        row = one(self.result.observations, resource_id="support_3155")
        self.assertEqual(row.identifiers["email"], ("obi.adaeze@icloud.com",))
        self.assertEqual(row.identifier_fields["matched_value"], "OBI.ADAEZE@ICLOUD.COM")


class DialectTest(unittest.TestCase):
    """LOG-4: one real line per dialect yields the same normalized fields."""

    CASES = [
        ("evidence_06", "tr-5563868", "json", "account_service", "account_6633"),
        ("evidence_11", "tr-4973247", "syslog", "account_service", "account_1785"),
        ("evidence_16", "tr-6074163", "bracket", "account_service", "account_7947"),
        ("evidence_21", "tr-5156005", "pipe", "security_service", "audit_record_4413"),
        ("evidence_25", "tr-1108860", "cloud", "search_service", "search_021"),
        ("evidence_94", "rb-074950-001", "kv", "account_service", "account_2064"),
    ]

    def test_each_dialect(self):
        for file_id, trace, dialect, service, resource in self.CASES:
            lines = read_evidence(file_id).splitlines()
            line_no, line = next((i, l) for i, l in enumerate(lines, 1) if trace in l and resource in l and "_completed" in l)
            event = tokenize_line(file_id, line_no, line)
            self.assertEqual((event.dialect, event.trace_id, event.service, event.fields["resource_id"]),
                             (dialect, trace, service, resource), file_id)
            self.assertTrue(event.event.endswith("_completed"), file_id)
            self.assertIsNotNone(event.ts)

    def test_every_supplied_line_parses(self):
        total = 0
        for ef in manifest().values():
            if ef.kind != "log":
                continue
            result = parse_log(ef, (DATASET / ef.path).read_text(encoding="utf-8"))
            self.assertEqual([i for i in result.issues if i.kind == "unparsed_line"], [], ef.file_id)
            total += result.stats["lines"]
        self.assertEqual(total, 2211)


class ExactCutoffTest(unittest.TestCase):
    """LOG-6: evidence_08 captured exactly at case_86's audit_at."""

    def test_inclusive_boundary(self):
        rows = obs_where(parsed("evidence_08").observations, resource_id="account_8473")
        self.assertEqual(sorted((o.observed_at, o.presence) for o in rows),
                         [(ts("2026-08-31T07:00:00Z"), PRESENT), (ts("2026-09-06T07:00:00Z"), ABSENT)])
        audit = ts("2026-09-06T07:00:00Z")
        self.assertEqual(store().copy_state("account_service", "account_8473", LOGICAL, audit).state, ABSENT)
        before = ts("2026-09-06T06:59:59Z")
        self.assertEqual([o for o in store().observations_for("account_service", "account_8473", before)
                          if o.evidence_id == "evidence_08"], [])


class ReadPathTest(unittest.TestCase):
    """LOG-7: pool/route recorded as read_path; the copy stays logical (H-2b unresolved)."""

    def test_read_path(self):
        row = one(parsed("evidence_16").observations, resource_id="analytics_6980")
        self.assertEqual([(kind, value) for (_, kind, value) in row.read_path],
                         [("pool", "analytics_platform_replica"), ("route", "primary")])
        self.assertEqual(row.copy_key, LOGICAL)


SYNTHETIC = """\
2026-01-01T00:00:01Z level=DEBUG service=account_service trace_id=tr-s1 event=profile_lookup_started lookup_key=only.started@example.com
2026-01-01T00:00:02Z level=DEBUG service=account_service trace_id=tr-s2 event=profile_lookup_started lookup_key=first@example.com
2026-01-01T00:00:03Z level=DEBUG service=account_service trace_id=tr-s2 event=profile_lookup_started lookup_key=second@example.com
2026-01-01T00:00:04Z level=INFO service=account_service trace_id=tr-s2 event=profile_lookup_completed resource_id=account_901 rows=1
2026-01-01T00:00:05Z level=DEBUG service=account_service trace_id=tr-s3 event=profile_lookup_started lookup_key=same@example.com
2026-01-01T00:00:06Z level=DEBUG service=account_service trace_id=tr-s3 event=profile_lookup_started lookup_key=same@example.com
2026-01-01T00:00:07Z level=INFO service=account_service trace_id=tr-s3 event=profile_lookup_completed resource_id=account_902 rows=1
2026-01-01T00:00:08Z level=INFO service=account_service trace_id=tr-s4 event=profile_lookup_completed resource_id=account_903 rows=0
2026-01-01T00:00:09Z level=DEBUG service=account_service trace_id=tr-s4 event=profile_lookup_started lookup_key=late@example.com
2026-01-01T00:00:10Z level=INFO service=account_service trace_id=tr-s4 event=profile_lookup_completed resource_id=account_903 rows=0
2026-01-01T00:00:11Z level=DEBUG service=account_service trace_id=tr-s5 event=profile_lookup_started lookup_key=pair@example.com
2026-01-01T00:00:12Z level=INFO service=search_service trace_id=tr-s5 event=index_query_completed resource_id=search_904 result=hit
2026-01-01T00:00:13Z level=DEBUG service=account_service trace_id=tr-s6 event=profile_lookup_started lookup_key=seq@example.com
2026-01-01T00:00:14Z level=INFO service=account_service trace_id=tr-s6 event=profile_lookup_completed resource_id=account_905 rows=1
2026-01-01T00:00:15Z level=DEBUG service=account_service trace_id=tr-s6 event=profile_lookup_started lookup_key=seq@example.com
2026-01-01T00:00:16Z level=INFO service=account_service trace_id=tr-s6 event=profile_lookup_completed resource_id=account_905 rows=0
"""
SYNTHETIC_OTHER_FILE = """\
2026-01-01T00:00:01Z level=DEBUG service=account_service trace_id=tr-cross event=profile_lookup_started lookup_key=cross@example.com
"""
SYNTHETIC_OTHER_FILE_2 = """\
2026-01-01T00:00:02Z level=INFO service=account_service trace_id=tr-cross event=profile_lookup_completed resource_id=account_906 rows=1
"""


class ConstructedTraceTest(unittest.TestCase):
    """LOG-5 / LOG-8: constructed fixtures for edge cases absent from the supplied logs."""

    def setUp(self):
        self.result = parse_log(synthetic_file("syn_log", "log", "2026-01-02T00:00:00Z"), SYNTHETIC)
        self.ambiguous = {i.locator: i.detail for i in self.result.issues if i.kind == "ambiguous_trace"}

    def joined(self, resource_id):
        return [(o.locator, [k.value for k in o.lookup_keys if k.link == LINK_TRACE_JOIN])
                for o in obs_where(self.result.observations, resource_id=resource_id)]

    def test_started_only_trace_produces_no_observation(self):  # LOG-5
        self.assertEqual(self.result.stats["started_only"], 1)
        self.assertFalse(any(o.trace_id == "tr-s1" for o in self.result.observations))

    def test_two_lookup_identities_is_ambiguous(self):  # LOG-8a
        self.assertIn("tr-s2", self.ambiguous)
        self.assertEqual(self.joined("account_901"), [("line=4", [])])

    def test_interleaved_repeated_starts_is_ambiguous(self):  # LOG-8b
        self.assertIn("tr-s3", self.ambiguous)
        self.assertEqual(self.joined("account_902"), [("line=7", [])])

    def test_completed_before_started_is_not_joined(self):  # LOG-8c
        self.assertEqual(self.joined("account_903"), [("line=8", []), ("line=10", ["late@example.com"])])

    def test_mismatched_event_pair_not_joined(self):  # LOG-8e
        self.assertEqual(self.joined("search_904"), [("line=12", [])])

    def test_sequential_repeats_pair_with_nearest_start(self):  # repeated, non-interleaved
        self.assertNotIn("tr-s6", self.ambiguous)
        self.assertEqual(self.joined("account_905"), [("line=14", ["seq@example.com"]), ("line=16", ["seq@example.com"])])

    def test_same_trace_id_in_two_files_never_joined(self):  # LOG-8d
        first = parse_log(synthetic_file("syn_a", "log", "2026-01-02T00:00:00Z"), SYNTHETIC_OTHER_FILE)
        second = parse_log(synthetic_file("syn_b", "log", "2026-01-02T00:00:00Z"), SYNTHETIC_OTHER_FILE_2)
        self.assertEqual(first.observations, [])
        self.assertEqual([o.lookup_keys for o in second.observations], [()])


if __name__ == "__main__":
    unittest.main()
