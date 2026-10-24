"""PDF business documents (ADR-004: pypdf).

The supplied PDFs are generated label/value tables. The generator splits cells at ``:``,
so a time such as ``10:24`` appears in the content stream as ``... 10`` and ``24 UTC`` in
two cells (verified in the raw stream; every extractor sees the same). The parser rejoins
these before extracting facts.

Each document yields a ``DocumentExtract`` (labelled facts, table rows, full text). A
resource-level ``Observation`` is emitted only for resource ids written in the document;
documents without resource ids (e.g. registers keyed by user id and order number) stay as
extracts for Phase 2.
"""

import re
from datetime import datetime, timezone

from pypdf import PdfReader

from core.model import PRESENT, CopyKey, DocumentExtract, IngestIssue, Observation, ParseResult
from core.vocab import EMAIL_IN_TEXT_RE, PHONE_IN_TEXT_RE, RESOURCE_ID_RE, normalize_identifier, system_for_resource

DOCUMENT = CopyKey("document")
SPLIT_TIME_RE = re.compile(r"(\b\d{1,2} [A-Z][a-z]{2} \d{4}) (\d{2})\s*\n+\s*(\d{2}) UTC")
DATETIME_RE = re.compile(r"\b(\d{1,2} [A-Z][a-z]{2} \d{4})(?: (\d{2}:\d{2}) UTC)?")
RESOURCE_TOKEN_RE = re.compile(r"\b(?:audit_record|data_flow|legal_hold|tombstone|analytics|account|support|search|export|backup)_\d+\b")
USER_TOKEN_RE = re.compile(r"\buser_\d+\b")
ORDER_RE = re.compile(r"\bORD-\d+\b")
TICKET_RE = re.compile(r"\bCS-\d+\b")
LABELLED = {
    "recorded": r"^recorded (.+)$",
    "retain_until": r"^retain until (.+)$",
    "issued": r"^issued (.+)$",
    "generated": r"^generated (.+)$",
    "submitted": r"^submitted (.+)$",
    "received": r"^received (.+)$",
    "captured": r"^captured (.+)$",
    "subject_reference": r"^subject reference (\S+)$",
    "event_reference": r"^Event reference (\S+)$",
    "event": r"^event (.+)$",
    "reference": r"^(?:Support )?reference (.+)$",
    "customer": r"^Customer ([A-Z][\w'\-]+(?: [A-Z][\w'\-]+)+)$",
    "order": r"^order (ORD-\d+)$",
    "statement": r"^statement (.+)$",
    "integrity_seal": r"^integrity seal (.+)$",
    "file_name": r"^file (\S+\.\w{2,4})$",
    "uploader": r"^uploader (\w+)$",
}


def extract_text(path):
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def rejoin_split_times(text):
    return SPLIT_TIME_RE.sub(r"\1 \2:\3 UTC", text)


def parse_doc_datetime(value):
    m = DATETIME_RE.search(value or "")
    if not m:
        return None
    if m.group(2):
        parsed = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d %b %Y %H:%M")
    else:
        parsed = datetime.strptime(m.group(1), "%d %b %Y")
    return parsed.replace(tzinfo=timezone.utc)


def parse_pdf(evidence, path):
    result = ParseResult()
    try:
        raw = extract_text(path)
    except Exception as exc:  # pypdf raises a variety of errors on malformed files
        result.issues.append(IngestIssue(evidence.file_id, "pdf_error", evidence.path, str(exc)))
        return result
    text = rejoin_split_times(raw)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fields = {}
    for line in lines:
        for name, pattern in LABELLED.items():
            m = re.match(pattern, line)
            if m and name not in fields:
                fields[name] = m.group(1).strip()
    rows = []
    for line in lines:
        if line.count(" | ") >= 3:
            rows.append([cell.strip() for cell in line.split(" | ")])
    emails = sorted({e.lower() for e in EMAIL_IN_TEXT_RE.findall(text)})
    phones = sorted({p.strip() for p in PHONE_IN_TEXT_RE.findall(text)})
    users = sorted(set(USER_TOKEN_RE.findall(text)))
    resources = sorted(set(RESOURCE_TOKEN_RE.findall(text)))
    fields.update({"emails": emails, "phones": phones, "user_ids": users, "resource_ids": resources,
                   "orders": sorted(set(ORDER_RE.findall(text))), "tickets": sorted(set(TICKET_RE.findall(text)))})
    title = lines[0] if lines else ""
    result.documents.append(
        DocumentExtract(evidence.file_id, title, evidence.captured_at, evidence.captured_at, fields, rows, text)
    )

    identifiers = {}
    if emails:
        identifiers["email"] = tuple(normalize_identifier("email", e) for e in emails)
    if phones:
        identifiers["phone"] = tuple(phones)
    if users:
        identifiers["user_id"] = tuple(users)
    if fields.get("customer"):
        identifiers["name"] = (fields["customer"],)
    if len(resources) > 1:
        result.issues.append(IngestIssue(evidence.file_id, "multi_resource_document", evidence.path, ",".join(resources)))
    attributes = {"document_title": title}
    for name in ("file_name", "uploader"):
        if name in fields:
            attributes[name] = fields[name]
    for name in ("recorded", "retain_until", "issued", "generated", "submitted", "received", "captured"):
        if name in fields:
            attributes[name] = parse_doc_datetime(fields[name])
    for resource_id in resources:
        if not RESOURCE_ID_RE.match(resource_id):
            continue
        result.observations.append(
            Observation(
                evidence_id=evidence.file_id,
                source_kind="pdf",
                locator="document",
                system_id=system_for_resource(resource_id),
                resource_id=resource_id,
                copy_key=DOCUMENT,
                observed_at=evidence.captured_at,
                captured_at=evidence.captured_at,
                presence=PRESENT,
                identifiers=dict(identifiers),
                identifier_fields={"emails": emails, "phones": phones, "user_ids": users},
                attributes=dict(attributes),
            )
        )
    result.copy_keys |= {o.copy_key for o in result.observations}
    return result
