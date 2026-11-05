"""Native vs containerized ORO: runtime, memory and results (default settings, 4 runs each)."""

import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / "results"
DOCKER = RESULTS / "memory" / "default"
NATIVE = RESULTS / "native"
sys.path.insert(0, str(HERE))
from trace_peaks import run_peaks  # noqa: E402

DATASETS = [("Order Management", "order_mgmt"), ("Container Logistics", "logistics"),
            ("Procure-to-Pay", "p2p"), ("BPI 2017", "bpi17")]
METRICS = ["preprocessing_s", "bottom_up_s", "top_down_mean_s", "total_s", "py_peak_mb"]


def ms(xs):
    xs = [float(x) for x in xs]
    return statistics.fmean(xs), (statistics.stdev(xs) if len(xs) > 1 else float("nan"))


def outputs(bu_rows, td, sc):
    return (str(bu_rows),
            sorted((q["category"], q["activity"] or "", q["otype"], int(q["counterexamples"])) for q in td),
            sorted((s["qid"], int(s["counterexamples"])) for s in sc))


def main():
    native = [json.loads(l) for l in (NATIVE / "runs.jsonl").read_text().splitlines() if l]
    report = {}
    for name, slug in DATASETS:
        nat = [r for r in native if r["run"]["dataset"] == name]
        d = DOCKER / slug
        drun = list(csv.DictReader(open(d / "runs.csv")))
        dtd = list(csv.DictReader(open(d / "top_down_queries.csv")))
        dsc = list(csv.DictReader(open(d / "scenarios.csv")))
        peaks = run_peaks(d / "ontop_memory_trace.csv")
        entry = {"native_runs": len(nat), "docker_runs": len(drun)}
        for m in METRICS:
            entry[m] = {"native": ms([r["run"][m] for r in nat]), "docker": ms([r[m] for r in drun])}
        entry["ontop_mb"] = {
            "native_rss_peak": ms([r["run"]["ontop_peak_mb"] for r in nat]),
            "docker_rss_peak": ms([p["rss"] for p in peaks]),
            "docker_anon_peak": ms([p["anon"] for p in peaks]),
            "docker_memory_peak": ms([r["ontop_peak_mb"] for r in drun]),
        }
        entry["scenario_s"] = {}
        for qid in sorted({s["qid"] for s in dsc}):
            entry["scenario_s"][qid] = {
                "native": ms([s["seconds"] for r in nat for s in r["scenarios"] if s["qid"] == qid]),
                "docker": ms([s["seconds"] for s in dsc if s["qid"] == qid]),
            }
        ref = outputs(drun[0]["bottom_up_rows"], [q for q in dtd if q["run"] == "0"],
                      [s for s in dsc if s["run"] == "0"])
        entry["same_results"] = all(
            outputs(r["run"]["bottom_up_rows"], r["top_down_queries"], r["scenarios"]) == ref for r in nat)
        report[name] = entry
    (NATIVE / "native_vs_docker.json").write_text(json.dumps(report, indent=2))

    print(f"{'dataset':<20} {'':<7} {'prep s':>6} {'BU s':>6} {'TD ms':>6} {'total s':>12} {'py MB':>6} "
          f"{'ontop RSS':>9} {'ORO MB':>7}")
    for name, e in report.items():
        for side in ("docker", "native"):
            rss = e["ontop_mb"][f"{side}_rss_peak"][0]
            print(f"{name if side == 'docker' else '':<20} {side:<7} {e['preprocessing_s'][side][0]:>6.2f} "
                  f"{e['bottom_up_s'][side][0]:>6.2f} {e['top_down_mean_s'][side][0]*1000:>6.1f} "
                  f"{e['total_s'][side][0]:>6.2f}±{e['total_s'][side][1]:<5.2f} {e['py_peak_mb'][side][0]:>6.0f} "
                  f"{rss:>9.0f} {e['py_peak_mb'][side][0] + rss:>7.0f}")
        print(f"{'':<20} docker memory.peak {e['ontop_mb']['docker_memory_peak'][0]:.0f} MB (anon "
              f"{e['ontop_mb']['docker_anon_peak'][0]:.0f}); same results: {e['same_results']}; " +
              ", ".join(f"{q}: {v['docker'][0]*1000:.0f}->{v['native'][0]*1000:.0f} ms"
                        for q, v in e["scenario_s"].items()))


if __name__ == "__main__":
    main()
