"""Evidence store: observations, coverage and per-copy state selection at a cutoff.

The store reports facts; it never decides compliance. In particular:

* every query is filtered by ``eligible()`` - inclusive ``captured_at <= audit_at`` and
  ``observed_at <= audit_at``;
* state is selected per copy and returned as a vector - the store never collapses copies;
* omission from a covered enumeration is ``NOT_LISTED`` (with its coverage basis), never
  ``ABSENT``; whether that is admissible proof of absence is H-COV (Phase 2);
* logical observations are their own copy and never update a physical copy (H-2b open);
* by default (``identity="strict"``) snapshot-derived copies are scoped to their evidence
  file, so a table name seen in two files is *not* assumed to be one copy. ``identity="h2d"``
  groups by ``CopyKey`` alone to evaluate hypothesis H-2d.
"""

from collections import defaultdict

from core.model import CONFLICT, NOT_LISTED, UNKNOWN, CopyState, IngestIssue
from core.vocab import canonical_collection

STRICT = "strict"
H2D = "h2d"


class EvidenceStore:
    def __init__(self):
        self.files = {}
        self.observations = []
        self.coverage = []
        self.trace_joins = []
        self.documents = []
        self.issues = []
        self.stats = defaultdict(int)
        self.file_copies = defaultdict(set)  # evidence_id -> copy keys present in that file
        self._by_resource = defaultdict(list)

    # ---- loading ------------------------------------------------------------------------

    def add_file(self, evidence):
        self.files[evidence.file_id] = evidence

    def add(self, evidence_id, result):
        self.observations.extend(result.observations)
        self.coverage.extend(result.coverage)
        self.trace_joins.extend(result.trace_joins)
        self.documents.extend(result.documents)
        self.issues.extend(result.issues)
        self.file_copies[evidence_id] |= result.copy_keys
        for key, value in result.stats.items():
            self.stats[key] += value
        for obs in result.observations:
            self._by_resource[(obs.system_id, obs.resource_id)].append(obs)
            if obs.observed_at > obs.captured_at:
                # Kept, not dropped: the fact-level cutoff in eligible() already prevents it from
                # being used before its own time. Flagged because a file cannot know its future.
                self.issues.append(IngestIssue(obs.evidence_id, "observed_after_capture", obs.locator,
                                               f"{obs.resource_id} observed {obs.observed_at} > captured {obs.captured_at}"))

    # ---- time ---------------------------------------------------------------------------

    @staticmethod
    def eligible(fact, audit_at):
        """Inclusive cutoff on both capture time and the fact's own observation time."""
        return fact.captured_at <= audit_at and fact.observed_at <= audit_at

    # ---- queries ------------------------------------------------------------------------

    def observations_for(self, system_id, resource_id, audit_at=None):
        found = self._by_resource.get((system_id, resource_id), [])
        if audit_at is None:
            return list(found)
        return [o for o in found if self.eligible(o, audit_at)]

    def _resource_collections(self, system_id, resource_id, audit_at):
        collections = {
            canonical_collection(o.collection)
            for o in self.observations_for(system_id, resource_id, audit_at)
            if o.collection
        }
        if collections:
            return collections, "observed"
        # Never observed with a collection: usable only if the system has a single covered collection.
        covered = {
            canonical_collection(c.collection)
            for c in self.coverage
            if c.system_id == system_id and self.eligible(c, audit_at)
        }
        if len(covered) == 1:
            return covered, "single_system_collection"
        return set(), "unresolved"

    def relevant_coverage(self, system_id, resource_id, audit_at):
        collections, _ = self._resource_collections(system_id, resource_id, audit_at)
        return [
            c for c in self.coverage
            if c.system_id == system_id
            and canonical_collection(c.collection) in collections
            and self.eligible(c, audit_at)
        ]

    def _group(self, copy_key, evidence_id, snapshot_scoped, identity):
        if identity == STRICT and snapshot_scoped:
            return (copy_key, evidence_id)
        return (copy_key, None)

    def copy_states(self, system_id, resource_id, audit_at, identity=STRICT, include_sibling_copies=True):
        """Per-copy state vector for one resource at ``audit_at``.

        Facts per copy: explicit observations with a stated presence, plus ``NOT_LISTED`` for
        each eligible covering enumeration of that copy that does not list the resource. The
        fact with the latest ``observed_at`` decides; equal times with different states give
        ``CONFLICT``. With ``include_sibling_copies``, copies that appear in the same evidence
        files as the resource but hold no fact about it are reported as ``UNKNOWN``.
        """
        facts = defaultdict(list)
        for obs in self.observations_for(system_id, resource_id, audit_at):
            if obs.presence is None:
                continue
            group = self._group(obs.copy_key, obs.evidence_id, obs.snapshot_scoped, identity)
            facts[group].append((obs.observed_at, obs.presence, "observation", obs.evidence_id, obs.locator, None))
        for cov in self.relevant_coverage(system_id, resource_id, audit_at):
            if resource_id in cov.listed:
                continue  # listed rows are already explicit observations
            group = self._group(cov.copy_key, cov.evidence_id, True, identity)
            facts[group].append((cov.observed_at, NOT_LISTED, "coverage", cov.evidence_id, cov.locator, cov.basis))

        states = []
        for (copy_key, scope), items in facts.items():
            latest = max(item[0] for item in items)
            winners = [item for item in items if item[0] == latest]
            values = {item[1] for item in winners}
            state = values.pop() if len(values) == 1 else CONFLICT
            deciding = tuple(sorted((kind, ev, loc, value, basis) for (_, value, kind, ev, loc, basis) in winners))
            states.append(CopyState(copy_key, scope, state, latest, deciding))

        if include_sibling_copies:
            seen = {(s.copy_key, s.evidence_scope) for s in states}
            for obs in self.observations_for(system_id, resource_id, audit_at):
                if not obs.snapshot_scoped:
                    continue
                for sibling in self.file_copies.get(obs.evidence_id, ()):
                    group = self._group(sibling, obs.evidence_id, True, identity)
                    if group not in seen:
                        seen.add(group)
                        states.append(CopyState(sibling, group[1], UNKNOWN, None, ()))
        states.sort(key=lambda s: (s.copy_key.label(), s.evidence_scope or ""))
        return states

    def copy_state(self, system_id, resource_id, copy_key, audit_at, evidence_scope=None, identity=STRICT):
        """State of one named copy; ``UNKNOWN`` when no eligible fact exists for it."""
        for state in self.copy_states(system_id, resource_id, audit_at, identity, include_sibling_copies=False):
            if state.copy_key == copy_key and (identity != STRICT or state.evidence_scope == evidence_scope):
                return state
        return CopyState(copy_key, evidence_scope, UNKNOWN, None, ())

    # ---- invariants ---------------------------------------------------------------------

    def time_violations(self):
        """Facts whose own observation time is later than their file's capture time."""
        return [f for f in self.observations + self.coverage if f.observed_at > f.captured_at]
