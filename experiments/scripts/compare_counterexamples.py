"""Check that each OCPQ query returns exactly the ORO counterexamples.

ORO sets are <QID>.csv files: results/baselines/oro_counterexamples (queries/oro_ground_truth.sql
over the ORO DuckDB tables) or results/section7/oro_counterexamples (dumped by the ORO benchmark);
OCPQ sets are the violated root situations dumped by `baseline-harness ocpq --dump-dir`.
Q2 and Q4 are compared on objects (their roots bind only the object); the others on
(object, event) pairs.
"""

import argparse
import csv
import json
from pathlib import Path

OBJECT_LEVEL = {"Q2", "Q4"}


def read_rows(path: Path, with_event: bool) -> set:
    with open(path) as f:
        return {(r["obj"], r["event"]) if with_event else r["obj"] for r in csv.DictReader(f)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oro", type=Path, required=True)
    parser.add_argument("--ocpq", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--queries", nargs="*", default=["Q1", "Q2", "Q3", "Q4"])
    args = parser.parse_args()

    report = {}
    for qid in args.queries:
        with_event = qid not in OBJECT_LEVEL
        oro = read_rows(args.oro / f"{qid}.csv", with_event)
        ocpq = read_rows(args.ocpq / f"{qid}.csv", with_event)
        objects = lambda s: {x[0] if with_event else x for x in s}
        report[qid] = {
            "oro_rows": len(oro), "ocpq_rows": len(ocpq),
            "oro_objects": len(objects(oro)), "ocpq_objects": len(objects(ocpq)),
            "only_oro": sorted(oro - ocpq)[:10], "only_ocpq": sorted(ocpq - oro)[:10],
            "equivalent": oro == ocpq,
        }
        print(qid, "equivalent" if oro == ocpq else "DIFFERENT", report[qid])
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
