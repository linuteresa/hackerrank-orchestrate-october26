"""Ghost Data Still Exists - deletion-audit agent (deterministic baseline).

Usage (from the repository root):

    python3 code/main.py                       # audit dataset/deletion_requests.csv -> output.csv
    python3 code/main.py --sample              # audit the 32 sample requests and evaluate
    python3 code/main.py --requests FILE --output FILE
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


def run(requests_name, output_path, auditor):
    requests = sorted(load_requests(DATASET, requests_name), key=lambda r: r.case_id)
    rows, results, problems = [], {}, {}
    for request in requests:
        result = auditor.audit(request)
        steps_json = to_json(result.steps)
        captured = {fid: ev.captured_at for fid, ev in auditor.store.files.items()}
        activity_ids = {a.activity_id: a.event_time for a in auditor.activity}

        def cite_ok(evidence_id, cutoff=request.audit_at):
            when = captured.get(evidence_id) or activity_ids.get(evidence_id)
            return when is not None and when <= cutoff

        errors = validate_row(request.case_id, result.status, steps_json, cite_ok)
        if errors:
            problems[request.case_id] = errors
        rows.append((request.case_id, result.status, steps_json))
        results[request.case_id] = result
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        writer.writerows(rows)
    return rows, results, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", action="store_true", help="audit and evaluate the 32 labelled samples")
    parser.add_argument("--requests", default=None, help="requests CSV name inside dataset/")
    parser.add_argument("--output", default=None, help="output CSV path")
    args = parser.parse_args(argv)

    auditor = Auditor(DATASET)
    if args.sample:
        requests = args.requests or "sample_deletion_requests.csv"
        output = Path(args.output or CODE_DIR / "eval" / "sample_predictions.csv")
    else:
        requests = args.requests or "deletion_requests.csv"
        output = Path(args.output or REPO / "output.csv")
    rows, results, problems = run(requests, output, auditor)
    print(f"wrote {len(rows)} rows to {output}; contract violations in {len(problems)} rows")
    for case_id, errors in sorted(problems.items())[:20]:
        print(f"  {case_id}: {errors[:3]}")
    if args.sample:
        predicted = {cid: (status, json.loads(steps)) for cid, status, steps in rows}
        metrics, details = evaluate(predicted, load_answers(DATASET / "sample_output.csv"))
        print(json.dumps(metrics, indent=2))
        for case_id, info in sorted(details.items()):
            if not info["exact"]:
                print(case_id, "status", info["status"])
                for step in info["missing"]:
                    print("   - missing", step)
                for step in info["extra"]:
                    print("   + extra  ", step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
