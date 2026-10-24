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


def parse_file(evidence, dataset_dir, claude=None):
    path = Path(dataset_dir) / evidence.path
    if not path.exists():
        result = ParseResult()
        result.issues.append(IngestIssue(evidence.file_id, "missing_file", evidence.path, ""))
        return result
    if evidence.kind in TEXT_PARSERS:
        return TEXT_PARSERS[evidence.kind](evidence, path.read_text(encoding="utf-8"))
    if evidence.kind == "pdf":
        return parse_pdf(evidence, path)
    if evidence.kind == "png" and claude is not None:
        from agent.vision import extract
        from ingest.png_facts import parse_png_facts

        extraction, source = extract(claude, path)
        return parse_png_facts(evidence, extraction, source)
    result = ParseResult()
    # Without a Claude client, screenshots are not read; nothing is guessed here.
    result.issues.append(IngestIssue(evidence.file_id, "unsupported_in_phase1", evidence.path, evidence.kind))
    return result


def load_store(dataset_dir, files=None, claude=None):
    store = EvidenceStore()
    for evidence in files if files is not None else load_manifest(dataset_dir):
        store.add_file(evidence)
        store.add(evidence.file_id, parse_file(evidence, dataset_dir, claude))
    return store
