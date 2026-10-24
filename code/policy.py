"""Policy engine: apply ``deletion_rules.csv`` to one resource's state.

Each decision returns findings (cited), remediation and the final-state verification for the
resource, or marks the resource unresolved with a reason. Final statuses are decided here and in
``audit.py`` by code; the model is never asked to choose them.
"""

from dataclasses import dataclass, field

from core.model import ABSENT, PRESENT
from core.resource_state import exposure, flow_signal, latest_signal, restore_signal

FLOW = "DATA_FLOW"
BACKUP = "BACKUP_ARCHIVE"

VERIFY_FOR_ACTION = {
    "DELETE": ("VERIFY_RECORD_ABSENT", "ABSENT"),
    "REDACT": ("VERIFY_RECORD_REDACTED", "REDACTED"),
    "ANONYMIZE": ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    "RETAIN": ("VERIFY_RECORD_PRESENT", "PRESENT"),
}
REMEDIATE_FOR_ACTION = {
    "DELETE": ("DELETE_RECORD", "ABSENT"),
    "REDACT": ("REDACT_RECORD", "REDACTED"),
    "ANONYMIZE": ("ANONYMIZE_RECORD", "ANONYMIZED"),
}


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""


@dataclass
class Decision:
    system_id: str
    resource_id: str
    category: str = None
    action: str = None
    findings: list = field(default_factory=list)
    remediation: list = field(default_factory=list)
    verification: list = field(default_factory=list)
    unresolved: str = None


def rule_actions(rules):
    return {rule.data_category: rule.required_action for rule in rules}


def decide(system_id, resource_id, category, state, actions):
    d = Decision(system_id, resource_id, category)
    if category is None:
        d.unresolved = "category"
        return d

    def finding(op, st, evidence):
        d.findings.append(Step("FINDING", system_id, resource_id, op, st, evidence))

    def remediate(op, st):
        d.remediation.append(Step("REMEDIATION", system_id, resource_id, op, st))

    def verify(op, st, evidence):
        d.verification.append(Step("VERIFICATION", system_id, resource_id, op, st, evidence))

    observations = state.eligible_observations
    if category == FLOW:
        value, obs = latest_signal(observations, flow_signal)
        if value == "active":
            finding("REPORT_ACTIVE_DATA_FLOW", "ACTIVE", obs.evidence_id)
            remediate("DISABLE_DATA_FLOW", "DISABLED")
            verify("VERIFY_DATA_FLOW_DISABLED", "DISABLED", "")
        elif value == "disabled":
            verify("VERIFY_DATA_FLOW_DISABLED", "DISABLED", obs.evidence_id)
        else:
            d.unresolved = "flow_state"
        return d
    if category == BACKUP:
        value, obs = latest_signal(observations, restore_signal)
        if value == "risk":
            finding("REPORT_RESTORE_RISK", "ACTIVE", obs.evidence_id)
            remediate("BLOCK_RESTORE", "BLOCKED")
            verify("VERIFY_RESTORE_BLOCKED", "BLOCKED", "")
        elif value == "blocked":
            verify("VERIFY_RESTORE_BLOCKED", "BLOCKED", obs.evidence_id)
        else:
            d.unresolved = "restore_state"
        return d

    action = actions.get(category)
    d.action = action
    if action is None:
        d.unresolved = f"no_rule:{category}"
        return d
    verify_op, verify_state = VERIFY_FOR_ACTION[action]

    if action == "RETAIN":
        if state.presence == PRESENT:
            verify(verify_op, verify_state, state.evidence_id)
        else:
            d.unresolved = f"retained_record_{state.presence.lower()}"
        return d

    if action == "DELETE":
        if state.presence == PRESENT:
            finding("REPORT_REMAINING_RECORD", "PRESENT", state.evidence_id)
            remediate(*REMEDIATE_FOR_ACTION[action])
            verify(verify_op, verify_state, "")
        elif state.presence == ABSENT:
            verify(verify_op, verify_state, state.evidence_id)
        else:
            verify(verify_op, verify_state, "")
            d.unresolved = "presence_unknown"
        return d

    # REDACT / ANONYMIZE: the record stays; only identifiers matter.
    if state.presence != PRESENT:
        d.unresolved = f"treated_record_{state.presence.lower()}"
        return d
    include_user_id = action == "ANONYMIZE"
    results = [(exposure(o, include_user_id), o) for o in state.present_observations]
    exposed = [o for r, o in results if r == "exposed"]
    if exposed:
        finding("REPORT_EXPOSED_PII", "PRESENT", exposed[0].evidence_id)
        remediate(*REMEDIATE_FOR_ACTION[action])
        verify(verify_op, verify_state, "")
        return d
    treated = [o for r, o in results if r == "treated"]
    if treated and len(treated) == len(results):
        verify(verify_op, verify_state, treated[0].evidence_id)
    else:
        verify(verify_op, verify_state, "")
        d.unresolved = "identifier_fields_unobserved"
    return d
