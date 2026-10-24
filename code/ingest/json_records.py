"""JSON evidence, detected by structure (file names are not trusted).

Shapes:

* bundle        ``{exported_at, snapshots: [{captured_at, data_store, collection, records}]}``
                - C1 coverage per snapshot; records observed at that snapshot's own time;
* capture       ``{captured_at, collection, records}`` - C1 coverage, system from manifest origin;
* lookup export ``{exported_at, service_exports: {system: {section: [rows with observed_at]}}}``
                - targeted lookups, no coverage;
* ledger        ``{exported_at, records: [{recorded_at, system, resource_id, record_type, result,
                subject_reference}]}`` - per-row results, no coverage.

``operational_record_history`` collections inside bundles are ledger rows about *other*
systems' resources and are parsed as ledger rows (their own C1 coverage concerns only the
history collection itself).
"""

import json

from core.model import (
    ABSENT,
    C1_DECLARED_COLLECTION,
    COLLECTION_CAPTURE,
    LINK_SAME_LINE,
    LOGICAL,
    PRESENT,
    RECORDS_BUNDLE,
    Coverage,
    IngestIssue,
    LookupKey,
    Observation,
    ParseResult,
)
from core.timeutil import parse_ts
from core.vocab import (
    COLLECTION_CATEGORY,
    KNOWN_SYSTEMS,
    classify_value,
    collect_identifiers,
    flatten_record,
    ledger_presence,
    normalize_identifier,
    null_if_text_null,
    system_for_resource,
)

LEDGER_COLLECTION = "operational_record_history"


def parse_json(evidence, text):
    result = ParseResult()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        result.issues.append(IngestIssue(evidence.file_id, "bad_json", evidence.path, str(exc)))
        return result
    if isinstance(doc, dict) and "snapshots" in doc:
        result.stats["shape:bundle"] = 1
        for index, snap in enumerate(doc["snapshots"]):
            _snapshot(evidence, snap, f"snapshots[{index}]", RECORDS_BUNDLE, snap["data_store"], result)
    elif isinstance(doc, dict) and "service_exports" in doc:
        result.stats["shape:lookup_export"] = 1
        _service_exports(evidence, doc["service_exports"], result)
    elif isinstance(doc, dict) and "collection" in doc and "records" in doc:
        result.stats["shape:capture"] = 1
        system = evidence.origin if evidence.origin in KNOWN_SYSTEMS else _uniform_system(doc["records"])
        if system is None:
            result.issues.append(IngestIssue(evidence.file_id, "capture_system_unknown", evidence.path, doc["collection"]))
        else:
            _snapshot(evidence, doc, "$", COLLECTION_CAPTURE, system, result)
    elif isinstance(doc, dict) and "records" in doc:
        result.stats["shape:ledger"] = 1
        for index, row in enumerate(doc["records"]):
            _ledger_row(evidence, row, f"records[{index}]", result)
    else:
        result.issues.append(IngestIssue(evidence.file_id, "unrecognized_json_shape", evidence.path, ",".join(doc)))
    result.copy_keys |= {o.copy_key for o in result.observations} | {c.copy_key for c in result.coverage}
    return result


def _uniform_system(records):
    systems = {system_for_resource(r.get("resource_id", "")) for r in records}
    return systems.pop() if len(systems) == 1 else None


def _record_observation(evidence, locator, system, collection, record, observed_at, copy_key, snapshot_scoped,
                        presence=PRESENT, lookup_keys=()):
    flat = flatten_record(record)
    identifiers, identifier_fields = collect_identifiers(flat)
    attributes = {
        k: v for k, v in flat.items()
        if k not in identifier_fields and k != "resource_id" and "." not in k
    }
    category = COLLECTION_CATEGORY.get(collection)
    return Observation(
        evidence_id=evidence.file_id,
        source_kind="json",
        locator=locator,
        system_id=system,
        resource_id=record["resource_id"],
        copy_key=copy_key,
        observed_at=observed_at,
        captured_at=evidence.captured_at,
        presence=presence,
        collection=collection,
        data_category=category,
        category_source="collection_map" if category else None,
        identifiers=identifiers,
        identifier_fields=identifier_fields,
        attributes=attributes,
        snapshot_scoped=snapshot_scoped,
        lookup_keys=tuple(lookup_keys),
    )


def _snapshot(evidence, snap, locator, copy_key, system, result):
    observed_at = parse_ts(snap["captured_at"])
    collection = snap["collection"]
    if collection == LEDGER_COLLECTION:
        for index, row in enumerate(snap["records"]):
            _ledger_row(evidence, row, f"{locator}.records[{index}]", result)
    else:
        for index, record in enumerate(snap["records"]):
            result.observations.append(
                _record_observation(evidence, f"{locator}.records[{index}]", system, collection, record,
                                    observed_at, copy_key, True)
            )
    result.coverage.append(
        Coverage(
            evidence_id=evidence.file_id, system_id=system, collection=collection, copy_key=copy_key,
            basis=C1_DECLARED_COLLECTION, observed_at=observed_at, captured_at=evidence.captured_at,
            locator=locator, listed=frozenset(r["resource_id"] for r in snap["records"]),
        )
    )


def _lookup_key(field, value):
    value = null_if_text_null(value)
    if value is None:
        return None
    kind = classify_value(value)
    return LookupKey(normalize_identifier(kind, value), kind, field, LINK_SAME_LINE)


def _service_exports(evidence, exports, result):
    for system, sections in exports.items():
        for section, rows in sections.items():
            for index, row in enumerate(rows):
                locator = f"service_exports.{system}.{section}[{index}]"
                observed_at = parse_ts(row["observed_at"])
                record = {k: v for k, v in row.items() if k not in ("observed_at", "profile", "hits", "query", "status_code")}
                presence = PRESENT
                lookups = []
                if "status_code" in row:
                    presence = PRESENT if row["status_code"] == 200 else ABSENT if row["status_code"] == 404 else None
                    record.update(row.get("profile") or {})
                if "hits" in row:
                    hits = row["hits"]
                    presence = PRESENT if hits.get("total", 0) > 0 else ABSENT
                    for doc in hits.get("documents", []):
                        if doc.get("resource_id") == row["resource_id"]:
                            record.update({k: v for k, v in doc.items() if k != "resource_id"})
                    key = _lookup_key("query", row.get("query"))
                    if key:
                        lookups.append(key)
                obs = _record_observation(evidence, locator, system, None, record, observed_at, LOGICAL, False,
                                          presence=presence, lookup_keys=lookups)
                obs.attributes["section"] = section
                if "status_code" in row:
                    obs.raw_state["status_code"] = row["status_code"]
                if "hits" in row:
                    obs.raw_state["hits_total"] = row["hits"].get("total")
                if presence is None:
                    result.issues.append(IngestIssue(evidence.file_id, "unclassified_status", locator, str(row.get("status_code"))))
                result.observations.append(obs)


def _ledger_row(evidence, row, locator, result):
    system = row.get("system") or system_for_resource(row["resource_id"])
    presence = ledger_presence(row.get("result"))
    if presence is None:
        result.issues.append(IngestIssue(evidence.file_id, "unclassified_result", locator, str(row.get("result"))))
    subject = row.get("subject_reference", row.get("lookup_value"))
    record = {"subject_reference": subject}
    identifiers, identifier_fields = collect_identifiers(record)
    result.observations.append(
        Observation(
            evidence_id=evidence.file_id,
            source_kind="json",
            locator=locator,
            system_id=system,
            resource_id=row["resource_id"],
            copy_key=LOGICAL,
            observed_at=parse_ts(row["recorded_at"]),
            captured_at=evidence.captured_at,
            presence=presence,
            data_category=row.get("record_type"),
            category_source="explicit" if row.get("record_type") else None,
            identifiers=identifiers,
            identifier_fields=identifier_fields,
            attributes={"reported_state": row.get("result")},
            raw_state={"result": row.get("result")},
        )
    )
