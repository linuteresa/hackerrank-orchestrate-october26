"""Ghost Data Still Exists - deletion-audit agent.

Usage (from the repository root):

    python3 code/main.py                 # audit dataset/deletion_requests.csv
    python3 code/main.py --sample        # audit and evaluate the 32 labelled samples
    python3 code/main.py --offline       # never call Claude; use cached model outputs only
    python3 code/main.py --best-effort   # export all rows; unverified rows are classified, not hidden

Every run writes provisional results to ``code/eval/provisional_<name>.csv`` plus an
``unresolved_<name>.json`` report and ``usage_<name>.json``.

Strict mode (default): the final ``output.csv`` at the repository root is written only when every row
passes the export gate (no unresolved intent or subject, and no COMPLETE row without cited proof).

Best-effort mode (``--best-effort``): every row is exported, but only if every row passes the same
mandatory contract validation (schema, ordering, citation and cutoff checks). The only change made to
a gated row is removing verification steps that cite no evidence from a COMPLETE row; nothing is
added. Rows with an unresolved intent or subject cannot be predicted and fail the export. Each row is
classified in ``code/eval/export_classes_<name>.json`` as ``verified``, ``verified_with_unresolved``
(passes the gate; some linked resources are unresolved and omitted) or ``best_effort`` (exported
only in this mode), with the assumptions it rests on.

Claude credentials are read from the environment (``ANTHROPIC_API_KEY``) by the Anthropic SDK;
nothing is stored.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from audit import Auditor  # noqa: E402
from eval.evaluate import evaluate, load_answers  # noqa: E402
from ingest.csv_inputs import load_requests  # noqa: E402
from planner import to_json  # noqa: E402
from validate import validate_row  # noqa: E402

REPO = CODE_DIR.parent
DATASET = REPO / "dataset"
SCRATCH = CODE_DIR / "eval"


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        writer.writerows(rows)


ASSUMPTIONS = {
    "complete_without_account_verification": "no account record of the subject appears in evidence captured "
                                              "by audit_at; account deletion is assumed, not verified",
    "complete_without_steps": "no evidenced resource is left to verify; COMPLETE carries no verification step",
    "complete_with_unresolved": "a linked resource's category or state is unresolved; it is omitted from the plan",
    "complete_with_uncited_step": "verification steps without citable evidence were removed",
}


def citation_checker(auditor, cutoff):
    captured = {fid: ev.captured_at for fid, ev in auditor.store.files.items()}
    activity_times = {a.activity_id: a.event_time for a in auditor.activity}

    def cite_ok(evidence_id):
        when = captured.get(evidence_id) or activity_times.get(evidence_id)
        return when is not None and when <= cutoff

    return cite_ok


def run(requests_name, auditor):
    requests = sorted(load_requests(DATASET, requests_name), key=lambda r: r.case_id)
    rows, results, violations, blockers = [], {}, {}, {}
    for request in requests:
        result = auditor.audit(request)
        result.audit_at = request.audit_at
        steps_json = to_json(result.steps)
        errors = validate_row(request.case_id, result.status, steps_json, citation_checker(auditor, request.audit_at))
        if errors:
            violations[request.case_id] = errors
        if result.export_blockers() or errors:
            blockers[request.case_id] = result.export_blockers() + [f"contract:{e}" for e in errors]
        rows.append((request.case_id, result.status, steps_json))
        results[request.case_id] = result
    return rows, results, violations, blockers


def unresolved_report(results, blockers):
    report = {}
    for case_id, result in sorted(results.items()):
        entry = {
            "status": result.status,
            "intent": getattr(result.intent, "status", None),
            "unresolved": [f"{k}:{d}" for k, d in result.unresolved],
            "candidates": sorted(f"{s}/{r}" for s, r in result.candidates),
            "notes": result.notes,
            "export_blockers": blockers.get(case_id, []),
        }
        if entry["unresolved"] or entry["export_blockers"] or entry["notes"]:
            report[case_id] = entry
    return report


def best_effort(rows, results, blockers, auditor):
    """``(rows, classes, failures)``: every row, its export class, and rows that still cannot be exported."""
    out, classes, failures = [], {}, {}
    for case_id, status, steps_json in rows:
        result = results[case_id]
        omitted = [d for k, d in result.unresolved if k == "decision"]
        reasons = blockers.get(case_id, [])
        if not reasons:
            out.append((case_id, status, steps_json))
            classes[case_id] = {"class": "verified_with_unresolved" if omitted else "verified", "status": status,
                                "assumptions": [f"unresolved and omitted: {d}" for d in omitted]}
            continue
        hard = sorted({r for r in reasons if r.startswith("unresolved:")})
        if hard:
            failures[case_id] = hard
            continue
        steps = result.steps
        removed = []
        if status == "COMPLETE":
            removed = [f"{s.operation} {s.resource_id}" for s in steps if not s.evidence_id]
            steps = [s for s in steps if s.evidence_id]
        steps_json = to_json(steps)
        errors = validate_row(case_id, status, steps_json, citation_checker(auditor, result.audit_at))
        if errors:
            failures[case_id] = errors
            continue
        kinds = sorted({r.split(":")[0] for r in reasons if not r.startswith("contract:")})
        if removed and "complete_with_uncited_step" not in kinds:
            kinds.append("complete_with_uncited_step")
        assumptions = [ASSUMPTIONS[k] for k in kinds if k in ASSUMPTIONS]
        assumptions += [f"unresolved and omitted: {d}" for d in omitted]
        assumptions += [f"removed uncited step: {r}" for r in removed]
        out.append((case_id, status, steps_json))
        classes[case_id] = {"class": "best_effort", "status": status, "blockers": sorted(set(reasons)),
                            "assumptions": assumptions}
    return out, classes, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", action="store_true", help="audit and evaluate the 32 labelled samples")
    parser.add_argument("--offline", action="store_true", help="use cached model outputs only")
    parser.add_argument("--output", default=None, help="final output path (default: <repo>/output.csv)")
    parser.add_argument("--best-effort", action="store_true",
                        help="export every row that passes contract validation and classify unverified rows")
    args = parser.parse_args(argv)

    auditor = Auditor(DATASET, offline=args.offline)
    name = "sample" if args.sample else "target"
    requests = "sample_deletion_requests.csv" if args.sample else "deletion_requests.csv"
    rows, results, violations, blockers = run(requests, auditor)

    provisional = SCRATCH / f"provisional_{name}.csv"
    write_csv(provisional, rows)
    (SCRATCH / f"unresolved_{name}.json").write_text(
        json.dumps(unresolved_report(results, blockers), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    usage = {"rows": auditor.claude.ledger.summary(), "totals": auditor.claude.ledger.totals()}
    (SCRATCH / f"usage_{name}.json").write_text(json.dumps(usage, indent=1) + "\n", encoding="utf-8")

    print(f"{len(rows)} rows -> {provisional} (provisional)")
    print(f"contract violations: {len(violations)} rows; export blockers: {len(blockers)} rows")
    for case_id, errors in sorted(violations.items())[:10]:
        print(f"  violation {case_id}: {errors[:3]}")
    kinds = {}
    for reasons in blockers.values():
        for reason in set(r.split(":")[0] + (":" + r.split(":")[1] if r.startswith("unresolved") else "") for r in reasons):
            kinds[reason] = kinds.get(reason, 0) + 1
    print("blocker kinds:", json.dumps(kinds, sort_keys=True))
    statuses = {}
    for _, status, _ in rows:
        statuses[status] = statuses.get(status, 0) + 1
    print("statuses:", json.dumps(statuses, sort_keys=True))
    non_deletion = {cid: r.intent.request_type for cid, r in results.items()
                    if getattr(r.intent, "status", None) == "NON_DELETION"}
    print(f"non-deletion intents: {len(non_deletion)}", json.dumps(non_deletion, sort_keys=True))
    for case_id, reasons in sorted(blockers.items()):
        unresolved = [f"{k}:{d}" for k, d in results[case_id].unresolved if k not in ("intent", "account_link")]
        print(f"  blocked {case_id}: {sorted(set(reasons))} {unresolved}")
    for row in usage["rows"]:
        print("model usage by purpose:", json.dumps(row, sort_keys=True))
    for label, total in usage["totals"].items():
        cost = "unknown" if total["cost_usd"] is None else f"${total['cost_usd']:.4f}"
        print(f"model usage [{label}]: calls={total['calls']} in={total['input_tokens']} "
              f"out={total['output_tokens']} cost={cost}")

    if args.sample:
        predicted = {cid: (status, json.loads(steps)) for cid, status, steps in rows}
        metrics, details = evaluate(predicted, load_answers(DATASET / "sample_output.csv"))
        print(json.dumps(metrics, indent=2))
        for case_id, info in sorted(details.items()):
            if not info["exact"]:
                print(case_id, "status", info["status"], "missing", len(info["missing"]), "extra", len(info["extra"]))
        return 0

    final = Path(args.output) if args.output else REPO / "output.csv"
    if args.best_effort:
        export, classes, failures = best_effort(rows, results, blockers, auditor)
        (SCRATCH / f"export_classes_{name}.json").write_text(
            json.dumps(classes, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        counts = {}
        for entry in classes.values():
            key = f"{entry['class']}:{entry['status']}"
            counts[key] = counts.get(key, 0) + 1
        print("BEST-EFFORT EXPORT (not the strict gate). export classes:", json.dumps(counts, sort_keys=True))
        for case_id, entry in sorted(classes.items()):
            if entry["class"] != "verified":
                print(f"  {entry['class']} {case_id} {entry['status']}: {'; '.join(entry['assumptions'])}")
        if failures:
            for case_id, errors in sorted(failures.items()):
                print(f"  cannot export {case_id}: {errors}")
            print(f"best-effort export refused: {len(failures)} rows fail validation; {final} not written")
            return 3
        write_csv(final, export)
        print(f"best-effort output written: {final} ({len(export)} rows; "
              f"{sum(e['class'] == 'best_effort' for e in classes.values())} best-effort)")
        return 0
    if blockers:
        print(f"final export refused: {len(blockers)} rows blocked; {final} not written")
        return 2
    write_csv(final, rows)
    print(f"final output written: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
