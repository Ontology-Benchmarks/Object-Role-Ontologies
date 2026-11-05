"""Compare ORO runtime, memory and results across the memory configurations."""

import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trace_peaks import run_peaks  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "results" / "memory"
DATASETS = [("Order Management", "order_mgmt"), ("Container Logistics", "logistics"),
            ("Procure-to-Pay", "p2p"), ("BPI 2017", "bpi17")]


def rows(path: Path) -> list:
    return list(csv.DictReader(open(path))) if path.exists() else []


def mean_sd(xs):
    xs = [float(x) for x in xs]
    return (statistics.fmean(xs), statistics.stdev(xs) if len(xs) > 1 else float("nan")) if xs else (float("nan"),) * 2


def fingerprint(d: Path) -> dict:
    """What the configuration computed, per run: bottom-up rows, top-down and scenario counterexamples."""
    td = {}
    for r in rows(d / "top_down_queries.csv"):
        td.setdefault(r["run"], {})[(r["category"], r["activity"], r["otype"])] = r["counterexamples"]
    sc = {}
    for r in rows(d / "scenarios.csv"):
        sc.setdefault(r["run"], {})[r["qid"]] = r["counterexamples"]
    bu = {r["run"]: r["bottom_up_rows"] for r in rows(d / "runs.csv")}
    return {run: (bu[run], td.get(run), sc.get(run)) for run in bu}


def main():
    conditions = sorted(p.name for p in (RESULTS).iterdir() if p.is_dir())
    conditions.sort(key=lambda c: c != "default")
    report = {}
    for cond in conditions:
        report[cond] = {}
        for name, slug in DATASETS:
            d = RESULTS / cond / slug
            runs = rows(d / "runs.csv")
            log = (d / "benchmark.log").read_text() if (d / "benchmark.log").exists() else ""
            if not runs:
                report[cond][name] = {"status": "failed", "error": log.strip().splitlines()[-1:] }
                continue
            peaks = run_peaks(d / "ontop_memory_trace.csv")
            ref = fingerprint(RESULTS / "default" / slug)
            fp = fingerprint(d)
            ref_one = next(iter(ref.values())) if ref else None
            same = all(v == ref_one for v in fp.values()) if ref_one else None
            entry = {"runs": len(runs), "same_results_as_default": same}
            for key in ["preprocessing_s", "bottom_up_s", "top_down_mean_s", "total_s", "py_peak_mb", "ontop_peak_mb"]:
                entry[key] = mean_sd([r[key] for r in runs])
            entry["ontop_anon_peak_mb"] = mean_sd([p["anon"] for p in peaks])
            entry["ontop_file_peak_mb"] = mean_sd([p["file"] for p in peaks])
            entry["ontop_rss_peak_mb"] = mean_sd([p["rss"] for p in peaks])
            entry["oro_peak_mb"] = entry["py_peak_mb"][0] + entry["ontop_peak_mb"][0]
            sc = rows(d / "scenario_summary.csv")
            entry["scenarios"] = {r["qid"]: {"mean_s": float(r["mean_s"]), "counterexamples": int(r["counterexamples"])} for r in sc}
            report[cond][name] = entry
    (RESULTS / "summary.json").write_text(json.dumps(report, indent=2))

    print(f"{'condition':<20} {'dataset':<20} {'total s':>14} {'BU s':>12} {'TD ms':>8} {'py MB':>7} "
          f"{'ontop MB':>9} {'anon':>6} {'file':>5} {'ORO MB':>7}  same")
    for cond, per_ds in report.items():
        for name, e in per_ds.items():
            if e.get("status") == "failed":
                print(f"{cond:<20} {name:<20} FAILED {e['error']}")
                continue
            print(f"{cond:<20} {name:<20} {e['total_s'][0]:>7.2f}±{e['total_s'][1]:<5.2f} "
                  f"{e['bottom_up_s'][0]:>6.2f}±{e['bottom_up_s'][1]:<4.2f} {e['top_down_mean_s'][0]*1000:>8.1f} "
                  f"{e['py_peak_mb'][0]:>7.0f} {e['ontop_peak_mb'][0]:>9.0f} {e['ontop_anon_peak_mb'][0]:>6.0f} "
                  f"{e['ontop_file_peak_mb'][0]:>5.0f} {e['oro_peak_mb']:>7.0f}  {e['same_results_as_default']}")
        for name, e in per_ds.items():
            for qid, s in e.get("scenarios", {}).items():
                print(f"{'':<20} {qid}: {s['mean_s']*1000:.1f} ms, {s['counterexamples']} counterexamples")


if __name__ == "__main__":
    main()
