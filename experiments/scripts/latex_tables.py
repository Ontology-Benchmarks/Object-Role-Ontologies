"""Write results/tables.tex: LaTeX rows (mean \\pm sample s.d. over the runs) for Tables 4-6.

Sources (all under experiments/results/):
* oro_benchmark/            ORO in Docker, the published runs (Table 4, ORO column of 5, Q1-Q3 of 6)
* native/*.jsonl            ORO without Docker, with and without transformation_role
* baselines/raw/*.jsonl     OC-DECLARE, TOTeM, and OCPQ on Q1-Q3
* section7/                 ORO and OCPQ on the revised Section 7 scenarios Q1-Q6
ORO memory is the per-run sum of the Python/DuckDB process and Ontop (Docker: container
memory.peak; native: Ontop process VmHWM).
"""

import csv
import json
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"
DATASETS = ["Order Management", "Container Logistics", "Procure-to-Pay", "BPI 2017"]
# Table 6 rows: (qid, log, checked schema). Q5e is optional in the paper (printed as Q5').
TABLE6 = [
    ("Q1", "Container Logistics", r"init(Forklift, Weigh)"),
    ("Q2", "BPI 2017", r"alt(Offer, \{Accepted, Cancelled, Refused\})"),
    ("Q3", "BPI 2017", r"clos(Offer, Sent)"),
    ("Q4", "Container Logistics", r"budg(Transport Doc., \{Reschedule Container, Depart\}, 2)"),
    ("Q5", "BPI 2017", r"carr$_{next}$(Offer, Sent, Returned)"),
    ("Q5e", "BPI 2017", r"carr(Offer, Sent, Returned)"),
    ("Q6", "BPI 2017", r"carr$_{\rightarrow}$(Offer, Sent, Returned)"),
]
PUBLISHED_QIDS = {"Q1", "Q2", "Q3"}  # identical in the original and the revised Section 7


def jsonl(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def read_csv(path: Path) -> list:
    with open(path) as f:
        return list(csv.DictReader(f))


def pm(xs, places: int, bold: bool = False) -> str:
    xs = [float(x) for x in xs]
    body = f"{statistics.fmean(xs):.{places}f} \\pm {statistics.stdev(xs):.{places}f}"
    return rf"$\mathbf{{{body}}}$" if bold else f"${body}$"


def by_ds(rows, ds):
    return [r for r in rows if r["dataset"] == ds]


def oro_variants() -> dict:
    native = RESULTS / "native"
    return {
        "Docker (published)": read_csv(RESULTS / "oro_benchmark" / "runs.csv"),
        "Native": [r["run"] for r in jsonl(native / "runs.jsonl")],
        r"Native, without \texttt{transformation\_role}":
            [r["run"] for r in jsonl(native / "runs_no_transformation.jsonl")],
    }


def table4(variants) -> list:
    lines = ["% tab:performance -- Dataset & Preprocessing & Bottom-up & Top-down & Total & DuckDB/Py & Ontop"]
    for variant, rows in variants.items():
        lines.append(f"% ORO, {variant}")
        for ds in DATASETS:
            rs = by_ds(rows, ds)
            lines.append(" & ".join([
                f"{ds:<20}",
                pm([r["preprocessing_s"] for r in rs], 2),
                pm([r["bottom_up_s"] for r in rs], 2),
                pm([r["top_down_mean_s"] for r in rs], 3),
                pm([r["total_s"] for r in rs], 2),
                pm([r["py_peak_mb"] for r in rs], 0),
                pm([r["ontop_peak_mb"] for r in rs], 0),
            ]) + r" \\")
    return lines


def table5(variants) -> list:
    raw = RESULTS / "baselines" / "raw"
    ocd, totem = jsonl(raw / "ocdeclare.jsonl"), jsonl(raw / "totem.jsonl")
    lines = []
    for variant, rows in variants.items():
        lines.append(f"% tab:comparison_bu -- ORO {variant}; "
                     "Dataset & ORO & OC-DECLARE & TOTeM (runtime s) & ORO & OC-DECLARE & TOTeM (peak MB)")
        for ds in DATASETS:
            o, d, t = by_ds(rows, ds), by_ds(ocd, ds), by_ds(totem, ds)
            lines.append(" & ".join([
                f"{ds:<20}",
                pm([r["total_s"] for r in o], 2),
                pm([r["total_s"] for r in d], 2),
                pm([r["total_s"] for r in t], 2),
                pm([float(r["py_peak_mb"]) + float(r["ontop_peak_mb"]) for r in o], 0),
                pm([r["peak_rss_mb"] for r in d], 0),
                pm([r["peak_rss_mb"] for r in t], 0),
            ]) + r" \\")
    return lines


def seconds_by_qid(rows, key="seconds") -> dict:
    out = {}
    for r in rows:
        values = r[key] if isinstance(r[key], list) else [r[key]]
        out.setdefault(r["qid"], []).extend(float(v) for v in values)
    return out


def table6_rows(oro, ocpq, counts, equivalence) -> list:
    lines = []
    for qid, log, schema in TABLE6:
        o_ms = [1000 * s for s in oro[qid]]
        q_ms = [1000 * s for s in ocpq[qid]]
        oro_faster = statistics.fmean(o_ms) <= statistics.fmean(q_ms)
        q_cell = pm(q_ms, 1, not oro_faster) if equivalence[qid]["equivalent"] else "n/e"
        label = "% optional: carr also holds\nQ5'" if qid == "Q5e" else qid
        lines.append(" & ".join([label, log, schema, str(counts[qid]), pm(o_ms, 1, oro_faster), q_cell]) + r" \\")
    return lines


def table6() -> list:
    section7 = RESULTS / "section7"
    s7_oro_rows = read_csv(section7 / "oro" / "scenarios.csv")
    s7_oro = seconds_by_qid(s7_oro_rows)
    s7_ocpq = seconds_by_qid(jsonl(section7 / "raw" / "ocpq.jsonl"))
    counts = {r["qid"]: r["counterexamples"] for r in s7_oro_rows}
    equivalence = json.loads((section7 / "equivalence.json").read_text())

    published = {q: s for q, s in seconds_by_qid(read_csv(RESULTS / "oro_benchmark" / "scenarios.csv")).items()
                 if q in PUBLISHED_QIDS}
    published_ocpq = {q: s for q, s in seconds_by_qid(jsonl(RESULTS / "baselines" / "raw" / "ocpq.jsonl")).items()
                      if q in PUBLISHED_QIDS}

    header = "% ID & Log & Checked schema & Counterex. & ORO (ms) & OCPQ (ms)"
    lines = ["% tab:comparison_td -- Q1-Q3: published runs of both tools; Q4-Q6: Section 7 runs", header]
    lines += table6_rows({**s7_oro, **published}, {**s7_ocpq, **published_ocpq}, counts, equivalence)
    lines += ["", "% Alternative: every row from the Section 7 runs (same conditions for all rows)", header]
    lines += table6_rows(s7_oro, s7_ocpq, counts, equivalence)
    return lines


def main():
    variants = oro_variants()
    lines = table4(variants) + [""] + table5(variants) + [""] + table6()
    out = RESULTS / "tables.tex"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
