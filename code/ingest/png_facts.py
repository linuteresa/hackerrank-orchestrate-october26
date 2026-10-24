"""Convert validated screenshot extractions into observations.

Every value proposed by the vision model is re-checked in code:
* ``resource_id`` must match a known resource family (``RESOURCE_ID_RE``); records without one
  become a ``DocumentExtract`` row only;
* identifiers are kept only if their shape matches their type (email, user id, phone, hash);
* presence is PRESENT only for ``SHOWN`` and ABSENT only for explicit ``NOT_FOUND``/``DELETED``.
Observations are a ``document``-role copy (the screen is not a named physical copy) observed at the
manifest capture time, so the approved cutoffs apply unchanged.

Visible label/value pairs are kept verbatim as ``field:<label>`` attributes. A small, closed vocabulary
of control labels is normalized into the attributes the flow and restore readers consume
(``enabled``, ``restore_allowed``); anything outside it (health, encryption, retention, checksums) is
never read as a control state.
"""

import re

from core.model import ABSENT, PRESENT, CopyKey, DocumentExtract, IngestIssue, Observation, ParseResult
from core.vocab import EMAIL_RE, HASH_RE, PHONE_RE, RESOURCE_ID_RE, USER_RE, normalize_identifier, system_for_resource

SCREEN = CopyKey("document")
PRESENCE = {"SHOWN": PRESENT, "NOT_FOUND": ABSENT, "DELETED": ABSENT}
SHAPES = {"emails": ("email", EMAIL_RE), "user_ids": ("user_id", USER_RE), "phones": ("phone", PHONE_RE),
          "email_hashes": ("email_hash", HASH_RE)}

# Connector run-state labels and values. "Healthy" / "Degraded" describe health, not whether the
# connector runs, and are deliberately absent.
FLOW_LABELS = frozenset({"mode", "sync mode", "sync", "status", "state", "connector status", "run state"})
FLOW_ACTIVE = frozenset({"continuous", "continuous sync", "real time", "realtime", "streaming", "active",
                         "enabled", "running", "scheduled"})
FLOW_DISABLED = frozenset({"disabled", "paused", "stopped", "inactive", "off", "suspended", "deactivated"})
# Restore-control labels. Encryption, retention, checksums and drills say nothing about whether the
# subject's data can be restored and are deliberately absent.
RESTORE_LABELS = frozenset({"restore", "restore allowed", "restore access", "restore status", "restorable",
                            "restore policy"})
RESTORE_BLOCK_LABELS = frozenset({"restore block", "restore blocked", "deletion block"})
YES = frozenset({"yes", "true", "allowed", "enabled", "permitted", "on"})
NO = frozenset({"no", "false", "denied", "blocked", "disabled", "off", "not allowed"})


def normalize_label(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def control_signals(system_id, fields):
    """Canonical control attributes from visible screen fields; conflicting values give nothing."""
    pairs = [(normalize_label(k), normalize_label(v)) for k, v in fields.items()]
    out = {}
    if system_id == "integration_service":
        states = {True for k, v in pairs if k in FLOW_LABELS and v in FLOW_ACTIVE}
        states |= {False for k, v in pairs if k in FLOW_LABELS and v in FLOW_DISABLED}
        if len(states) == 1:
            out["enabled"] = states.pop()
    if system_id == "backup_service":
        allowed = {True for k, v in pairs if k in RESTORE_LABELS and v in YES}
        allowed |= {False for k, v in pairs if k in RESTORE_LABELS and v in NO}
        allowed |= {False for k, v in pairs if k in RESTORE_BLOCK_LABELS and v in YES}
        allowed |= {True for k, v in pairs if k in RESTORE_BLOCK_LABELS and v in NO}
        if len(allowed) == 1:
            out["restore_allowed"] = allowed.pop()
    return out


def parse_png_facts(evidence, extraction, source):
    result = ParseResult()
    if extraction is None:
        result.issues.append(IngestIssue(evidence.file_id, "png_not_extracted", evidence.path, str(source)))
        return result
    rows = []
    for index, record in enumerate(extraction.get("records", [])):
        identifiers, rejected = {}, []
        for key, (kind, shape) in SHAPES.items():
            for value in record.get(key, []):
                value = (value or "").strip()
                if shape.match(value):
                    identifiers.setdefault(kind, set()).add(normalize_identifier(kind, value))
                elif value:
                    rejected.append((key, value))
        names = [n.strip() for n in record.get("names", []) if n.strip()]
        if names:
            identifiers["name"] = set(names)
        identifiers = {k: tuple(sorted(v)) for k, v in identifiers.items()}
        for key, value in rejected:
            result.issues.append(IngestIssue(evidence.file_id, "png_value_rejected", f"records[{index}].{key}", value))
        fields = {f["name"]: f["value"] for f in record.get("fields", []) if f.get("name")}
        related = [r for r in record.get("related_resource_ids", []) if RESOURCE_ID_RE.match(r.strip())]
        rows.append({"resource_id": record.get("resource_id", ""), "record_kind": record.get("record_kind", ""),
                     "presence": record.get("presence"), "identifiers": identifiers, "fields": fields,
                     "related_resource_ids": related})
        resource_id = (record.get("resource_id") or "").strip()
        if not RESOURCE_ID_RE.match(resource_id):
            continue
        system_id = system_for_resource(resource_id)
        result.observations.append(
            Observation(
                evidence_id=evidence.file_id,
                source_kind="png",
                locator=f"records[{index}]",
                system_id=system_id,
                resource_id=resource_id,
                copy_key=SCREEN,
                observed_at=evidence.captured_at,
                captured_at=evidence.captured_at,
                presence=PRESENCE.get(record.get("presence")),
                identifiers=identifiers,
                identifier_fields={k: record.get(k) for k in SHAPES} | {"names": names},
                attributes={"record_kind": record.get("record_kind", ""), "screen_title": extraction.get("screen_title", ""),
                            "related_resource_ids": related, **{f"field:{k}": v for k, v in fields.items()},
                            **control_signals(system_id, fields)},
                raw_state={"presence": record.get("presence"), "extraction_source": source},
            )
        )
    result.documents.append(DocumentExtract(evidence.file_id, extraction.get("screen_title", ""), evidence.captured_at,
                                            evidence.captured_at, {"application": extraction.get("application", ""),
                                                                   "visible_timestamp": extraction.get("visible_timestamp", "")},
                                            rows))
    result.copy_keys |= {o.copy_key for o in result.observations}
    return result


CATALOG_ALIAS_RE = re.compile(r"^\S+ catalog (?:linked|confirmed) (\S+) \(([^)]+)\)")


def catalog_aliases(activity):
    """``(claim, resource_id, alias)`` for data-catalog claims naming a resource by another name."""
    out = []
    for claim in activity:
        if claim.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        match = CATALOG_ALIAS_RE.match(claim.details)
        if match and match.group(1) == claim.resource_id and match.group(2) != claim.resource_id:
            out.append((claim, claim.resource_id, match.group(2)))
    return out


def alias_observations(store, activity, category_for_text):
    """Screen rows joined to a catalogued resource by its exact alias (e.g. a file name).

    The join is accepted only within the claim's own system (the screenshot's manifest origin) and
    only when exactly one screen row carries the alias; the resource's owner still comes from the
    claim and its normal link checks. The fact becomes knowable when both the screen and the claim
    exist, so ``captured_at`` is the later of the two and the approved cutoffs apply unchanged.
    """
    observations = []
    for claim, resource_id, alias in catalog_aliases(activity):
        matches = [(doc, index, row) for doc in store.documents
                   if store.files[doc.evidence_id].kind == "png" and store.files[doc.evidence_id].origin == claim.target_system
                   for index, row in enumerate(doc.rows) if (row.get("resource_id") or "").strip() == alias]
        if len(matches) != 1:
            if len(matches) > 1:
                store.issues.append(IngestIssue(matches[0][0].evidence_id, "alias_ambiguous", alias, claim.activity_id))
            continue
        doc, index, row = matches[0]
        category = category_for_text(claim.details)
        observations.append(Observation(
            evidence_id=doc.evidence_id,
            source_kind="png",
            locator=f"records[{index}]",
            system_id=claim.target_system,
            resource_id=resource_id,
            copy_key=SCREEN,
            observed_at=doc.observed_at,
            captured_at=max(doc.captured_at, claim.event_time),
            presence=PRESENCE.get(row.get("presence")),
            data_category=category,
            category_source="explicit" if category else None,
            identifiers=row.get("identifiers", {}),
            attributes={"record_kind": row.get("record_kind", ""), "alias": alias, "alias_claim": claim.activity_id,
                        **{f"field:{k}": v for k, v in row.get("fields", {}).items()}},
            raw_state={"presence": row.get("presence"), "join": "catalog_alias"},
        ))
    return observations
