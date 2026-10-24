"""Audit one deletion request end to end (deterministic baseline, no model calls).

intent -> subject identity -> linked resources -> category -> state -> policy -> ordered steps.
Intent classification is Phase 3; until then every request is treated as a deletion request.
"""

from dataclasses import dataclass, field

from core.categories import resolve_category
from core.identity import IdentityIndex, link_resources
from core.resource_state import resource_state
from ingest.csv_inputs import load_activity, load_identities, load_identity_changes, load_rules
from ingest.loader import load_store
from planner import order_steps
from policy import decide, rule_actions


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    subject: object = None
    links: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Auditor:
    def __init__(self, dataset_dir):
        self.store = load_store(dataset_dir)
        self.activity = load_activity(dataset_dir)
        self.index = IdentityIndex(load_identities(dataset_dir), load_identity_changes(dataset_dir))
        self.actions = rule_actions(load_rules(dataset_dir))

    def audit(self, request):
        subject = self.index.resolve(request.email)
        if subject is None:
            return CaseResult(request.case_id, "INCOMPLETE", [], notes=["subject_unresolved"])
        links = link_resources(subject, request, self.store, self.index, self.activity)
        decisions = []
        for (system_id, resource_id), link in sorted(links.items()):
            state = resource_state(self.store, system_id, resource_id, request.audit_at)
            category, reason = resolve_category(resource_id, state.eligible_observations)
            decision = decide(system_id, resource_id, category, state, self.actions)
            decision.category_reason = reason
            decisions.append(decision)
        findings = [s for d in decisions for s in d.findings]
        if findings:
            # Remediation and post-remediation checks describe future work: no citation.
            future = [s for d in decisions for s in d.remediation + d.verification]
            for step in future:
                step.evidence_id = ""
            steps = findings + future
            status = "INCOMPLETE"
        else:
            steps = [s for d in decisions for s in d.verification]
            status = "COMPLETE"
        return CaseResult(request.case_id, status, order_steps(steps), subject, links, decisions)
