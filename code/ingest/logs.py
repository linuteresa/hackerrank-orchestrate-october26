"""Log evidence: dialect tokenizers, strict trace joins, observation extraction.

Dialects observed in the supplied logs (one tokenizer each, all yielding ``LogEvent``):

* ``kv``       ``TS level=INFO service=x trace_id=tr-1 event=e k=v``  (also ``TS INFO service=...``)
* ``json``     ``TS {"severity": ..., "service": ..., "event": ...}``
* ``syslog``   ``TS <14>1 host app procid - - level=INFO k="v"``      (RFC 5424, procid = trace)
* ``bracket``  ``TS [LEVEL] [service] [trace] event: k="v"``
* ``pipe``     ``TS | LEVEL | service | trace | event | k="v"``
* ``cloud``    ``TS requestId=trace level=L function=service message="k=\\"v\\" ..."``

Trace joins are strict (ADR-005): a ``*_started`` line's lookup key is attached to an outcome
line only inside one ``(evidence_id, trace_id)`` group, when the group has exactly one lookup
identity and one resource id, the start precedes the outcome, the outcome event is an allowed
terminal for that start event, and no other start of the same group lies between them.
Everything else is recorded as ambiguous/orphan and produces no identity link.
"""

import json
import re
from dataclasses import dataclass, field

from core.model import (
    ABSENT,
    LINK_SAME_LINE,
    LINK_TRACE_JOIN,
    LOGICAL,
    PRESENT,
    CopyKey,
    IngestIssue,
    LookupKey,
    Observation,
    ParseResult,
    TraceJoin,
)
from core.timeutil import ISO_PATTERN, parse_ts
from core.vocab import (
    LOOKUP_FIELDS,
    classify_value,
    collect_identifiers,
    ledger_presence,
    normalize_identifier,
    null_if_text_null,
    system_for_resource,
)

TS = "(?P<ts>" + ISO_PATTERN + ")"
JSON_RE = re.compile("^" + TS + r" (?P<body>\{.*\})\s*$")
SYSLOG_RE = re.compile("^" + TS + r" <\d+>\d+ (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) \S+ \S+ (?P<rest>.*)$")
BRACKET_RE = re.compile("^" + TS + r" \[(?P<level>\w+)\] \[(?P<service>[^\]]+)\] \[(?P<slot>[^\]]+)\] (?P<event>\w+): ?(?P<rest>.*)$")
PIPE_RE = re.compile("^" + TS + r" \| (?P<level>\w+) \| (?P<service>\S+) \| (?P<slot>\S+) \| (?P<event>\w+) \| ?(?P<rest>.*)$")
CLOUD_RE = re.compile("^" + TS + r' requestId=(?P<slot>\S+) level=(?P<level>\w+) function=(?P<service>\S+) message="(?P<msg>.*)"\s*$')
KV_RE = re.compile("^" + TS + r" (?P<rest>.*)$")
PAIR_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
TRACE_RE = re.compile(r"^(?:tr|rb)-[\w-]+$")
LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR"}

# Start event -> allowed terminal events (outcome lines carrying the resource state).
# Derived from the supplied logs; same-stem ``*_completed`` is always allowed.
TERMINALS = {
    "profile_lookup_started": {"profile_lookup_completed"},
    "index_query_started": {"index_query_completed"},
    "audit_record_read_started": {"audit_record_read_completed"},
    "audit_record_query_started": {"audit_record_read_completed"},
    "connector_config_read_started": {"connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"},
    "analytics_profile_read_started": {"analytics_profile_read_completed"},
    "analytics_profile_scan_started": {"analytics_profile_read", "analytics_identity_scan"},
    "support_ticket_read_started": {"support_ticket_read_completed"},
    "message_scan_started": {"message_pii_scan", "message_read"},
    "attachment_scan_started": {"attachment_pii_scan"},
    "restore_policy_read_started": {"restore_policy_read_completed"},
    "restore_policy_check_started": {"restore_candidate_evaluated", "restore_authorization_completed"},
    "export_download_check_started": {"export_download_check_completed"},
    "export_metadata_read_started": {"export_metadata_read_completed"},
    "contact_query_started": {"contact_query_completed", "deletion_marker_check"},
    "retained_order_query_started": {"retained_order_loaded"},
}

# Outcome events whose presence is stated by the line itself (a record was returned/found).
RECORD_RETURNED_EVENTS = {
    "audit_record_read_completed", "connector_config_read_completed", "connector_config_loaded",
    "connector_heartbeat", "analytics_profile_read_completed", "analytics_profile_read",
    "support_ticket_read_completed", "message_read", "export_metadata_read_completed",
    "restore_candidate_evaluated", "retained_order_loaded", "message_pii_scan",
    "attachment_pii_scan", "analytics_identity_scan",
}
# Outcome events that report attributes of a resource but not whether the record exists.
ATTRIBUTE_ONLY_EVENTS = {
    "deletion_marker_check", "restore_authorization_completed", "export_download_check_completed", "thumbnail_failed",
}

BOOLEAN_FIELDS = {
    "enabled", "restore_allowed", "archive_present", "available", "marker_found", "eligible",
    "deletion_block", "scheduler_registered", "email_opt_in",
}
CONTEXT_FIELDS = {"instance", "pod", "region", "attempt", "duration_ms", "took_ms", "latency_ms", "shard", "level"}


@dataclass
class LogEvent:
    evidence_id: str
    line_no: int
    ts: object
    dialect: str
    service: str
    level: str
    trace_id: object
    event: str
    fields: dict = field(default_factory=dict)
    raw: str = ""


def _pairs(text):
    out = {}
    for key, value in PAIR_RE.findall(text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        out[key] = value
    return out


def _trace(value):
    return value if value and TRACE_RE.match(value) else None


def tokenize_line(evidence_id, line_no, line):
    """Parse one log line into a ``LogEvent`` (or ``None`` for unparseable lines)."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    m = JSON_RE.match(line)
    if m:
        try:
            body = json.loads(m["body"])
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict):
            fields = {k: str(v) if not isinstance(v, str) else v for k, v in body.items()}
            service = fields.pop("service", None) or fields.get("logger", "")
            fields.pop("logger", None)
            level = fields.pop("severity", "")
            return LogEvent(evidence_id, line_no, parse_ts(m["ts"]), "json", service, level,
                            _trace(fields.pop("trace_id", None)), fields.pop("event", ""), fields, line)
    m = SYSLOG_RE.match(line)
    if m:
        fields = _pairs(m["rest"])
        level = fields.pop("level", "")
        fields["host"] = m["host"]
        return LogEvent(evidence_id, line_no, parse_ts(m["ts"]), "syslog", m["app"], level,
                        _trace(m["procid"]), fields.pop("event", ""), fields, line)
    for dialect, regex in (("bracket", BRACKET_RE), ("pipe", PIPE_RE)):
        m = regex.match(line)
        if m:
            fields = _pairs(m["rest"])
            return LogEvent(evidence_id, line_no, parse_ts(m["ts"]), dialect, m["service"], m["level"],
                            _trace(m["slot"]), m["event"], fields, line)
    m = CLOUD_RE.match(line)
    if m:
        fields = _pairs(m["msg"].replace('\\"', '"'))
        return LogEvent(evidence_id, line_no, parse_ts(m["ts"]), "cloud", m["service"], m["level"],
                        _trace(m["slot"]), fields.pop("event", ""), fields, line)
    m = KV_RE.match(line)
    if m:
        rest = m["rest"]
        level = ""
        head = rest.split(" ", 1)[0]
        if head in LEVELS:
            level, rest = head, rest[len(head):]
        fields = _pairs(rest)
        level = fields.pop("level", level)
        return LogEvent(evidence_id, line_no, parse_ts(m["ts"]), "kv", fields.pop("service", ""), level,
                        _trace(fields.pop("trace_id", None)), fields.pop("event", ""), fields, line)
    return None


def _stem(event):
    for suffix in ("_started", "_completed"):
        if event.endswith(suffix):
            return event[: -len(suffix)]
    return event


def allowed_terminal(start_event, event):
    if event in TERMINALS.get(start_event, ()):
        return True
    return event.endswith("_completed") and _stem(event) == _stem(start_event)


def _lookup_values(event):
    """Lookup identity on a start line: (field, normalized value, kind)."""
    out = []
    for key in sorted(LOOKUP_FIELDS):
        value = null_if_text_null(event.fields.get(key))
        if value is None:
            continue
        kind = classify_value(value)
        out.append((key, normalize_identifier(kind, value), kind))
    return out


def join_traces(events):
    """Strict joins within one evidence file. Returns (joins, ambiguous_groups, stats)."""
    groups = {}
    for ev in events:
        if ev.trace_id:
            groups.setdefault(ev.trace_id, []).append(ev)
    joins = {}
    ambiguous = {}
    stats = {"trace_groups": len(groups), "joined": 0, "ambiguous": 0, "started_only": 0,
             "completed_only": 0, "no_outcome_or_start": 0, "orphan_outcomes": 0, "null_lookup": 0}
    for trace_id, group in groups.items():
        group.sort(key=lambda e: e.line_no)
        starts = [e for e in group if e.event.endswith("_started")]
        outcomes = [e for e in group if e.fields.get("resource_id") and not e.event.endswith("_started")]
        if not starts:
            stats["completed_only" if outcomes else "no_outcome_or_start"] += 1
            continue
        if not outcomes:
            stats["started_only"] += 1
            continue
        lookups = {(v, k) for s in starts for (_, v, k) in _lookup_values(s)}
        resources = {e.fields["resource_id"] for e in group if e.fields.get("resource_id")}
        if not lookups:
            stats["null_lookup"] += 1  # e.g. ``lookup_key=null``: nothing to join, not ambiguous
            continue
        if len(lookups) != 1 or len(resources) != 1:
            ambiguous[trace_id] = f"lookups={sorted(lookups)} resources={sorted(resources)}"
            stats["ambiguous"] += 1
            continue
        # Two starts with no outcome between them: which start an outcome answers is unknowable.
        ordered = sorted(starts + outcomes, key=lambda e: e.line_no)
        kinds = ["S" if e.event.endswith("_started") else "O" for e in ordered]
        if any(a == b == "S" for a, b in zip(kinds, kinds[1:])):
            ambiguous[trace_id] = "repeated starts without an intervening outcome"
            stats["ambiguous"] += 1
            continue
        lookup_value, lookup_kind = next(iter(lookups))
        joined_any = False
        for out in outcomes:
            prior = [s for s in starts if s.line_no < out.line_no]
            if not prior or not allowed_terminal(prior[-1].event, out.event):
                stats["orphan_outcomes"] += 1  # outcome before any start, or wrong event pair
                continue
            start = prior[-1]
            field_name = next(k for (k, v, _) in _lookup_values(start) if v == lookup_value)
            joins[(trace_id, out.line_no)] = (
                TraceJoin(out.evidence_id, trace_id, start.line_no, out.line_no, start.event, out.event,
                          lookup_value, out.fields["resource_id"]),
                LookupKey(lookup_value, lookup_kind, field_name, LINK_TRACE_JOIN, start.line_no),
            )
            joined_any = True
        if joined_any:
            stats["joined"] += 1
    return joins, ambiguous, stats


def _presence(event):
    fields = event.fields
    rows = fields.get("rows")
    if rows is not None and str(rows).isdigit():
        return PRESENT if int(rows) > 0 else ABSENT
    status = fields.get("http_status")
    if status == "404":
        return ABSENT
    if status == "200":
        return PRESENT
    result = (fields.get("result") or "").upper()
    if result == "HIT":
        return PRESENT
    if result == "MISS":
        return ABSENT
    if ledger_presence(result):
        return ledger_presence(result)
    archive = fields.get("archive_present")
    if archive in ("true", "false"):
        return PRESENT if archive == "true" else ABSENT
    if event.event in RECORD_RETURNED_EVENTS:
        return PRESENT
    return None


def _copy_key(event):
    # A service that names itself a replica is an explicit (if coarse) copy identity.
    if event.service.endswith("_replica"):
        return CopyKey(role="replica", database=event.service, instance=event.fields.get("instance"))
    return LOGICAL


def parse_log(evidence, text):
    result = ParseResult()
    events = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        ev = tokenize_line(evidence.file_id, line_no, line)
        if ev is None:
            result.issues.append(IngestIssue(evidence.file_id, "unparsed_line", f"line={line_no}", line[:120]))
            continue
        events.append(ev)
    result.stats["lines"] = len(events)
    result.stats.update({f"dialect:{ev.dialect}": 0 for ev in events})
    for ev in events:
        result.stats[f"dialect:{ev.dialect}"] += 1

    joins, ambiguous, join_stats = join_traces(events)
    result.stats.update(join_stats)
    for trace_id, why in sorted(ambiguous.items()):
        result.issues.append(IngestIssue(evidence.file_id, "ambiguous_trace", trace_id, why))
    for join, _ in joins.values():
        result.trace_joins.append(join)

    read_paths = {}
    for ev in events:
        if ev.trace_id and ("pool" in ev.fields or "route" in ev.fields):
            item = ("pool", ev.fields["pool"]) if "pool" in ev.fields else ("route", ev.fields["route"])
            read_paths.setdefault(ev.trace_id, []).append((ev.line_no,) + item)

    for ev in events:
        resource_id = ev.fields.get("resource_id")
        if not resource_id or ev.event.endswith("_started"):
            continue
        system = ev.fields.get("system") or system_for_resource(resource_id)
        if system is None:
            result.issues.append(IngestIssue(evidence.file_id, "unmapped_resource", f"line={ev.line_no}", resource_id))
            continue
        presence = _presence(ev)
        if presence is None and ev.event not in ATTRIBUTE_ONLY_EVENTS:
            result.issues.append(IngestIssue(evidence.file_id, "unclassified_event", f"line={ev.line_no}", ev.event))
        content = {k: v for k, v in ev.fields.items() if k not in LOOKUP_FIELDS}
        identifiers, identifier_fields = collect_identifiers(content)
        lookup_keys = []
        for key in sorted(LOOKUP_FIELDS & set(ev.fields)):
            value = null_if_text_null(ev.fields[key])
            if value is not None:
                kind = classify_value(value)
                lookup_keys.append(LookupKey(normalize_identifier(kind, value), kind, key, LINK_SAME_LINE, ev.line_no))
        joined = joins.get((ev.trace_id, ev.line_no))
        if joined:
            lookup_keys.append(joined[1])
        attributes = {}
        for key, value in ev.fields.items():
            if key in identifier_fields or key in LOOKUP_FIELDS or key == "resource_id" or key in CONTEXT_FIELDS:
                continue
            value = null_if_text_null(value)
            if key in BOOLEAN_FIELDS and value in ("true", "false"):
                value = value == "true"
            attributes[key] = value
        if "result" in ev.fields and ledger_presence(ev.fields["result"]):
            attributes["reported_state"] = ev.fields["result"].upper()
        attributes["event"] = ev.event
        attributes["emitter"] = ev.service
        data_category = ev.fields.get("record_type")
        result.observations.append(
            Observation(
                evidence_id=evidence.file_id,
                source_kind="log",
                locator=f"line={ev.line_no}",
                system_id=system,
                resource_id=resource_id,
                copy_key=_copy_key(ev),
                observed_at=ev.ts,
                captured_at=evidence.captured_at,
                presence=presence,
                data_category=data_category,
                category_source="explicit" if data_category else None,
                identifiers=identifiers,
                identifier_fields=identifier_fields,
                attributes=attributes,
                trace_id=ev.trace_id,
                lookup_keys=tuple(lookup_keys),
                read_path=tuple(p for p in read_paths.get(ev.trace_id, []) if p[0] < ev.line_no),
                raw_state={k: ev.fields[k] for k in ("rows", "http_status", "result") if k in ev.fields}
                | {"dialect": ev.dialect, "instance": ev.fields.get("instance"), "region": ev.fields.get("region")},
            )
        )
    result.copy_keys |= {o.copy_key for o in result.observations}
    return result
