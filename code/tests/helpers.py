"""Shared test helpers: dataset location, a once-per-run store, small finders."""

import functools
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from core.model import EvidenceFile  # noqa: E402
from core.timeutil import parse_ts  # noqa: E402
from ingest.loader import load_store  # noqa: E402
from ingest.manifest import load_manifest  # noqa: E402

DATASET = CODE_DIR.parent / "dataset"


def ts(value):
    return parse_ts(value)


@functools.lru_cache(maxsize=1)
def manifest():
    return {ef.file_id: ef for ef in load_manifest(DATASET)}


@functools.lru_cache(maxsize=1)
def store():
    return load_store(DATASET)


def evidence(file_id):
    return manifest()[file_id]


def read_evidence(file_id):
    return (DATASET / evidence(file_id).path).read_text(encoding="utf-8")


def synthetic_file(file_id, kind, captured_at, path="synthetic", origin="synthetic"):
    return EvidenceFile(file_id, path, origin, parse_ts(captured_at), kind)


def obs_where(observations, **criteria):
    return [o for o in observations if all(getattr(o, k) == v for k, v in criteria.items())]


@functools.lru_cache(maxsize=1)
def _sample_requests():
    from ingest.csv_inputs import load_requests

    return {r.case_id: r for r in load_requests(DATASET, "sample_deletion_requests.csv")}


def samples_for(case_id):
    return _sample_requests()[case_id]
