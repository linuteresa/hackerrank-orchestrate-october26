"""SQLite-compatible SQL snapshot exports.

Each file is executed into an in-memory SQLite database (stdlib ``sqlite3``); SQL text is
never regex-parsed for data. Three table shapes occur:

* generic copy tables  ``<database>_<role>[_<region>]`` with ``record_json`` rows
  (e.g. ``customer_data_replica_eu_west``) - observations only, **no coverage**: a
  collection appearing in a table does not prove the table enumerates that collection;
* typed copy tables   ``<database>_<role>`` with one collection's columns
  (e.g. ``customer_profiles_failover_replica``) - C2 coverage for that copy;
* inventory tables    named after a collection, alongside ``snapshot_scope`` - C3 coverage
  for systems listed in ``snapshot_scope``; the physical copy is unresolved.

Every table yields its own ``CopyKey`` so replicas can never overwrite each other.
"""

import json
import re
import sqlite3

from core.model import (
    C2_TYPED_COPY_TABLE,
    C3_SCOPE_TABLE,
    INVENTORY,
    PRESENT,
    CopyKey,
    Coverage,
    IngestIssue,
    Observation,
    ParseResult,
)
from core.timeutil import ISO_PATTERN, parse_ts
from core.vocab import (
    COLLECTION_CATEGORY,
    INVENTORY_TABLE_SYSTEM,
    TYPED_DATABASE_SYSTEM,
    collect_identifiers,
    flatten_record,
    system_for_resource,
)

COPY_TABLE_RE = re.compile(
    r"^(?P<database>[a-z_]+?)_(?P<role>primary|read_replica|reporting_replica|failover_replica|replica)"
    r"(?:_(?P<region>[a-z]+_[a-z]+))?$"
)
HEADER_CAPTURED_RE = re.compile(r"^-- Captured at: (" + ISO_PATTERN + ")", re.M)
HEADER_TOPOLOGY_RE = re.compile(r"^-- Database topology: (\S+)", re.M)
COMPLETED_RE = re.compile(r"^-- snapshot_completed_at=(" + ISO_PATTERN + ")", re.M)
MODIFIED_COLUMNS = ("updated_at", "indexed_at")
BOOLEAN_COLUMNS = ("enabled", "restore_allowed")


def copy_key_for_table(table):
    match = COPY_TABLE_RE.match(table)
    if not match:
        return None
    return CopyKey(role=match["role"], database=match["database"], region=match["region"])


def parse_sql(evidence, text):
    result = ParseResult()
    header_captured = HEADER_CAPTURED_RE.search(text)
    completed = COMPLETED_RE.search(text)
    topology = HEADER_TOPOLOGY_RE.search(text)

    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
    except sqlite3.Error as exc:
        result.issues.append(IngestIssue(evidence.file_id, "sql_error", evidence.path, str(exc)))
        return result

    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    scope = {}
    if "snapshot_scope" in tables:
        for system, captured in con.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
            scope[system] = parse_ts(captured)

    # Snapshot observation time: header, else completion marker, else scope, else manifest.
    times = {
        "header": parse_ts(header_captured.group(1)) if header_captured else None,
        "completed": parse_ts(completed.group(1)) if completed else None,
    }
    times.update({f"scope:{k}": v for k, v in scope.items()})
    candidates = [t for t in times.values() if t is not None]
    snapshot_at = times["header"] or times["completed"] or (min(candidates) if candidates else evidence.captured_at)
    for label, value in times.items():
        if value is not None and value != evidence.captured_at:
            result.issues.append(
                IngestIssue(evidence.file_id, "snapshot_time_mismatch", label, f"{value} != manifest {evidence.captured_at}")
            )
    result.stats["snapshot_scope_systems"] = len(scope)

    for table in tables:
        if table == "snapshot_scope":
            continue
        columns = [c[1] for c in con.execute(f'PRAGMA table_info("{table}")')]
        cursor = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
        rows = [dict(zip(columns, values)) for values in cursor]
        copy_key = copy_key_for_table(table)
        if "record_json" in columns and copy_key is not None:
            _generic_table(evidence, table, copy_key, rows, snapshot_at, topology, result)
        elif copy_key is not None:
            _typed_copy_table(evidence, table, copy_key, rows, snapshot_at, result)
        elif table in INVENTORY_TABLE_SYSTEM:
            _inventory_table(evidence, table, rows, snapshot_at, scope, result)
        else:
            result.issues.append(IngestIssue(evidence.file_id, "unrecognized_table", table, ",".join(columns)))
    con.close()
    return result


def _observation(evidence, table, row_no, system, resource_id, copy_key, collection, record, snapshot_at, modified):
    flat = flatten_record(record)
    identifiers, identifier_fields = collect_identifiers(flat)
    attributes = {k: v for k, v in flat.items() if k not in identifier_fields and k != "resource_id"}
    for key in BOOLEAN_COLUMNS:
        if key in attributes and attributes[key] in (0, 1, "0", "1"):
            attributes[key] = str(attributes[key]) == "1"
    category = COLLECTION_CATEGORY.get(collection)
    return Observation(
        evidence_id=evidence.file_id,
        source_kind="sql",
        locator=f"table={table};row={row_no}",
        system_id=system,
        resource_id=resource_id,
        copy_key=copy_key,
        observed_at=snapshot_at,
        captured_at=evidence.captured_at,
        presence=PRESENT,
        collection=collection,
        data_category=category,
        category_source="collection_map" if category else None,
        identifiers=identifiers,
        identifier_fields=identifier_fields,
        attributes=attributes,
        last_modified_at=parse_ts(modified) if modified else None,
        snapshot_scoped=True,
        raw_state={"table": table},
    )


def _modified(row):
    for column in MODIFIED_COLUMNS:
        if row.get(column):
            return row[column]
    return None


def _generic_table(evidence, table, copy_key, rows, snapshot_at, topology, result):
    result.copy_keys.add(copy_key)
    for row_no, row in enumerate(rows, 1):
        try:
            record = json.loads(row["record_json"])
        except json.JSONDecodeError as exc:
            result.issues.append(IngestIssue(evidence.file_id, "bad_record_json", f"{table}:{row_no}", str(exc)))
            continue
        obs = _observation(
            evidence, table, row_no, row["origin_system"], row["resource_id"], copy_key,
            row["collection_name"], record, snapshot_at, _modified(row),
        )
        if topology:
            obs.raw_state["topology"] = topology.group(1)
        result.observations.append(obs)
    # Collection sightings are informational only; they never create coverage (H-2c withdrawn).
    result.stats["generic_tables"] = result.stats.get("generic_tables", 0) + 1


def _typed_copy_table(evidence, table, copy_key, rows, snapshot_at, result):
    system = TYPED_DATABASE_SYSTEM.get(copy_key.database)
    if system is None:
        result.issues.append(IngestIssue(evidence.file_id, "unmapped_typed_table", table, copy_key.database))
        return
    result.copy_keys.add(copy_key)
    listed = []
    for row_no, row in enumerate(rows, 1):
        record = {k: v for k, v in row.items() if k not in MODIFIED_COLUMNS}
        result.observations.append(
            _observation(evidence, table, row_no, system, row["resource_id"], copy_key,
                         copy_key.database, record, snapshot_at, _modified(row))
        )
        listed.append(row["resource_id"])
    result.coverage.append(
        Coverage(
            evidence_id=evidence.file_id, system_id=system, collection=copy_key.database, copy_key=copy_key,
            basis=C2_TYPED_COPY_TABLE, observed_at=snapshot_at, captured_at=evidence.captured_at,
            locator=f"table={table}", listed=frozenset(listed),
        )
    )


def _inventory_table(evidence, table, rows, snapshot_at, scope, result):
    system = INVENTORY_TABLE_SYSTEM[table]
    result.copy_keys.add(INVENTORY)
    listed = []
    for row_no, row in enumerate(rows, 1):
        record = {k: v for k, v in row.items() if k not in MODIFIED_COLUMNS}
        mapped = system_for_resource(row["resource_id"])
        if mapped != system:
            result.issues.append(
                IngestIssue(evidence.file_id, "inventory_system_mismatch", f"{table}:{row_no}", row["resource_id"])
            )
        result.observations.append(
            _observation(evidence, table, row_no, system, row["resource_id"], INVENTORY, table, record,
                         scope.get(system, snapshot_at), _modified(row))
        )
        listed.append(row["resource_id"])
    if system in scope:
        result.coverage.append(
            Coverage(
                evidence_id=evidence.file_id, system_id=system, collection=table, copy_key=INVENTORY,
                basis=C3_SCOPE_TABLE, observed_at=scope[system], captured_at=evidence.captured_at,
                locator=f"table={table};snapshot_scope={system}", listed=frozenset(listed),
            )
        )
    elif rows:
        result.issues.append(
            IngestIssue(evidence.file_id, "rows_outside_scope", table, f"{len(rows)} rows, {system} not in snapshot_scope")
        )
