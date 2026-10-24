"""Audit one request end to end.

intent -> subject identity -> confirmed links (+ uncertain candidates) -> category -> per-copy state
-> policy decision -> ordered steps -> export gate.

The model is used only for intent (``agent/intent.py``) and screenshot transcription
(``agent/vision.py``); every status is decided here in code. Insufficient evidence is tracked as
``unresolved`` and never converted into findings or a different status.
"""

from dataclasses import dataclass, field

from agent.intent import classify
from agent.llm import Claude
from core.categories import category_for_text, resolve_category
from core.identity import IdentityIndex, link_resources
from core.resource_state import resource_state
from ingest.csv_inputs import load_activity, load_identities, load_identity_changes, load_rules
from core.model import ParseResult
from ingest.loader import load_store
from ingest.png_facts import alias_observations
from planner import order_steps
from policy import decide, rule_actions


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    subject: object = None
    intent: object = None
    links: dict = field(default_factory=dict)
    candidates: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # (kind, detail)
    notes: list = field(default_factory=list)
    audit_at: object = None

    def export_blockers(self):
        """Reasons this row must not be exported as final output (empty list = exportable)."""
        blockers = [f"unresolved:{kind}" for kind, _ in self.unresolved if kind in ("intent", "subject")]
        if self.status == "COMPLETE":
            if not self.steps:
                blockers.append("complete_without_steps")
            if any(not s.evidence_id for s in self.steps):
                blockers.append("complete_with_uncited_step")
            blockers += [f"complete_with_unresolved:{kind}" for kind, _ in self.unresolved if kind == "decision"]
            if any(kind == "account_link" for kind, _ in self.unresolved):
                blockers.append("complete_without_account_verification")
        return blockers


class Auditor:
    def __init__(self, dataset_dir, offline=False, claude=None):
        self.claude = claude or Claude(offline=offline)
        self.store = load_store(dataset_dir, claude=self.claude)
        self.activity = load_activity(dataset_dir)
        for obs in alias_observations(self.store, self.activity, category_for_text):
            self.store.add(obs.evidence_id, ParseResult(observations=[obs], copy_keys={obs.copy_key}))
        self.index = IdentityIndex(load_identities(dataset_dir), load_identity_changes(dataset_dir))
        self.actions = rule_actions(load_rules(dataset_dir))
        self.classifications = [a for a in self.activity if a.event_type == "CLASSIFY_REQUEST"]

    def audit(self, request):
        intent = classify(self.claude, request.request_text)
        if intent.status == "NON_DELETION":
            return CaseResult(request.case_id, "NOT_APPLICABLE", [], intent=intent)
        result = self._audit_deletion(request)
        result.intent = intent
        if intent.status == "UNRESOLVED":
            result.unresolved.append(("intent", intent.reason))
        else:
            # A recorded intake classification is a claim; disagreement is surfaced, not resolved.
            for claim in self.classifications:
                if (claim.target_system, claim.resource_id) in result.links or (
                        "account_service", claim.resource_id) in result.links:
                    if claim.event_time <= request.audit_at and "non-deletion" in claim.details:
                        result.notes.append(f"intent_conflicts_with:{claim.activity_id}")
        return result

    def _audit_deletion(self, request):
        subject = self.index.resolve(request.email)
        if subject is None:
            return CaseResult(request.case_id, "INCOMPLETE", [], unresolved=[("subject", request.email)])
        links, candidates, contradicted = link_resources(subject, request, self.store, self.activity)
        decisions, unresolved = [], []
        for (system_id, resource_id), link in sorted(links.items()):
            state = resource_state(self.store, system_id, resource_id, request.audit_at)
            category, reason = resolve_category(resource_id, state.eligible_observations)
            decision = decide(system_id, resource_id, category, state, self.actions)
            decision.category_reason = reason
            decisions.append(decision)
            if decision.unresolved:
                unresolved.append(("decision", f"{resource_id}:{decision.unresolved}"))
        if not any(system == "account_service" for system, _ in links):
            unresolved.append(("account_link", "no confirmed account_service link"))
        findings = [s for d in decisions for s in d.findings]
        if findings:
            # Remediation and post-remediation checks describe future work: no citation.
            future = [s for d in decisions for s in d.remediation + d.verification]
            for step in future:
                step.evidence_id = ""
            steps, status = findings + future, "INCOMPLETE"
        else:
            steps, status = [s for d in decisions for s in d.verification], "COMPLETE"
        notes = [f"catalog_link_contradicted:{system}/{resource}:{evidence}" for (system, resource), evidence
                 in sorted(contradicted.items())]
        return CaseResult(request.case_id, status, order_steps(steps), subject, links=links, candidates=candidates,
                          decisions=decisions, unresolved=unresolved, notes=notes)
