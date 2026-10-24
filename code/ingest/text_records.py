"""Plain-text handover records.

Format: a ``Snapshot: <ts>`` header and lines of
``- <ts> | <system> | <resource_id> | <RECORD_TYPE> | <RESULT> | <subject or 'no direct identifier'>``.
"""

import re

from core.model import LOGICAL, IngestIssue, Observation, ParseResult
from core.timeutil import ISO_PATTERN, parse_ts
from core.vocab import collect_identifiers, ledger_presence

LINE_RE = re.compile(
    r"^- (?P<ts>" + ISO_PATTERN + r") \| (?P<system>[\w]+) \| (?P<resource>[\w]+) \| (?P<category>[A-Z_]+) \| "
    r"(?P<result>[A-Z_]+) \| (?P<subject>.+?)\s*$"
)
SNAPSHOT_RE = re.compile(r"^Snapshot: (" + ISO_PATTERN + r")\s*$", re.M)
NO_IDENTIFIER = "no direct identifier"


def parse_text(evidence, text):
    result = ParseResult()
    snapshot = SNAPSHOT_RE.search(text)
    if snapshot and parse_ts(snapshot.group(1)) != evidence.captured_at:
        result.issues.append(IngestIssue(evidence.file_id, "snapshot_time_mismatch", "header", snapshot.group(1)))
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.startswith("- "):
            continue
        m = LINE_RE.match(line)
        if not m:
            result.issues.append(IngestIssue(evidence.file_id, "unparsed_line", f"line={line_no}", line[:120]))
            continue
        subject = None if m["subject"].strip().lower() == NO_IDENTIFIER else m["subject"].strip()
        identifiers, identifier_fields = collect_identifiers({"subject_reference": subject})
        presence = ledger_presence(m["result"])
        if presence is None:
            result.issues.append(IngestIssue(evidence.file_id, "unclassified_result", f"line={line_no}", m["result"]))
        result.observations.append(
            Observation(
                evidence_id=evidence.file_id,
                source_kind="text",
                locator=f"line={line_no}",
                system_id=m["system"],
                resource_id=m["resource"],
                copy_key=LOGICAL,
                observed_at=parse_ts(m["ts"]),
                captured_at=evidence.captured_at,
                presence=presence,
                data_category=m["category"],
                category_source="explicit",
                identifiers=identifiers,
                identifier_fields=identifier_fields,
                attributes={"reported_state": m["result"], "subject_reference": subject,
                            "subject_raw": m["subject"].strip()},
                raw_state={"result": m["result"]},
            )
        )
    result.copy_keys |= {o.copy_key for o in result.observations}
    return result
