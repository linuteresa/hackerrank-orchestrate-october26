"""Output contract validation for ``output.csv`` rows."""

import json

from planner import OPERATION_ORDER, PHASES

OPERATION_PHASE_STATE = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate_row(case_id, status, steps_json, cite_ok=None):
    """Return a list of contract violations for one output row.

    ``cite_ok(evidence_id)`` optionally checks that a cited id exists and is not after the cutoff.
    """
    errors = []
    if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        errors.append(f"bad status {status}")
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return [f"invalid json: {exc}"]
    if not isinstance(steps, list):
        return ["steps_json is not an array"]
    if status == "NOT_APPLICABLE" and steps:
        errors.append("NOT_APPLICABLE must have []")
    previous = None
    for i, step in enumerate(steps, 1):
        if set(step) != FIELDS:
            errors.append(f"step {i}: fields {sorted(step)}")
            continue
        if step["step_no"] != i:
            errors.append(f"step {i}: step_no {step['step_no']}")
        op = step["operation"]
        if op not in OPERATION_PHASE_STATE:
            errors.append(f"step {i}: unknown operation {op}")
            continue
        phase, state = OPERATION_PHASE_STATE[op]
        if step["phase"] != phase or step["state"] != state:
            errors.append(f"step {i}: {op} with {step['phase']}/{step['state']}")
        key = (PHASES.index(phase), OPERATION_ORDER[op], step["system_id"], step["resource_id"])
        if previous is not None and key < previous:
            errors.append(f"step {i}: out of order")
        previous = key
        cited = step["evidence_id"]
        future = status == "INCOMPLETE" and phase != "FINDING"
        if future and cited:
            errors.append(f"step {i}: future step cites {cited}")
        if not future and not cited:
            errors.append(f"step {i}: missing evidence")
        if cited and cite_ok and not cite_ok(cited):
            errors.append(f"step {i}: ineligible citation {cited}")
    if status == "COMPLETE" and any(s.get("phase") != "VERIFICATION" for s in steps):
        errors.append("COMPLETE with non-verification steps")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
        errors.append("INCOMPLETE without findings")
    return errors
