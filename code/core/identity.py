"""Identity resolution: who is the subject, and which resources are theirs.

Every resource-to-person link records *why* it was made (``Link.reasons``). Link kinds:

* ``identifier``  - an eligible observation's record content carries a strong identifier of the
                    subject (email, phone, email hash or user id);
* ``lookup``      - an eligible observation was found with a strong identifier as lookup key
                    (trace-joined or on the same line), observed within the request lifecycle
                    (``observed_at >= requested_at``);
* ``catalog``     - a ``LINK_RESOURCE_IDENTITY`` activity names a strong identifier of the subject;
* ``window``      - a deletion-worker DELETE of an account inside the request window
                    (``requested_at <= t <= reported_at``). Timing alone is never enough: the link
                    is made only when exactly one window account is not strongly linked to anybody
                    else (corroboration by exclusion), and only if no account is linked otherwise.

Names are weak: a name match alone never creates a link.
"""

import hashlib
import re
from dataclasses import dataclass, field

from core.vocab import EMAIL_IN_TEXT_RE

STRONG_KINDS = ("email", "phone", "email_hash", "user_id")
USER_IN_TEXT_RE = re.compile(r"\buser_\d+\b")


def norm_phone(value):
    return re.sub(r"\D", "", value or "")


def norm_value(kind, value):
    if value is None:
        return None
    if kind == "phone":
        return norm_phone(value)
    return str(value).strip().lower()


def email_hash(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


@dataclass
class Subject:
    user_id: str
    request_email: str
    match: str  # current | historical
    strong: dict = field(default_factory=dict)  # kind -> set(normalized values)
    names: set = field(default_factory=set)

    def matches(self, kind, value):
        return norm_value(kind, value) in self.strong.get(kind, ())


@dataclass
class Link:
    system_id: str
    resource_id: str
    reasons: list = field(default_factory=list)  # (kind, evidence_id, locator, detail)

    @property
    def kinds(self):
        return {r[0] for r in self.reasons}


class IdentityIndex:
    """Current and historical identifiers of every user, from the participant-facing CSVs."""

    def __init__(self, identities, changes):
        self.users = {}
        self.current_email = {}
        for ident in identities:
            self.current_email[ident.user_id] = norm_value("email", ident.email)
            strong = {
                "email": {norm_value("email", ident.email)},
                "phone": {norm_value("phone", ident.phone)},
                "email_hash": {norm_value("email_hash", ident.email_hash)},
                "user_id": {ident.user_id},
            }
            self.users[ident.user_id] = (strong, {ident.name})
        for change in changes:
            if change.user_id not in self.users:
                continue
            kind = change.field_name
            if kind in ("email", "phone", "email_hash"):
                strong = self.users[change.user_id][0]
                for value in (change.old_value, change.new_value):
                    if value:
                        strong[kind].add(norm_value(kind, value))
        for user_id, (strong, _) in self.users.items():
            for email in list(strong["email"]):
                strong["email_hash"].add(email_hash(email))
        self.owner = {}
        for user_id, (strong, _) in self.users.items():
            for kind, values in strong.items():
                for value in values:
                    self.owner.setdefault((kind, value), set()).add(user_id)

    def resolve(self, request_email):
        email = norm_value("email", request_email)
        owners = self.owner.get(("email", email), set())
        if len(owners) != 1:
            return None
        user_id = next(iter(owners))
        strong, names = self.users[user_id]
        match = "current" if self.current_email[user_id] == email else "historical"
        return Subject(user_id, request_email, match, {k: set(v) for k, v in strong.items()}, set(names))

    def owners_of(self, kind, value):
        return self.owner.get((kind, norm_value(kind, value)), set())


def strong_values(obs):
    """(kind, value, source) for every strong identifier carried by an observation."""
    out = []
    for kind in STRONG_KINDS:
        for value in obs.identifiers.get(kind, ()):
            out.append((kind, value, "identifier"))
    for key in obs.lookup_keys:
        if key.kind in STRONG_KINDS:
            out.append((key.kind, key.value, "lookup"))
    return out


def discoverable(obs, audit_at):
    """Gate for using an observation to discover resources (same inclusive cutoff as state)."""
    return obs.captured_at <= audit_at and obs.observed_at <= audit_at


def link_resources(subject, request, store, index, activity):
    """Return ``{(system_id, resource_id): Link}`` for the subject at the request's cutoff."""
    audit_at = request.audit_at
    links = {}

    def add(system_id, resource_id, reason):
        link = links.setdefault((system_id, resource_id), Link(system_id, resource_id))
        link.reasons.append(reason)

    for obs in store.observations:
        if not discoverable(obs, audit_at):
            continue
        for kind, value, source in strong_values(obs):
            if not subject.matches(kind, value):
                continue
            # A lookup is a query event: it speaks for this request only inside its lifecycle.
            if source == "lookup" and obs.observed_at < request.requested_at:
                continue
            add(obs.system_id, obs.resource_id, (source, obs.evidence_id, obs.locator, f"{kind}={value}"))
            break

    for claim in activity:
        if claim.event_type != "LINK_RESOURCE_IDENTITY" or claim.event_time > audit_at:
            continue
        emails = {e.lower() for e in EMAIL_IN_TEXT_RE.findall(claim.details)}
        users = set(USER_IN_TEXT_RE.findall(claim.details))
        if emails & subject.strong["email"] or users & subject.strong["user_id"]:
            add(claim.target_system, claim.resource_id, ("catalog", claim.activity_id, "details", claim.details[:80]))

    if not any(system == "account_service" for system, _ in links):
        account = _window_account(subject, request, store, index, activity)
        if account:
            add("account_service", account[0], ("window", account[1], "activity", "exclusive window account"))
    return links


def _window_account(subject, request, store, index, activity):
    candidates = [
        c for c in activity
        if c.source_system == "deletion_worker" and c.target_system == "account_service"
        and c.event_type == "DELETE_RECORD" and request.requested_at <= c.event_time <= request.reported_at
        and c.event_time <= request.audit_at
    ]
    free = []
    for claim in candidates:
        owners = set()
        for obs in store.observations_for("account_service", claim.resource_id):
            if not discoverable(obs, request.audit_at):
                continue
            for kind, value, _ in strong_values(obs):
                owners |= index.owners_of(kind, value)
        if not owners - {subject.user_id}:
            free.append(claim)
    resources = {c.resource_id for c in free}
    if len(resources) == 1:
        claim = free[0]
        return claim.resource_id, claim.activity_id
    return None
