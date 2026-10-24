"""Shared vocabulary: system ids, collection names, identifier typing.

Every mapping here is an *interpretation* of participant-visible naming and is covered by
tests against the real evidence (e.g. every row of an inventory table must carry the
resource-id prefix of the system the table is mapped to).
"""

import re

# Resource-id prefix -> system id. Derived from the dataset's resource naming; used only
# when a source does not name the system explicitly.
RESOURCE_PREFIX_SYSTEM = (
    ("audit_record_", "security_service"),
    ("data_flow_", "integration_service"),
    ("legal_hold_", "legal_service"),
    ("tombstone_", "identity_service"),
    ("analytics_", "analytics_platform"),
    ("account_", "account_service"),
    ("support_", "support_platform"),
    ("search_", "search_service"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
)

KNOWN_SYSTEMS = frozenset(system for _, system in RESOURCE_PREFIX_SYSTEM)

RESOURCE_ID_RE = re.compile(
    r"^(?:audit_record|data_flow|legal_hold|tombstone|analytics|account|support|search|export|backup)_\d+$"
)


def system_for_resource(resource_id):
    for prefix, system in RESOURCE_PREFIX_SYSTEM:
        if resource_id.startswith(prefix):
            return system
    return None


# Typed inventory tables (SQL snapshots with a snapshot_scope table) carry no
# origin_system column; the table name identifies the system's collection.
INVENTORY_TABLE_SYSTEM = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}

# Typed copy tables (``<database>_<role>[_<region>]`` with one collection per table).
TYPED_DATABASE_SYSTEM = {
    "customer_profiles": "account_service",
    "search_documents": "search_service",
}

# Collection aliases -> canonical collection name, used only to decide whether a Coverage
# record is about the same collection as a resource. Unlisted names map to themselves, so
# unknown collections never match each other by accident.
COLLECTION_ALIASES = {
    "search_documents": "customer_search_documents",
}


def canonical_collection(name):
    if name is None:
        return None
    return COLLECTION_ALIASES.get(name, name)


# Collection -> data category, used only when the source does not state record_type.
# Ambiguous collections (e.g. inventory ``support_records``) are intentionally absent.
COLLECTION_CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_exports": "USER_EXPORT",
    "customer_export_jobs": "USER_EXPORT",
    "connector_subscriptions": "DATA_FLOW",
    "connector_configurations": "DATA_FLOW",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "backup_catalog": "BACKUP_ARCHIVE",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}

# --- identifier typing ---------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_IN_TEXT_RE = re.compile(r"\+\d{1,3}(?:[ \-]?\d{1,5}){2,6}")
USER_RE = re.compile(r"^user_\d+$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PHONE_RE = re.compile(r"^\+\d[\d \-]{5,}$")

# Field name -> identifier type assumed when the value is null (and therefore untypable).
FIELD_DEFAULT_TYPE = {
    "email": "email",
    "customer_email": "email",
    "requester_email": "email",
    "owner_email": "email",
    "uploaded_by": "email",
    "phone": "phone",
    "requester_phone": "phone",
    "owner_phone": "phone",
    "email_hash": "email_hash",
    "user_id": "user_id",
    "customer_reference": "user_id",
    "subject_reference": "user_id",
    "subject_ref": "user_id",
    "name": "name",
    "full_name": "name",
    "display_name": "name",
    "requester_name": "name",
    "matched_value": None,
    "lookup_value": None,
    "subject_selector": None,
}
IDENTIFIER_FIELDS = frozenset(FIELD_DEFAULT_TYPE)
NAME_FIELDS = frozenset(k for k, v in FIELD_DEFAULT_TYPE.items() if v == "name")

# Free-text fields scanned for embedded e-mail addresses / phone numbers.
FREE_TEXT_FIELDS = frozenset({"message_text", "message_excerpt"})

# Fields that carry a *lookup* key (how the record was found), not record content.
LOOKUP_FIELDS = frozenset(
    {"lookup_key", "match_key", "scan_key", "subject_key", "owner_key", "subject_ref", "correlation_key", "query"}
)


# Ledger / handover result vocabulary. Every non-NOT_FOUND result describes a record that
# exists in some state; the state itself is kept verbatim as ``reported_state``.
LEDGER_PRESENT_RESULTS = frozenset({"FOUND", "IDENTIFIABLE", "ACTIVE", "ANONYMIZED", "REDACTED", "BLOCKED", "DISABLED"})
LEDGER_ABSENT_RESULTS = frozenset({"NOT_FOUND"})


def ledger_presence(result):
    value = (result or "").strip().upper()
    if value in LEDGER_PRESENT_RESULTS:
        return "PRESENT"
    if value in LEDGER_ABSENT_RESULTS:
        return "ABSENT"
    return None


def null_if_text_null(value):
    if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
        return None
    return value


def classify_value(value):
    """Type an identifier value by its shape. Names cannot be typed by shape."""
    if value is None:
        return None
    text = str(value).strip()
    if EMAIL_RE.match(text):
        return "email"
    if USER_RE.match(text):
        return "user_id"
    if HASH_RE.match(text):
        return "email_hash"
    if PHONE_RE.match(text):
        return "phone"
    if RESOURCE_ID_RE.match(text):
        return "resource_id"
    return "other"


def normalize_identifier(kind, value):
    if value is None:
        return None
    text = str(value).strip()
    if kind in ("email", "email_hash"):
        return text.lower()
    return text


def collect_identifiers(record, extra_fields=()):
    """Extract identifiers from a flat mapping of observed fields.

    Returns ``(identifiers, identifier_fields)`` where ``identifiers`` maps type -> tuple of
    normalized values (empty tuple = observed null) and ``identifier_fields`` preserves the
    raw field values.
    """
    found = {}
    raw = {}
    for key, value in record.items():
        if key not in IDENTIFIER_FIELDS and key not in extra_fields:
            continue
        value = null_if_text_null(value)
        raw[key] = value
        if value is None:
            kind = FIELD_DEFAULT_TYPE.get(key)
            if kind:
                found.setdefault(kind, set())
            continue
        kind = "name" if key in NAME_FIELDS else classify_value(value)
        if kind in (None, "other", "resource_id"):
            continue
        found.setdefault(kind, set()).add(normalize_identifier(kind, value))
    for key in FREE_TEXT_FIELDS:
        text = record.get(key)
        if not isinstance(text, str):
            continue
        for email in EMAIL_IN_TEXT_RE.findall(text):
            found.setdefault("email", set()).add(email.lower())
            raw.setdefault(f"{key}#embedded_email", []).append(email)
        for phone in PHONE_IN_TEXT_RE.findall(text):
            found.setdefault("phone", set()).add(phone.strip())
            raw.setdefault(f"{key}#embedded_phone", []).append(phone.strip())
    identifiers = {kind: tuple(sorted(values)) for kind, values in found.items()}
    return identifiers, raw


def flatten_record(record, prefix=""):
    """Flatten nested dicts (e.g. a search ``document``) into one level; lists stay as-is."""
    flat = {}
    for key, value in record.items():
        if isinstance(value, dict):
            for inner_key, inner_value in flatten_record(value).items():
                flat.setdefault(inner_key, inner_value)
                flat[f"{key}.{inner_key}"] = inner_value
        else:
            flat[key] = value
    return flat
