"""Native ORO with vs without the transformation_role category (4 runs each)."""

import json
import statistics
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[2] / "results" / "native"
DATASETS = ["Order Management", "Container Logistics", "Procure-to-Pay", "BPI 2017"]


def load(name):
    return [json.loads(l) for l in (NATIVE / name).read_text().splitlines() if l]


def mean(rs, key):
    return statistics.fmean(float(r["run"][key]) for r in rs)


def td(r):
    return sorted((q["category"], q["activity"] or "", q["otype"], q["counterexamples"]) for q in r["top_down_queries"])


def main():
    full, skip = load("runs.jsonl"), load("runs_no_transformation.jsonl")
    report = {}
    print(f"{'dataset':<20} {'variant':<18} {'BU s':>6} {'transf s':>8} {'total s':>8} {'TD ms':>6} "
          f"{'py MB':>6} {'ontop MB':>13} {'ORO MB':>7}")
    for ds in DATASETS:
        f = [r for r in full if r["run"]["dataset"] == ds]
        s = [r for r in skip if r["run"]["dataset"] == ds]
        same_td = all(td(r) == td(f[0]) for r in s)
        same_sc = all(sorted((x["qid"], x["counterexamples"]) for x in r["scenarios"]) ==
                      sorted((x["qid"], x["counterexamples"]) for x in f[0]["scenarios"]) for r in s)
        report[ds] = {}
        for label, rs in (("with transformation", f), ("without", s)):
            peaks = [float(r["run"]["ontop_peak_mb"]) for r in rs]
            e = {k: mean(rs, k) for k in ["bottom_up_s", "total_s", "top_down_mean_s", "py_peak_mb", "ontop_peak_mb"]}
            e["ontop_peak_range_mb"] = [min(peaks), max(peaks)]
            e["transformation_s"] = mean(rs, "bu_transformation_role_s") if "bu_transformation_role_s" in rs[0]["run"] else 0.0
            report[ds][label] = e
            print(f"{ds if label != 'without' else '':<20} {label:<18} {e['bottom_up_s']:>6.2f} {e['transformation_s']:>8.2f} "
                  f"{e['total_s']:>8.2f} {e['top_down_mean_s']*1000:>6.1f} {e['py_peak_mb']:>6.0f} "
                  f"{e['ontop_peak_mb']:>5.0f} ({min(peaks):.0f}-{max(peaks):.0f}) {e['py_peak_mb'] + e['ontop_peak_mb']:>7.0f}")
        report[ds]["same_top_down_and_scenarios"] = same_td and same_sc
        print(f"{'':<20} top-down and Q1-Q4 unchanged: {same_td and same_sc}")
    (NATIVE / "transformation_skip.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
