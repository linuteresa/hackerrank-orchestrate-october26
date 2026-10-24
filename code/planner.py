"""Step ordering and serialization (problem statement ordering rules).

Phases: FINDING, REMEDIATION, VERIFICATION. Within a phase: the operation order of the allowed
operations table, then ``system_id``, then ``resource_id``. That table order already puts
``DISABLE_DATA_FLOW`` and ``BLOCK_RESTORE`` before record changes.
"""

import json

PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
OPERATION_ORDER = {
    op: i for i, op in enumerate((
        "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
        "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
        "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
    ))
}


def order_steps(steps):
    ordered = sorted(steps, key=lambda s: (PHASES.index(s.phase), OPERATION_ORDER[s.operation], s.system_id, s.resource_id))
    unique = []
    seen = set()
    for step in ordered:
        key = (step.phase, step.system_id, step.resource_id, step.operation)
        if key not in seen:
            seen.add(key)
            unique.append(step)
    return unique


def to_json(steps):
    payload = [
        {"step_no": i, "phase": s.phase, "system_id": s.system_id, "resource_id": s.resource_id,
         "operation": s.operation, "state": s.state, "evidence_id": s.evidence_id}
        for i, s in enumerate(steps, 1)
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
