"""Data-category resolution for a resource at a cutoff.

Order of evidence (first unambiguous answer wins):

1. explicit ``record_type`` / handover category on any eligible observation of the resource;
2. the collection the resource was observed in (``COLLECTION_CATEGORY``);
3. the observing event or record shape (``EVENT_CATEGORY``, free-text/file fields);
4. the resource-id family, only where the family maps to exactly one category in the supplied
   rules (``UNAMBIGUOUS_FAMILY``). ``analytics_*`` and ``support_*`` are deliberately absent: they
   span several categories (analytics/marketing/telemetry; message/attachment).

Conflicting answers at the same level leave the category unresolved.
"""

from core.vocab import COLLECTION_CATEGORY

EVENT_CATEGORY = {
    "profile_lookup_completed": "USER_PROFILE",
    "index_query_completed": "SEARCH_INDEX_ENTRY",
    "audit_record_read_completed": "SECURITY_AUDIT_LOG",
    "connector_config_read_completed": "DATA_FLOW",
    "connector_config_loaded": "DATA_FLOW",
    "connector_heartbeat": "DATA_FLOW",
    "restore_policy_read_completed": "BACKUP_ARCHIVE",
    "restore_candidate_evaluated": "BACKUP_ARCHIVE",
    "restore_authorization_completed": "BACKUP_ARCHIVE",
    "export_metadata_read_completed": "USER_EXPORT",
    "export_download_check_completed": "USER_EXPORT",
    "analytics_profile_read_completed": "ANALYTICS_PROFILE",
    "analytics_profile_read": "ANALYTICS_PROFILE",
    "analytics_identity_scan": "ANALYTICS_PROFILE",
    "message_pii_scan": "SUPPORT_MESSAGE",
    "message_read": "SUPPORT_MESSAGE",
    "support_ticket_read_completed": "SUPPORT_MESSAGE",
    "attachment_pii_scan": "SUPPORT_ATTACHMENT",
    "retained_order_loaded": "LEGAL_HOLD_RECORD",
}

SECTION_CATEGORY = {
    "profile_lookups": "USER_PROFILE",
    "index_queries": "SEARCH_INDEX_ENTRY",
    "audit_records": "SECURITY_AUDIT_LOG",
    "connectors": "DATA_FLOW",
    "restore_entries": "BACKUP_ARCHIVE",
    "exports": "USER_EXPORT",
    "profiles": "ANALYTICS_PROFILE",
}

UNAMBIGUOUS_FAMILY = (
    ("account_", "USER_PROFILE"),
    ("search_", "SEARCH_INDEX_ENTRY"),
    ("export_", "USER_EXPORT"),
    ("data_flow_", "DATA_FLOW"),
    ("backup_", "BACKUP_ARCHIVE"),
    ("audit_record_", "SECURITY_AUDIT_LOG"),
    ("tombstone_", "ACCOUNT_TOMBSTONE"),
    ("legal_hold_", "LEGAL_HOLD_RECORD"),
)


# Screenshot record kinds (free text transcribed by the vision step) -> category. Keywords only;
# a kind matching several entries stays unresolved.
SCREEN_KIND_CATEGORY = (
    ("marketing", "MARKETING_PROFILE"),
    ("attachment", "SUPPORT_ATTACHMENT"),
    ("audit", "SECURITY_AUDIT_LOG"),
    ("tombstone", "ACCOUNT_TOMBSTONE"),
    ("connector", "DATA_FLOW"),
    ("backup", "BACKUP_ARCHIVE"),
    ("export", "USER_EXPORT"),
    ("search", "SEARCH_INDEX_ENTRY"),
    ("telemetry", "PRODUCT_TELEMETRY"),
)


def _screen_category(obs):
    kind = (obs.attributes.get("record_kind") or "").lower()
    found = {category for keyword, category in SCREEN_KIND_CATEGORY if keyword in kind}
    return found.pop() if len(found) == 1 else None


def _shape_category(obs):
    if obs.source_kind == "png":
        return _screen_category(obs)
    if obs.attributes.get("event") in EVENT_CATEGORY:
        return EVENT_CATEGORY[obs.attributes["event"]]
    if obs.attributes.get("section") in SECTION_CATEGORY:
        return SECTION_CATEGORY[obs.attributes["section"]]
    if obs.system_id == "support_platform":
        if "message_excerpt" in obs.attributes or "message_text" in obs.attributes:
            return "SUPPORT_MESSAGE"
        if "file_name" in obs.attributes or "file" in obs.attributes:
            return "SUPPORT_ATTACHMENT"
    return None


def resolve_category(resource_id, observations):
    """Return ``(category or None, reason)`` from the given (already eligible) observations."""
    levels = [
        ("explicit", {o.data_category for o in observations if o.category_source == "explicit" and o.data_category}),
        ("collection", {COLLECTION_CATEGORY[o.collection] for o in observations if o.collection in COLLECTION_CATEGORY}),
        ("shape", {c for c in (_shape_category(o) for o in observations) if c}),
    ]
    for reason, found in levels:
        if len(found) == 1:
            return next(iter(found)), reason
        if len(found) > 1:
            return None, f"conflict:{reason}:{sorted(found)}"
    for prefix, category in UNAMBIGUOUS_FAMILY:
        if resource_id.startswith(prefix):
            return category, "family"
    return None, "unresolved"
