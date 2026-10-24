"""Compare predictions with labelled answers (``sample_output.csv``).

Metrics:

* status accuracy  - exact ``deletion_status`` match per case;
* step accuracy    - multiset match of full steps (all fields except ``step_no``), reported as
                     precision / recall / F1 over all cases;
* field accuracy   - per field, position-aligned agreement over aligned step pairs;
* exact sequence   - the whole ``steps_json`` array equal (ignoring key order).
"""

import csv
import json
from collections import Counter

STEP_FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def load_answers(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(handle)}


def _key(step):
    return tuple(step[f] for f in STEP_FIELDS)


def evaluate(predicted, expected):
    """``predicted``/``expected``: case_id -> (status, steps). Returns (metrics, per-case details)."""
    cases = sorted(expected)
    status_ok = sum(predicted.get(c, (None,))[0] == expected[c][0] for c in cases)
    tp = fp = fn = 0
    field_hits = Counter()
    field_total = 0
    exact = 0
    details = {}
    for c in cases:
        p_status, p_steps = predicted.get(c, (None, []))
        e_status, e_steps = expected[c]
        p_count, e_count = Counter(map(_key, p_steps)), Counter(map(_key, e_steps))
        common = sum((p_count & e_count).values())
        tp += common
        fp += sum(p_count.values()) - common
        fn += sum(e_count.values()) - common
        for p, e in zip(p_steps, e_steps):
            field_total += 1
            for f in STEP_FIELDS:
                field_hits[f] += p[f] == e[f]
        field_total += abs(len(p_steps) - len(e_steps))
        same = [_key(s) for s in p_steps] == [_key(s) for s in e_steps]
        exact += same and p_status == e_status
        details[c] = {
            "status": (p_status, e_status),
            "missing": sorted(set(e_count) - set(p_count)),
            "extra": sorted(set(p_count) - set(e_count)),
            "exact": same and p_status == e_status,
        }
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    metrics = {
        "cases": len(cases),
        "status_accuracy": status_ok / len(cases),
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "field_accuracy": {f: field_hits[f] / field_total if field_total else 0.0 for f in STEP_FIELDS},
        "exact_sequence_accuracy": exact / len(cases),
    }
    return metrics, details
