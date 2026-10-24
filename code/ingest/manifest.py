"""Evidence manifest (``dataset/evidence_files.csv``)."""

import csv
from pathlib import Path

from core.model import EvidenceFile
from core.timeutil import parse_ts

KIND_BY_SUFFIX = {
    ".sql": "sql",
    ".json": "json",
    ".log": "log",
    ".txt": "text",
    ".pdf": "pdf",
    ".png": "png",
}


def load_manifest(dataset_dir):
    """Return manifest rows as ``EvidenceFile`` objects, in file order."""
    path = Path(dataset_dir) / "evidence_files.csv"
    files = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            suffix = Path(row["file_path"]).suffix.lower()
            files.append(
                EvidenceFile(
                    file_id=row["file_id"],
                    path=row["file_path"],
                    origin=row["origin"],
                    captured_at=parse_ts(row["captured_at"]),
                    kind=KIND_BY_SUFFIX.get(suffix, "unknown"),
                )
            )
    return files
