"""Phase 3 model layer: cache, usage/cost ledger, validation, unavailability, PNG fact conversion.

All tests use a constructed fake client: no network, no credentials, no real model output.
"""

import json
import tempfile
import unittest
from types import SimpleNamespace

from tests.helpers import DATASET, samples_for, synthetic_file

from agent.intent import classify
from agent.llm import FALLBACK_BETA, MODEL, Claude, Usage
from audit import Auditor
from ingest.png_facts import parse_png_facts

TEXT = "Bitte löschen Sie mein Konto und alle meine Daten."


class FakeClient:
    def __init__(self, payload=None, error=None, stop_reason="end_turn"):
        self.payload, self.error, self.stop_reason, self.calls = payload, error, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            model=MODEL, stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )


def deletion_payload(quote="löschen Sie mein Konto"):
    return {"authorizes_deletion": True, "request_type": "DELETION", "language": "de",
            "evidence_quote": quote, "confidence": "high"}


class LlmLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_call_shape_cache_and_cost(self):
        fake = FakeClient(deletion_payload())
        live = Claude(client=fake, cache_dir=self.tmp.name)
        intent = classify(live, TEXT)
        self.assertEqual((intent.status, intent.source), ("DELETION", "live"))
        call = fake.calls[0]
        self.assertEqual((call["model"], call["fallbacks"], call["betas"]), (MODEL, "default", [FALLBACK_BETA]))
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        # A second client without any model access reuses the cached output and its recorded usage.
        cached = Claude(offline=True, cache_dir=self.tmp.name)
        again = classify(cached, TEXT)
        self.assertEqual((again.status, again.source), ("DELETION", "cache"))
        summary = cached.ledger.summary()[0]
        self.assertEqual((summary["source"], summary["calls"], summary["input_tokens"]), ("cache", 1, 1000))
        self.assertAlmostEqual(summary["cost_usd"], Usage(MODEL, 1000, 50).cost())
        self.assertAlmostEqual(Usage(MODEL, 1_000_000, 1_000_000).cost(), 24.0)

    def test_missing_credentials_leave_intent_unresolved(self):
        fake = FakeClient(error=TypeError("Could not resolve authentication method. Expected one of api_key"))
        claude = Claude(client=fake, cache_dir=self.tmp.name)
        self.assertEqual(classify(claude, TEXT).status, "UNRESOLVED")
        self.assertEqual(classify(claude, TEXT + " ").status, "UNRESOLVED")
        self.assertEqual(len(fake.calls), 1)  # unavailability is remembered; no retry storm

    def test_offline_cache_miss_is_unresolved(self):
        self.assertEqual(classify(Claude(offline=True, cache_dir=self.tmp.name), TEXT).status, "UNRESOLVED")

    def test_non_verbatim_quote_and_inconsistent_labels_rejected(self):
        bad_quote = classify(Claude(client=FakeClient(deletion_payload("delete my account")), cache_dir=self.tmp.name), TEXT)
        self.assertEqual((bad_quote.status, bad_quote.reason), ("UNRESOLVED", "evidence_quote not verbatim in request text"))
        payload = deletion_payload() | {"request_type": "ACCESS"}
        inconsistent = classify(Claude(client=FakeClient(payload), cache_dir=self.tmp.name + "/x"), TEXT)
        self.assertEqual(inconsistent.reason, "inconsistent labels")

    def test_refusal_is_unresolved(self):
        fake = FakeClient(deletion_payload(), stop_reason="refusal")
        self.assertEqual(classify(Claude(client=fake, cache_dir=self.tmp.name), TEXT).status, "UNRESOLVED")


class StubClaude:
    """Answers intent with a fixed label; screenshots are 'unavailable'."""

    def __init__(self, request_type):
        from agent.llm import Ledger

        self.ledger = Ledger()
        self.request_type = request_type

    def call(self, purpose, prompt_version, input_digest, content, schema, **_):
        from agent.llm import ModelUnavailable

        if purpose != "intent":
            raise ModelUnavailable("stub")
        text = content.split("<request_text>\n", 1)[1].split("\n</request_text>", 1)[0]
        return {"authorizes_deletion": self.request_type == "DELETION", "request_type": self.request_type,
                "language": "en", "evidence_quote": text[:12], "confidence": "high"}, "cache"


class IntentIntegrationTest(unittest.TestCase):
    def test_non_deletion_gives_not_applicable_with_empty_steps(self):
        auditor = Auditor(DATASET, claude=StubClaude("ACCESS"))
        result = auditor.audit(samples_for("case_11"))
        self.assertEqual((result.status, result.steps, result.export_blockers()), ("NOT_APPLICABLE", [], []))

    def test_deletion_intent_runs_the_deterministic_audit(self):
        auditor = Auditor(DATASET, claude=StubClaude("DELETION"))
        result = auditor.audit(samples_for("case_11"))
        self.assertEqual(result.status, "INCOMPLETE")
        self.assertEqual(result.export_blockers(), [])
        self.assertTrue(all(i.kind == "png_not_extracted" for i in auditor.store.issues if i.evidence_id in
                            {f for f, ev in auditor.store.files.items() if ev.kind == "png"}))


class PngFactsTest(unittest.TestCase):
    """Constructed extraction (no real model output) to test code-side validation."""

    def test_shape_validation_and_presence(self):
        evidence = synthetic_file("syn_png", "png", "2026-09-26T11:00:00Z")
        extraction = {"screen_title": "Customer", "application": "Ops", "visible_timestamp": "", "records": [
            {"resource_id": "account_5762", "system_hint": "Customer Operations", "record_kind": "profile",
             "presence": "SHOWN", "user_ids": ["user_11"], "emails": ["Haruka.S1838@zoho.com", "not-an-email"],
             "phones": ["+81 90 7610 2507"], "names": ["Haruka Sato"], "email_hashes": [],
             "related_resource_ids": ["support_9538", "CS-8538"], "fields": [{"name": "Loyalty tier", "value": "Silver"}]},
            {"resource_id": "ORD-20265762", "system_hint": "", "record_kind": "order", "presence": "SHOWN",
             "user_ids": [], "emails": [], "phones": [], "names": [], "email_hashes": [], "related_resource_ids": [],
             "fields": []},
        ]}
        result = parse_png_facts(evidence, extraction, "cache")
        self.assertEqual([o.resource_id for o in result.observations], ["account_5762"])
        obs = result.observations[0]
        self.assertEqual((obs.system_id, obs.presence, obs.copy_key.role), ("account_service", "PRESENT", "document"))
        self.assertEqual(obs.identifiers["email"], ("haruka.s1838@zoho.com",))
        self.assertEqual(obs.attributes["related_resource_ids"], ["support_9538"])
        self.assertEqual([(i.kind, i.detail) for i in result.issues], [("png_value_rejected", "not-an-email")])
        self.assertEqual(len(result.documents[0].rows), 2)  # the order row is kept as a document fact only

    def test_unextracted_png_is_an_issue(self):
        result = parse_png_facts(synthetic_file("syn_png", "png", "2026-09-26T11:00:00Z"), None, "model unavailable")
        self.assertEqual([i.kind for i in result.issues], ["png_not_extracted"])


if __name__ == "__main__":
    unittest.main()
