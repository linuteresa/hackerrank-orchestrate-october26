"""Participant-created evaluation cases.

Each case is a constructed request built from participant-facing data (a sample request with one
field changed, or a dataset identity placed in a sample request window). Expectations were derived
by hand from the evidence files, not from this system's output:

* CUST-1 identity invariance  - case_30 re-submitted with the subject's other (current) email.
                                Same person, same times, same text: the output must equal case_30's, and
                                the labelled case_30 answer applies (agreement reported separately).
* CUST-2 cutoff before proof  - case_03 with audit_at moved to 10:00, before evidence_94 (captured
                                10:38:56) which holds every proof for that case. Nothing captured after
                                the cutoff may be cited, and an unproven COMPLETE must not be exportable.
* CUST-3 surname decoy        - user_44 (Freja Nystrom) placed in case_21's request window. No strong
                                identifier of user_44 appears in any evidence or activity; another
                                person's records (lars-nystrom@...) share the surname, and deletion-worker
                                activity in the window belongs to other subjects. Expected: no linked
                                resources, no steps, export blocked.
* CUST-4 normalisation        - case_03's email in mixed case with surrounding spaces. Expected: the
                                same output as case_03 (labelled-answer agreement reported separately).
* CUST-5 non-deletion text    - case_01's identity and timeline with a request text the intent step
                                classified as non-deletion (picked from the cache, so no new model call).
                                Expected: NOT_APPLICABLE with [].

Run from the repository root (uses cached model outputs; no model call unless --live):

    python3 code/evaluation/custom_cases.py
"""

import argparse
import csv
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))

from agent.intent import classify  # noqa: E402
from agent.llm import Claude  # noqa: E402
from audit import Auditor  # noqa: E402
from eval.evaluate import evaluate, load_answers  # noqa: E402
from ingest.csv_inputs import load_requests  # noqa: E402
from planner import to_json  # noqa: E402

DATASET = CODE_DIR.parent / "dataset"


def _cached_non_deletion_text(requests):
    """A request text whose cached intent is non-deletion (deterministic: first by case_id; cache only)."""
    cache_only = Claude(offline=True)
    for request in sorted(requests, key=lambda r: r.case_id):
        if classify(cache_only, request.request_text).status == "NON_DELETION":
            return request.case_id, request.request_text
    return None, None


def build_cases():
    samples = {r.case_id: r for r in load_requests(DATASET, "sample_deletion_requests.csv")}
    targets = load_requests(DATASET, "deletion_requests.csv")
    gold = load_answers(DATASET / "sample_output.csv")
    cases = [
        ("CUST-1", "identity invariance", replace(samples["case_30"], case_id="CUST-1",
                                                  email="camille.bernard@outlook.com"),
         {"gold": gold["case_30"], "same_as": samples["case_30"]}),
        ("CUST-2", "cutoff before proof", replace(samples["case_03"], case_id="CUST-2",
                                                  audit_at=datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)),
         {"no_late_citation": True, "not_exportable_complete": True}),
        ("CUST-3", "surname decoy", replace(samples["case_21"], case_id="CUST-3", email="nystrom.freja@icloud.com"),
         {"no_links": True, "steps": [], "blocked": True}),
        ("CUST-4", "email normalisation", replace(samples["case_03"], case_id="CUST-4", email="  L.Fontaine46@Yahoo.COM "),
         {"gold": gold["case_03"], "same_as": samples["case_03"]}),
    ]
    source_case, text = _cached_non_deletion_text(targets)
    if text is not None:
        cases.append(("CUST-5", f"non-deletion text (from {source_case})",
                      replace(samples["case_01"], case_id="CUST-5", request_text=text),
                      {"status": "NOT_APPLICABLE", "steps": []}))
    return cases


def check(auditor, request, expect):
    result = auditor.audit(request)
    steps = json.loads(to_json(result.steps))
    captured = {fid: ev.captured_at for fid, ev in auditor.store.files.items()}
    activity_times = {a.activity_id: a.event_time for a in auditor.activity}
    failures, metrics = [], None
    if "gold" in expect:  # agreement with the labelled answer is reported, not the pass criterion
        metrics, _ = evaluate({request.case_id: (result.status, steps)}, {request.case_id: expect["gold"]})
    if "same_as" in expect:
        original = auditor.audit(expect["same_as"])
        if (original.status, to_json(original.steps)) != (result.status, to_json(result.steps)):
            failures.append(f"output differs from {expect['same_as'].case_id}")
    if expect.get("no_late_citation"):
        late = [s["evidence_id"] for s in steps if s["evidence_id"] and
                (captured.get(s["evidence_id"]) or activity_times.get(s["evidence_id"])) > request.audit_at]
        if late:
            failures.append(f"cites evidence captured after audit_at: {late}")
    if expect.get("not_exportable_complete") and result.status == "COMPLETE" and not result.export_blockers():
        failures.append("unproven COMPLETE would be exported")
    if expect.get("no_links") and result.links:
        failures.append(f"linked resources: {sorted(result.links)}")
    if "status" in expect and result.status != expect["status"]:
        failures.append(f"status {result.status} != {expect['status']}")
    if "steps" in expect and steps != expect["steps"]:
        failures.append(f"{len(steps)} steps, expected {len(expect['steps'])}")
    if expect.get("blocked") and not result.export_blockers():
        failures.append("export not blocked")
    return result, steps, failures, metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="allow model calls on cache misses")
    args = parser.parse_args(argv)
    auditor = Auditor(DATASET, offline=not args.live)
    rows = []
    for case_id, title, request, expect in build_cases():
        result, steps, failures, metrics = check(auditor, request, expect)
        rows.append({"case": case_id, "title": title, "status": result.status, "steps": len(steps),
                     "export_blockers": result.export_blockers(), "pass": not failures, "failures": failures,
                     "gold_status_match": None if metrics is None else metrics["status_accuracy"] == 1.0,
                     "gold_exact": None if metrics is None else metrics["exact_sequence_accuracy"] == 1.0,
                     "gold_step_f1": None if metrics is None else round(metrics["step_f1"], 3)})
    print(json.dumps(rows, indent=1))
    print(f"custom cases passed: {sum(r['pass'] for r in rows)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
