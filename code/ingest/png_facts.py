"""Convert validated screenshot extractions into observations.

Every value proposed by the vision model is re-checked in code:
* ``resource_id`` must match a known resource family (``RESOURCE_ID_RE``); records without one
  become a ``DocumentExtract`` row only;
* identifiers are kept only if their shape matches their type (email, user id, phone, hash);
* presence is PRESENT only for ``SHOWN`` and ABSENT only for explicit ``NOT_FOUND``/``DELETED``.
Observations are a ``document``-role copy (the screen is not a named physical copy) observed at the
manifest capture time, so the approved cutoffs apply unchanged.
"""

from core.model import ABSENT, PRESENT, CopyKey, DocumentExtract, IngestIssue, Observation, ParseResult
from core.vocab import EMAIL_RE, HASH_RE, PHONE_RE, RESOURCE_ID_RE, USER_RE, normalize_identifier, system_for_resource

SCREEN = CopyKey("document")
PRESENCE = {"SHOWN": PRESENT, "NOT_FOUND": ABSENT, "DELETED": ABSENT}
SHAPES = {"emails": ("email", EMAIL_RE), "user_ids": ("user_id", USER_RE), "phones": ("phone", PHONE_RE),
          "email_hashes": ("email_hash", HASH_RE)}


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
        result.observations.append(
            Observation(
                evidence_id=evidence.file_id,
                source_kind="png",
                locator=f"records[{index}]",
                system_id=system_for_resource(resource_id),
                resource_id=resource_id,
                copy_key=SCREEN,
                observed_at=evidence.captured_at,
                captured_at=evidence.captured_at,
                presence=PRESENCE.get(record.get("presence")),
                identifiers=identifiers,
                identifier_fields={k: record.get(k) for k in SHAPES} | {"names": names},
                attributes={"record_kind": record.get("record_kind", ""), "screen_title": extraction.get("screen_title", ""),
                            "related_resource_ids": related, **{f"field:{k}": v for k, v in fields.items()}},
                raw_state={"presence": record.get("presence"), "extraction_source": source},
            )
        )
    result.documents.append(DocumentExtract(evidence.file_id, extraction.get("screen_title", ""), evidence.captured_at,
                                            evidence.captured_at, {"application": extraction.get("application", ""),
                                                                   "visible_timestamp": extraction.get("visible_timestamp", "")},
                                            rows))
    result.copy_keys |= {o.copy_key for o in result.observations}
    return result
