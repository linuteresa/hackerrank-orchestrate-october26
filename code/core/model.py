"""Typed records produced by ingestion.

Ingestion records *facts*, never treatments: whether a present record with null
identifiers counts as redacted or anonymized is decided later by the policy engine.

Two fact kinds feed state selection:

* ``Observation`` - a source's positive statement about one resource in one copy
  (``presence`` is ``PRESENT``/``ABSENT`` only when the source states it explicitly).
* ``Coverage``    - a source's explicit declaration that it enumerated a collection in a
  copy. Omission from a covered enumeration is reported as ``NOT_LISTED``, never ``ABSENT``.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

PRESENT = "PRESENT"
ABSENT = "ABSENT"
NOT_LISTED = "NOT_LISTED"
UNKNOWN = "UNKNOWN"
CONFLICT = "CONFLICT"

# Coverage bases (PLAN.md section 4.3).
C1_DECLARED_COLLECTION = "C1_DECLARED_COLLECTION"
C2_TYPED_COPY_TABLE = "C2_TYPED_COPY_TABLE"
C3_SCOPE_TABLE = "C3_SCOPE_TABLE"

# Link strengths for lookup keys attached to an observation.
LINK_TRACE_JOIN = "trace_join"  # lookup key from a *_started line joined by trace_id
LINK_SAME_LINE = "same_line"  # lookup key on the outcome line itself (correlation_key, query)


@dataclass(frozen=True)
class CopyKey:
    """Identity of a physical (or unresolved logical) copy, built only from explicit evidence.

    Unknown parts stay ``None``. Roles ``logical``, ``inventory``, ``records_bundle`` and
    ``collection_capture`` are deliberately unresolved: they are not mapped to a physical
    copy (hypotheses H-2b / H-2d remain open).
    """

    role: str
    database: Optional[str] = None
    region: Optional[str] = None
    instance: Optional[str] = None

    def label(self):
        parts = [p for p in (self.database, self.role, self.region, self.instance) if p]
        return "/".join(parts)


LOGICAL = CopyKey("logical")
INVENTORY = CopyKey("inventory")
RECORDS_BUNDLE = CopyKey("records_bundle")
COLLECTION_CAPTURE = CopyKey("collection_capture")


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    path: str  # relative to dataset/
    origin: str
    captured_at: datetime
    kind: str  # sql | json | log | text | pdf | png


@dataclass(frozen=True)
class LookupKey:
    value: str
    kind: str  # email | user_id | email_hash | phone | resource_id | other
    field: str
    link: str  # LINK_TRACE_JOIN | LINK_SAME_LINE
    line_no: Optional[int] = None


@dataclass
class Observation:
    evidence_id: str
    source_kind: str
    locator: str
    system_id: str
    resource_id: str
    copy_key: CopyKey
    observed_at: datetime
    captured_at: datetime
    presence: Optional[str] = None
    collection: Optional[str] = None
    data_category: Optional[str] = None
    category_source: Optional[str] = None  # explicit | collection_map
    # type -> tuple of observed values; an empty tuple means "field observed as null".
    identifiers: dict = field(default_factory=dict)
    # raw identifier-bearing fields exactly as observed (field -> value)
    identifier_fields: dict = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)
    last_modified_at: Optional[datetime] = None
    snapshot_scoped: bool = False  # True for snapshot-type sources (SQL, bundles, captures)
    trace_id: Optional[str] = None
    lookup_keys: tuple = ()
    read_path: tuple = ()
    raw_state: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Coverage:
    evidence_id: str
    system_id: str
    collection: str
    copy_key: CopyKey
    basis: str
    observed_at: datetime
    captured_at: datetime
    locator: str
    listed: frozenset
    filter_scope: str = "unknown"  # no evidence declares how rows were selected

    @property
    def declared_empty(self):
        return not self.listed


@dataclass(frozen=True)
class TraceJoin:
    evidence_id: str
    trace_id: str
    start_line: int
    end_line: int
    start_event: str
    end_event: str
    lookup_key: str
    resource_id: str


@dataclass
class DocumentExtract:
    """Structured facts from a document that carry no resource-level state by themselves."""

    evidence_id: str
    doc_type: str
    observed_at: datetime
    captured_at: datetime
    fields: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    text: str = ""


@dataclass(frozen=True)
class IngestIssue:
    evidence_id: str
    kind: str
    locator: str
    detail: str


@dataclass
class ParseResult:
    observations: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    trace_joins: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    copy_keys: set = field(default_factory=set)
    stats: dict = field(default_factory=dict)

    def extend(self, other):
        self.observations.extend(other.observations)
        self.coverage.extend(other.coverage)
        self.trace_joins.extend(other.trace_joins)
        self.documents.extend(other.documents)
        self.issues.extend(other.issues)
        self.copy_keys |= other.copy_keys
        for key, value in other.stats.items():
            self.stats[key] = self.stats.get(key, 0) + value


@dataclass(frozen=True)
class CopyState:
    """State of one copy at a cutoff, with the facts that decided it."""

    copy_key: CopyKey
    evidence_scope: Optional[str]  # evidence_id when copies are evidence-scoped (strict mode)
    state: str
    observed_at: Optional[datetime]
    deciding: tuple  # ((fact_kind, evidence_id, locator, state, basis), ...)

