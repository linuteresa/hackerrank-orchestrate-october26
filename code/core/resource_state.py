"""Resource-level state at a cutoff, built from per-copy facts.

Two small functions hold the assumptions that Phase 1 left open:

* ``copy_class`` (copy matching, H-2b / H-2d): explicitly named physical copies (``primary``,
  ``*_replica``) form one class per ``(database, role, region, instance)`` across files; every
  other source (logical API reads, records bundles, inventories, captures, documents) forms one
  *service view* of the resource. So a logical read can update the service view but never a
  named physical replica, and a later snapshot of the same named table supersedes an earlier one.
* ``coverage_admissible`` (H-COV): which coverage bases may turn omission into absence.

Resource presence: PRESENT if the latest fact of any class is PRESENT (a compliant copy never
hides a non-compliant one); ABSENT if every class with facts ends absent / not-listed; UNKNOWN
otherwise. A positive logical read establishes that a record remains available without
identifying its physical copy.
"""

from dataclasses import dataclass, field

from core.model import (
    ABSENT,
    C1_DECLARED_COLLECTION,
    C2_TYPED_COPY_TABLE,
    C3_SCOPE_TABLE,
    NOT_LISTED,
    PRESENT,
    UNKNOWN,
)

PHYSICAL_ROLES = frozenset({"primary", "read_replica", "reporting_replica", "failover_replica", "replica"})
ADMISSIBLE_BASES = frozenset({C1_DECLARED_COLLECTION, C2_TYPED_COPY_TABLE, C3_SCOPE_TABLE})


def copy_class(copy_key):
    if copy_key.role in PHYSICAL_ROLES:
        return ("physical", copy_key.database, copy_key.role, copy_key.region, copy_key.instance)
    return ("service",)


def coverage_admissible(coverage):
    return coverage.basis in ADMISSIBLE_BASES


@dataclass(frozen=True)
class Fact:
    observed_at: object
    state: str  # PRESENT | ABSENT | NOT_LISTED
    evidence_id: str
    explicit: bool
    observation: object = None


@dataclass
class ResourceState:
    presence: str
    evidence_id: str = ""
    present_observations: list = field(default_factory=list)  # latest PRESENT obs per present class
    eligible_observations: list = field(default_factory=list)
    classes: dict = field(default_factory=dict)  # class -> latest Fact


def resource_state(store, system_id, resource_id, audit_at):
    observations = store.observations_for(system_id, resource_id, audit_at)
    facts = {}
    for obs in observations:
        if obs.presence in (PRESENT, ABSENT):
            facts.setdefault(copy_class(obs.copy_key), []).append(
                Fact(obs.observed_at, obs.presence, obs.evidence_id, True, obs))
    for cov in store.relevant_coverage(system_id, resource_id, audit_at):
        klass = copy_class(cov.copy_key)
        if resource_id in cov.listed or not coverage_admissible(cov):
            continue
        # Omission is weaker than an explicit statement: it only speaks for a copy class that
        # has no explicit eligible observation of this resource (H-COV, sample-measured).
        if any(f.explicit for f in facts.get(klass, ())):
            continue
        facts.setdefault(klass, []).append(Fact(cov.observed_at, NOT_LISTED, cov.evidence_id, False))

    latest = {}
    for klass, items in facts.items():
        top = max(f.observed_at for f in items)
        winners = [f for f in items if f.observed_at == top]
        # Conservative tie-break: if facts at the same instant disagree, the record may remain.
        present = [f for f in winners if f.state == PRESENT]
        explicit = [f for f in winners if f.explicit]
        latest[klass] = (present or explicit or winners)[0]

    state = ResourceState(UNKNOWN, eligible_observations=observations, classes=latest)
    present = [f for f in latest.values() if f.state == PRESENT]
    if present:
        newest = max(present, key=lambda f: f.observed_at)
        state.presence, state.evidence_id = PRESENT, newest.evidence_id
        state.present_observations = sorted((f.observation for f in present), key=lambda o: o.observed_at, reverse=True)
        return state
    if latest:
        explicit = [f for f in latest.values() if f.explicit]
        cite = max(explicit or latest.values(), key=lambda f: f.observed_at)
        state.presence, state.evidence_id = ABSENT, cite.evidence_id
    return state


# ---- signals ----------------------------------------------------------------------------

DIRECT_KINDS = ("email", "phone", "name", "email_hash")


def exposure(obs, include_user_id):
    """'exposed' | 'treated' | None (no identifier fields observed: missing is not proof)."""
    reported = (obs.attributes.get("reported_state") or "").upper()
    if reported == "IDENTIFIABLE":
        return "exposed"
    if reported in ("ANONYMIZED", "REDACTED"):
        return "treated"
    direct = obs.attributes.get("direct_identifiers")
    if direct is not None and str(direct).isdigit():
        return "exposed" if int(direct) > 0 else "treated"
    kinds = DIRECT_KINDS + (("user_id",) if include_user_id else ())
    observed = [k for k in kinds if k in obs.identifiers]
    if any(obs.identifiers[k] for k in observed):
        return "exposed"
    if obs.attributes.get("matched_value") or obs.identifier_fields.get("matched_value"):
        return "exposed"
    if observed:
        return "treated"
    return None


def flow_signal(obs):
    """'active' | 'disabled' | None."""
    enabled = obs.attributes.get("enabled")
    if enabled is True:
        return "active"
    if enabled is False:
        return "disabled"
    reported = (obs.attributes.get("reported_state") or "").upper()
    if reported == "ACTIVE":
        return "active"
    if reported == "DISABLED":
        return "disabled"
    return None


def restore_signal(obs):
    """'risk' | 'blocked' | None."""
    allowed = obs.attributes.get("restore_allowed")
    if allowed is True:
        return "risk"
    if allowed is False:
        return "blocked"
    if obs.attributes.get("deletion_block") is True:
        return "blocked"
    if obs.attributes.get("eligible") is True and obs.attributes.get("deletion_block") is False:
        return "risk"
    decision = (obs.attributes.get("decision") or "").lower()
    if decision == "deny":
        return "blocked"
    if decision == "allow":
        return "risk"
    reported = (obs.attributes.get("reported_state") or "").upper()
    if reported == "ACTIVE":
        return "risk"
    if reported == "BLOCKED":
        return "blocked"
    return None


def latest_signal(observations, signal):
    """Latest observation carrying a signal; ties resolved toward the risky value."""
    found = [(o.observed_at, signal(o), o) for o in observations if signal(o)]
    if not found:
        return None, None
    top = max(t for t, _, _ in found)
    at_top = [(s, o) for t, s, o in found if t == top]
    risky = [x for x in at_top if x[0] in ("active", "risk", "exposed")]
    value, obs = (risky or at_top)[0]
    return value, obs
