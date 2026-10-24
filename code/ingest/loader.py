"""Load every manifest-listed evidence file into an ``EvidenceStore``."""

from pathlib import Path

from core.model import IngestIssue, ParseResult
from core.store import EvidenceStore
from ingest.json_records import parse_json
from ingest.logs import parse_log
from ingest.manifest import load_manifest
from ingest.pdf_documents import parse_pdf
from ingest.sql_snapshots import parse_sql
from ingest.text_records import parse_text

TEXT_PARSERS = {"sql": parse_sql, "json": parse_json, "log": parse_log, "text": parse_text}


def parse_file(evidence, dataset_dir):
    path = Path(dataset_dir) / evidence.path
    if not path.exists():
        result = ParseResult()
        result.issues.append(IngestIssue(evidence.file_id, "missing_file", evidence.path, ""))
        return result
    if evidence.kind in TEXT_PARSERS:
        return TEXT_PARSERS[evidence.kind](evidence, path.read_text(encoding="utf-8"))
    if evidence.kind == "pdf":
        return parse_pdf(evidence, path)
    result = ParseResult()
    # PNG screenshots are extracted by the Phase 3 vision step; nothing is guessed here.
    result.issues.append(IngestIssue(evidence.file_id, "unsupported_in_phase1", evidence.path, evidence.kind))
    return result


def load_store(dataset_dir, files=None):
    store = EvidenceStore()
    for evidence in files if files is not None else load_manifest(dataset_dir):
        store.add_file(evidence)
        store.add(evidence.file_id, parse_file(evidence, dataset_dir))
    return store
