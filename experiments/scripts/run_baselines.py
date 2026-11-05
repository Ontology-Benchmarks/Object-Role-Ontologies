"""Run the OC-DECLARE, TOTeM and OCPQ baselines on the four paper logs.

Every repetition runs in a fresh process (as the ORO benchmark does), sequentially:

* OC-DECLARE: end-to-end discovery (OCEL 2.0 JSON import + linking + discovery), library defaults.
* TOTeM: end-to-end (OCEL 2.0 SQLite import with TOTeM's vendored ocpa + mine_totem(tau=0.9)),
  inside the totem-env image (Python 3.9.13, TOTeM's Pipfile.lock).
* OCPQ: the log is loaded, each scenario query is evaluated once untimed (warm-up) and once timed.
  The violated root situations of the first run are kept for the equivalence check.

Raw per-run JSON lines go to <results>/raw/*.jsonl, stderr logs to <results>/logs/.
Default: Tables 5-6 set into results/baselines; `--scenario-set s7 --tools ocpq` for Section 7.
"""

import argparse
import json
import subprocess
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parent.parent
ROOT = EXPERIMENTS.parent
BASELINES = EXPERIMENTS / "baselines"
HARNESS = BASELINES / "harness" / "target" / "release" / "baseline-harness"
TOTEM_REPO = BASELINES / "TOTeM-temporal-object-type-model"
QUERIES = EXPERIMENTS / "queries" / "ocpq"
TOTEM_IMAGE = "totem-env:py3.9.13"

DATASETS = [
    ("Order Management", "order-management"),
    ("Container Logistics", "ContainerLogistics"),
    ("Procure-to-Pay", "ocel2-p2p"),
    ("BPI 2017", "bpi17_ocel"),
]
# Original Tables 5-6 run; Q4old = clos(Offer, Returned), stored as "Q4" in results/baselines.
SCENARIOS = {"Container Logistics": ["Q1"], "BPI 2017": ["Q2", "Q3", "Q4old"]}
# Revised Section 7 scenarios (budg, carr_next, carr, carr_forward); select with --scenario-set s7.
SCENARIOS_S7 = {"Container Logistics": ["Q1", "Q4"], "BPI 2017": ["Q2", "Q3", "Q5", "Q5e", "Q6"]}


def run_json(cmd, log_path: Path) -> list:
    with open(log_path, "a") as log:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=log, text=True, check=True)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]


def append(path: Path, record: dict):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "input" / "data")
    parser.add_argument("--results", type=Path, help="default: results/baselines, or results/section7 with s7")
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--tools", nargs="*", default=["ocdeclare", "totem", "ocpq"])
    parser.add_argument("--scenario-set", choices=["original", "s7"], default="original")
    args = parser.parse_args()
    scenarios = SCENARIOS_S7 if args.scenario_set == "s7" else SCENARIOS
    args.results = args.results or EXPERIMENTS / "results" / ("section7" if args.scenario_set == "s7" else "baselines")

    raw = args.results / "raw"
    logs = args.results / "logs"
    raw.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    for name, stem in DATASETS:
        ocel_json = args.data / f"{stem}.json"
        for run in range(args.runs):
            if "ocdeclare" in args.tools:
                model = args.results / "models" / f"oc-declare-{stem}.json"
                model.parent.mkdir(exist_ok=True)
                cmd = [str(HARNESS), "ocdeclare", "--ocel", str(ocel_json)]
                if run == 0:
                    cmd += ["--model-out", str(model)]
                (rec,) = run_json(cmd, logs / f"ocdeclare-{stem}.log")
                rec.update(dataset=name, run=run)
                append(raw / "ocdeclare.jsonl", rec)
                print(f"[OC-DECLARE] {name} run {run}: {rec['total_s']:.3f}s {rec['peak_rss_mb']:.0f}MB "
                      f"({rec['constraints']} constraints)", flush=True)

            if "totem" in args.tools:
                cmd = ["docker", "run", "--rm", "--network", "none",
                       "-v", f"{TOTEM_REPO}:/totem:ro", "-v", f"{EXPERIMENTS}:/work",
                       TOTEM_IMAGE, "python", "/work/scripts/run_totem.py",
                       "--totem", "/totem", "--sqlite", f"/work/baselines/totem-env/sqlite/{stem}.sqlite"]
                (rec,) = run_json(cmd, logs / f"totem-{stem}.log")
                rec.update(dataset=name, run=run)
                append(raw / "totem.jsonl", rec)
                print(f"[TOTeM] {name} run {run}: {rec['total_s']:.2f}s (import {rec['import_s']:.2f}s) "
                      f"{rec['peak_rss_mb']:.0f}MB", flush=True)

            if "ocpq" in args.tools and name in scenarios:
                cmd = [str(HARNESS), "ocpq", "--ocel", str(ocel_json), "--runs", "1", "--warmup", "1",
                       "--queries", *[str(QUERIES / q) for q in scenarios[name]]]
                if run == 0:
                    cmd += ["--dump-dir", str(args.results / "ocpq_counterexamples")]
                for rec in run_json(cmd, logs / f"ocpq-{stem}.log"):
                    rec.update(dataset=name, run=run)
                    append(raw / "ocpq.jsonl", rec)
                    print(f"[OCPQ] {rec['qid']} run {run}: {rec['mean_s']*1000:.2f}ms "
                          f"violated={rec['violated']}", flush=True)


if __name__ == "__main__":
    main()
