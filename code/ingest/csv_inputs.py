"""Typed loaders for the participant-facing CSV inputs.

``system_activity.csv`` rows are loaded as ``ActivityClaim``s. They are workflow claims
(``reported_result`` is not proof) and never enter the observation store.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from core.timeutil import parse_ts


@dataclass(frozen=True)
class ActivityClaim:
    activity_id: str
    event_time: object
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class DeletionRequest:
    case_id: str
    email: str
    requested_at: object
    reported_status: str
    reported_at: object
    audit_at: object
    request_text: str


@dataclass(frozen=True)
class Identity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: object


@dataclass(frozen=True)
class IdentityChange:
    audit_id: str
    user_id: str
    event_time: object
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


def _rows(dataset_dir, name):
    with open(Path(dataset_dir) / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_activity(dataset_dir):
    return [
        ActivityClaim(r["activity_id"], parse_ts(r["event_time"]), r["event_type"], r["source_system"],
                      r["target_system"], r["resource_id"], r["reported_result"], r["details"])
        for r in _rows(dataset_dir, "system_activity.csv")
    ]


def load_requests(dataset_dir, name="deletion_requests.csv"):
    return [
        DeletionRequest(r["case_id"], r["email"], parse_ts(r["requested_at"]), r["reported_status"],
                        parse_ts(r["reported_at"]), parse_ts(r["audit_at"]), r["request_text"])
        for r in _rows(dataset_dir, name)
    ]


def load_identities(dataset_dir):
    return [
        Identity(r["user_id"], r["name"], r["email"], r["phone"], r["email_hash"], parse_ts(r["updated_at"]))
        for r in _rows(dataset_dir, "user_identities.csv")
    ]


def load_identity_changes(dataset_dir):
    return [
        IdentityChange(r["audit_id"], r["user_id"], parse_ts(r["event_time"]), r["field_name"], r["old_value"],
                       r["new_value"])
        for r in _rows(dataset_dir, "user_audit_log.csv")
    ]


def load_rules(dataset_dir):
    return [DeletionRule(r["rule_id"], r["data_category"], r["condition"], r["required_action"])
            for r in _rows(dataset_dir, "deletion_rules.csv")]
