"""Repeatable runtime and peak-memory benchmark for the ORO pipeline.

Each repetition runs in a fresh Python process so that the reported peak RSS
belongs to that run only. Stages per run:

* preprocessing: OCEL JSON load/transform, DuckDB load, OIG tables (which the
  Ontop mapping reads from) and OBDA/lens generation.
* ontop_startup: until Ontop serves the newly staged VKG (restart/reload and
  lazy initialisation). Reported separately, never inside another stage.
* bottom_up: the query of every bottom-up schema (schemata.BOTTOM_UP).
* top_down: counterexample queries (generated from the DL axioms) of init, clos, single,
  ext and eres for every candidate of the log; reported as mean time per query.
* scenarios: the counterexample queries of the paper's Section 7 (Q1-Q6).

Timed SPARQL calls run without retries, so a connectivity issue fails the run
instead of adding retry back-off to the measured time.

Ontop peak memory is the cgroup ``memory.peak`` of the Ontop container. It is
only available when the Docker socket is reachable from this container, in
which case Ontop is also restarted before each run (fresh JVM, fresh peak).
Without the socket, Ontop's dev-mode file watcher reloads the mapping and the
Ontop memory column is NaN.
"""

import http.client
import json
import multiprocessing
import socket
import statistics
import time
import urllib.parse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from SPARQLWrapper import JSON as SPARQL_JSON
from SPARQLWrapper import SPARQLWrapper

from .datasets import DATA_DIR, DATASETS
from .schemata import get_schema

EVENT_COUNT_QUERY = """
PREFIX ocel: <https://stl.mie.utoronto.ca/ontologies/ocel/>
SELECT (COUNT(?e) AS ?n) WHERE { ?e a ocel:Event }
""".strip()

PREPROCESSING_STAGES = ("load_transform_s", "duckdb_load_s", "oig_s", "mapping_generation_s")


@dataclass(frozen=True)
class ScenarioQuery:
    """A top-down check of a dataset: schema ``schema`` with free variables ``bindings``.

    ``bindings`` are check() arguments (paper names ot, a, A, N, a1, a2, ...), with the log's
    labels for object types and activities.
    """

    qid: str
    description: str
    schema: str
    bindings: Tuple[Tuple[str, object], ...]

    def arguments(self) -> Dict[str, object]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.bindings}


TOP_DOWN_SCHEMATA = ("creation_role", "closure_role", "single_use_role", "external_object_type",
                     "resource_object_type")


@dataclass(frozen=True)
class BenchmarkDataset:
    name: str
    ocel_json_path: str
    db_dir: str  # folder of the DuckDB file (under the input folder Ontop mounts)
    scenarios: Tuple[ScenarioQuery, ...] = ()


@dataclass(frozen=True)
class BenchmarkConfig:
    ontop_sparql_endpoint: str = "http://ontop:8080/sparql"
    docker_socket: str = "/var/run/docker.sock"
    ontop_container: str = "ontop_service"
    # Schemata checked top-down for every candidate of the log ((ot, a) pairs or object types).
    top_down_categories: Tuple[str, ...] = TOP_DOWN_SCHEMATA
    # None: enumerate all counterexamples (comparable to OCPQ, which returns all).
    top_down_limit: Optional[int] = None
    ready_timeout_s: float = 180.0
    reload_detect_timeout_s: float = 20.0
    # Ontop must keep serving the expected log this long before timing starts.
    stable_window_s: float = 5.0
    poll_interval_s: float = 0.25
    # Role categories left out of the bottom-up stage (e.g. ("transformation_role",)).
    skip_categories: Tuple[str, ...] = ()
    # If set, each scenario's counterexamples are written there as <qid>.csv (obj, event)
    # and its query as <qid>.rq, after timing.
    counterexample_dir: Optional[str] = None


# Section 7 of the paper. "Sent" covers both send channels of BPI 2017.
_SENT = ("Sent (mail and online)_complete", "Sent (online only)_complete")
_OFFER_SENT_RETURNED = (("ot", "offer"), ("a1", _SENT), ("a2", "Returned_complete"))
PAPER_SCENARIOS: Dict[str, Tuple[ScenarioQuery, ...]] = {
    "Container Logistics": (
        ScenarioQuery("Q1", "init(Forklift, Weigh)", "init", (("ot", "forklift"), ("a", "Weigh"))),
        ScenarioQuery("Q4", "budg(Transport Document, {Reschedule Container, Depart}, 2)", "budg",
                      (("ot", "transport_document"), ("A", ("Reschedule Container", "Depart")), ("N", 2))),
    ),
    "BPI 2017": (
        ScenarioQuery("Q2", "alt(Offer, {Accepted, Cancelled, Refused})", "alt",
                      (("ot", "offer"), ("A", ("Accepted_complete", "Cancelled_complete", "Refused_complete")))),
        ScenarioQuery("Q3", "clos(Offer, Sent)", "clos", (("ot", "offer"), ("a", "Sent (mail and online)_complete"))),
        ScenarioQuery("Q5", "carr_next(Offer, Sent, Returned)", "carr_next", _OFFER_SENT_RETURNED),
        ScenarioQuery("Q5e", "carr(Offer, Sent, Returned)", "carr", _OFFER_SENT_RETURNED),
        ScenarioQuery("Q6", "carr_fwd(Offer, Sent, Returned)", "carr_fwd", _OFFER_SENT_RETURNED),
    ),
}


def paper_datasets(data_path: str = DATA_DIR) -> List[BenchmarkDataset]:
    """The four evaluation logs of the paper, with their scenario queries."""
    data_path = data_path.rstrip("/") + "/"
    return [
        BenchmarkDataset(d.name, data_path + d.file_name, data_path + d.folder + "/",
                         PAPER_SCENARIOS.get(d.name, ()))
        for d in DATASETS.values()
    ]


# ---------------------------------------------------------------------------
# Docker Engine API (Ontop restart and cgroup peak memory)
# ---------------------------------------------------------------------------


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 120.0):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerEngine:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    @classmethod
    def connect(cls, socket_path: str) -> Optional["DockerEngine"]:
        engine = cls(socket_path)
        try:
            status, _ = engine._request("GET", "/_ping")
        except OSError:
            return None
        return engine if status == 200 else None

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, bytes]:
        conn = _UnixHTTPConnection(self.socket_path)
        try:
            payload = json.dumps(body) if body is not None else None
            conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def restart(self, container: str, stop_timeout_s: int = 10) -> None:
        status, data = self._request("POST", f"/containers/{container}/restart?t={stop_timeout_s}")
        if status != 204:
            raise RuntimeError(f"Docker restart of {container} failed ({status}): {data!r}")

    def exec_output(self, container: str, cmd: List[str]) -> str:
        status, data = self._request(
            "POST",
            f"/containers/{container}/exec",
            {"AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": cmd},
        )
        if status != 201:
            raise RuntimeError(f"Docker exec create on {container} failed ({status}): {data!r}")
        exec_id = json.loads(data)["Id"]
        status, data = self._request("POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": True})
        if status != 200:
            raise RuntimeError(f"Docker exec start on {container} failed ({status}): {data!r}")
        return data.decode(errors="replace")

    def peak_memory_mb(self, container: str) -> float:
        # cgroup v2 first, then cgroup v1.
        output = self.exec_output(container, [
            "sh", "-c",
            "cat /sys/fs/cgroup/memory.peak 2>/dev/null "
            "|| cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes",
        ])
        return int(output.strip().split()[-1]) / (1024 * 1024)


# ---------------------------------------------------------------------------
# Single run (executed in a fresh process)
# ---------------------------------------------------------------------------


def _sparql_scalar(endpoint: str, query: str, timeout_s: float) -> Optional[int]:
    wrapper = SPARQLWrapper(endpoint)
    wrapper.setReturnFormat(SPARQL_JSON)
    wrapper.setTimeout(max(1, int(timeout_s)))
    wrapper.setQuery(query)
    try:
        results = wrapper.query().convert()
    except Exception:
        return None
    bindings = results.get("results", {}).get("bindings", []) if isinstance(results, dict) else []
    if not bindings:
        return None
    return int(next(iter(bindings[0].values()))["value"])


def _health_up(endpoint: str) -> bool:
    parsed = urllib.parse.urlparse(endpoint)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=2)
    try:
        conn.request("GET", "/actuator/health")
        return conn.getresponse().status == 200
    except OSError:
        return False
    finally:
        conn.close()


def _wait_until_serving(endpoint: str, expected_events: int, config: BenchmarkConfig) -> float:
    """Block until Ontop stably serves the VKG of the expected log.

    Dev mode reloads once per staged file, so the endpoint can come up and go
    down again. Returns the time at which the final stable streak began.
    """
    deadline = time.perf_counter() + config.ready_timeout_s
    ready_since: Optional[float] = None
    while time.perf_counter() < deadline:
        now = time.perf_counter()
        if _sparql_scalar(endpoint, EVENT_COUNT_QUERY, timeout_s=60) == expected_events:
            if ready_since is None:
                ready_since = now
            elif now - ready_since >= config.stable_window_s:
                return ready_since
        else:
            ready_since = None
        time.sleep(config.poll_interval_s)
    raise TimeoutError(f"Ontop did not serve a VKG with {expected_events} events within {config.ready_timeout_s}s")


def _wait_for_dev_mode_reload(endpoint: str, config: BenchmarkConfig) -> bool:
    """Wait for Ontop's file watcher to take the endpoint down after re-staging."""
    deadline = time.perf_counter() + config.reload_detect_timeout_s
    while time.perf_counter() < deadline:
        if not _health_up(endpoint):
            return True
        time.sleep(config.poll_interval_s)
    return False


def render_scenario(scenario: ScenarioQuery, inferrer) -> str:
    """SPARQL counterexample query of a scenario, generated from its DL axiom."""
    return inferrer.counterexample_query(scenario.schema, **scenario.arguments())


def top_down_instantiations(inferrer, schema_names: Sequence[str]):
    """(schema, bindings) for every candidate of the log: (ot, a) pairs, or object types."""
    role_pairs, _ = inferrer._catalog()
    for name in schema_names:
        schema = get_schema(name)
        if schema.free_variables == ("ot", "a"):
            candidates = [{"ot": ot, "a": a} for a, ot in role_pairs]
        elif schema.free_variables == ("ot",):
            candidates = [{"ot": ot} for ot in sorted({ot for _, ot in role_pairs})]
        else:
            raise ValueError(f"{schema.label} has free variables {schema.free_variables}; "
                             "only (ot, a) and (ot) schemata are enumerated top-down.")
        for bindings in candidates:
            yield schema, inferrer._with_derived(schema, bindings)


def _timed_query(inferrer, query: str) -> Tuple[float, pd.DataFrame]:
    """Return (seconds, result)."""
    start = time.perf_counter()
    result_df = inferrer.query_sparql(query)
    return time.perf_counter() - start, result_df


def _counts(result_df: pd.DataFrame) -> Dict[str, int]:
    """Result rows and distinct violating objects."""
    objects = result_df["obj"].nunique() if "obj" in result_df.columns else len(result_df)
    return {"rows": len(result_df), "counterexamples": objects}


def _write_counterexamples(out_dir: Path, qid: str, query: str, result_df: pd.DataFrame) -> None:
    """<qid>.csv with the local names of (obj, event), as compared against OCPQ, and <qid>.rq."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local = lambda v: "" if pd.isna(v) else str(v).rsplit("/", 1)[-1]
    table = pd.DataFrame({col: result_df[col].map(local) if col in result_df.columns else ""
                          for col in ("obj", "event")}, index=result_df.index)
    table.to_csv(out_dir / f"{qid}.csv", index=False)
    (out_dir / f"{qid}.rq").write_text(query + "\n")


def run_single(dataset: BenchmarkDataset, config: BenchmarkConfig, run_index: int) -> Dict:
    """Run the full pipeline once and return its timings and peak memory."""
    import resource

    from .inferrer import OCELSchemaInferrer

    docker = DockerEngine.connect(config.docker_socket)

    inferrer = OCELSchemaInferrer(
        dataset.ocel_json_path,
        db_dir=dataset.db_dir,
        ontop_sparql_endpoint=config.ontop_sparql_endpoint,
        sparql_max_attempts=1,
    )
    inferrer.run_inference(keep_db=True, update_oro_artifacts=True)
    record: Dict = {"dataset": dataset.name, "run": run_index, **inferrer.stage_timings}
    record["preprocessing_s"] = sum(inferrer.stage_timings[s] for s in PREPROCESSING_STAGES)
    record["events"] = inferrer.event_count

    start = time.perf_counter()
    if docker is not None:
        docker.restart(config.ontop_container)
        record["ontop_reload"] = "docker_restart"
    else:
        reloaded = _wait_for_dev_mode_reload(config.ontop_sparql_endpoint, config)
        record["ontop_reload"] = "dev_mode" if reloaded else "dev_mode_not_observed"
    ready_at = _wait_until_serving(config.ontop_sparql_endpoint, inferrer.event_count, config)
    record["ontop_startup_s"] = ready_at - start

    # Bottom-up: every role-category inference query.
    category_defs = [c for c in inferrer.get_default_ontological_role_categories()
                     if c.name not in config.skip_categories]
    record["skipped_categories"] = list(config.skip_categories)
    bottom_up_total = 0.0
    inferred_rows = 0
    for category in category_defs:
        start = time.perf_counter()
        inferred_df = inferrer.categorize_roles_by_ontology(categories=[category])
        elapsed = time.perf_counter() - start
        record[f"bu_{category.name}_s"] = elapsed
        bottom_up_total += elapsed
        inferred_rows += len(inferred_df)
    record["bottom_up_s"] = bottom_up_total
    record["bottom_up_rows"] = inferred_rows
    record["total_s"] = record["preprocessing_s"] + record["bottom_up_s"]

    # Top-down: the counterexample query of each schema for every candidate of the log.
    # Query generation (from the DL axiom) is untimed; ext and eres take R_init/R_close and the
    # activities of ot from the bottom-up results above.
    top_down_queries = []
    for schema, bindings in top_down_instantiations(inferrer, config.top_down_categories):
        if schema.subject != "concept" and schema.counterexample_query(**bindings) is None:
            continue  # no individual can violate it (ext of a type without init/closure roles)
        start = time.perf_counter()
        result_df = inferrer.evaluate(schema, bindings, config.top_down_limit)  # eres: one witness query per a
        seconds = time.perf_counter() - start
        top_down_queries.append({
            "category": schema.name, "activity": bindings.get("a"), "otype": bindings["ot"],
            "seconds": seconds, **_counts(result_df),
        })
    td_seconds = [q["seconds"] for q in top_down_queries]
    record["top_down_queries"] = len(td_seconds)
    record["top_down_mean_s"] = statistics.fmean(td_seconds) if td_seconds else float("nan")
    record["top_down_sum_s"] = sum(td_seconds)

    scenario_results = []
    for scenario in dataset.scenarios:
        query = render_scenario(scenario, inferrer)
        seconds, result_df = _timed_query(inferrer, query)
        scenario_results.append({
            "qid": scenario.qid, "description": scenario.description,
            "seconds": seconds, **_counts(result_df),
        })
        if config.counterexample_dir:
            _write_counterexamples(Path(config.counterexample_dir), scenario.qid, query, result_df)

    # ru_maxrss is in KiB on Linux.
    record["py_peak_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    record["ontop_peak_mb"] = (
        docker.peak_memory_mb(config.ontop_container) if docker is not None else float("nan")
    )

    return {"run": record, "top_down_queries": top_down_queries, "scenarios": scenario_results}


# ---------------------------------------------------------------------------
# Driver and aggregation
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResults:
    runs: pd.DataFrame
    top_down_queries: pd.DataFrame
    scenarios: pd.DataFrame
    warmup_runs: int = 0
    notes: List[str] = field(default_factory=list)

    def measured(self, df: pd.DataFrame) -> pd.DataFrame:
        # Empty frames (e.g. no dataset has scenarios) have no columns at all.
        if df.empty:
            return df
        return df[df["run"] >= self.warmup_runs]

    def summary(self) -> pd.DataFrame:
        """Mean and sample s.d. per dataset over the measured (non-warm-up) runs."""
        metrics = [
            *PREPROCESSING_STAGES, "preprocessing_s", "ontop_startup_s", "bottom_up_s",
            "top_down_mean_s", "total_s", "py_peak_mb", "ontop_peak_mb",
        ]
        grouped = self.measured(self.runs).groupby("dataset", sort=False)[metrics]
        summary = grouped.agg(["mean", "std"])
        summary.columns = [f"{metric}_{stat}" for metric, stat in summary.columns]
        summary["runs"] = grouped.size()
        summary["top_down_queries"] = self.measured(self.runs).groupby("dataset", sort=False)["top_down_queries"].max()
        return summary

    def scenario_summary(self) -> pd.DataFrame:
        measured = self.measured(self.scenarios)
        if measured.empty:
            return measured
        return measured.groupby(["qid", "dataset", "description"], sort=False).agg(
            counterexamples=("counterexamples", "max"),
            counterexamples_min=("counterexamples", "min"),
            rows=("rows", "max"),
            mean_s=("seconds", "mean"),
            std_s=("seconds", "std"),
            runs=("seconds", "size"),
        ).reset_index()

    def latex_rows(self, digits: int = 2, query_digits: int = 3) -> str:
        """Table rows (ORO columns only; others left as \\tbd) for the three paper tables."""
        def fmt(value: float, places: int = digits) -> str:
            return r"\tbd" if pd.isna(value) else f"{value:.{places}f}"

        def pm(mean: float, std: float, places: int = digits) -> str:
            return rf"${fmt(mean, places)} \pm {fmt(std, places)}$"

        s = self.summary()
        lines = ["% tab:performance"]
        for name, row in s.iterrows():
            lines.append(" & ".join([
                f"{name:<20}",
                pm(row.preprocessing_s_mean, row.preprocessing_s_std),
                pm(row.bottom_up_s_mean, row.bottom_up_s_std),
                pm(row.top_down_mean_s_mean, row.top_down_mean_s_std, query_digits),
                pm(row.total_s_mean, row.total_s_std),
                fmt(row.py_peak_mb_mean),
                fmt(row.ontop_peak_mb_mean),
            ]) + r" \\")

        lines.append("% tab:comparison_bu (ORO columns)")
        for name, row in s.iterrows():
            oro_mem = row.py_peak_mb_mean + row.ontop_peak_mb_mean
            lines.append(" & ".join([
                f"{name:<20}", fmt(row.total_s_mean), r"\tbd", r"\tbd",
                fmt(oro_mem), r"\tbd", r"\tbd",
            ]) + r" \\")

        lines.append("% tab:comparison_td (ORO columns)")
        for _, row in self.scenario_summary().iterrows():
            lines.append(" & ".join([
                row.qid, row.dataset, row.description,
                str(int(row.counterexamples)), fmt(row.mean_s, query_digits), r"\tbd",
            ]) + r" \\")
        return "\n".join(lines)

    def save(self, results_dir: str) -> None:
        out = Path(results_dir)
        out.mkdir(parents=True, exist_ok=True)
        self.runs.to_csv(out / "runs.csv", index=False)
        self.top_down_queries.to_csv(out / "top_down_queries.csv", index=False)
        self.scenarios.to_csv(out / "scenarios.csv", index=False)
        self.summary().to_csv(out / "summary.csv")
        self.scenario_summary().to_csv(out / "scenario_summary.csv", index=False)
        (out / "tables.tex").write_text(self.latex_rows() + "\n")


def run_benchmark(
    datasets: Sequence[BenchmarkDataset],
    runs: int = 4,
    warmup_runs: int = 0,
    config: Optional[BenchmarkConfig] = None,
    results_dir: Optional[str] = None,
) -> BenchmarkResults:
    """Run every dataset ``warmup_runs + runs`` times, each in a fresh process."""
    config = config or BenchmarkConfig()
    notes = []
    if DockerEngine.connect(config.docker_socket) is None:
        notes.append(
            f"Docker socket {config.docker_socket} not reachable: Ontop is reloaded via dev mode "
            "and Ontop peak memory is not measured."
        )
        print("NOTE:", notes[-1])

    run_rows, td_rows, scenario_rows = [], [], []
    spawn = multiprocessing.get_context("spawn")
    for dataset in datasets:
        for run_index in range(warmup_runs + runs):
            label = "warm-up" if run_index < warmup_runs else f"run {run_index - warmup_runs + 1}/{runs}"
            print(f"=== {dataset.name}: {label} ===")
            with ProcessPoolExecutor(max_workers=1, mp_context=spawn) as pool:
                result = pool.submit(run_single, dataset, config, run_index).result()
            run_rows.append(result["run"])
            td_rows.extend({"dataset": dataset.name, "run": run_index, **q} for q in result["top_down_queries"])
            scenario_rows.extend({"dataset": dataset.name, "run": run_index, **q} for q in result["scenarios"])
            r = result["run"]
            print(
                f"    preprocessing={r['preprocessing_s']:.2f}s ontop_startup={r['ontop_startup_s']:.2f}s "
                f"bottom_up={r['bottom_up_s']:.2f}s top_down_mean={r['top_down_mean_s']:.4f}s "
                f"(n={r['top_down_queries']}) py_peak={r['py_peak_mb']:.0f}MB ontop_peak={r['ontop_peak_mb']:.0f}MB"
            )

    results = BenchmarkResults(
        runs=pd.DataFrame(run_rows),
        top_down_queries=pd.DataFrame(td_rows),
        scenarios=pd.DataFrame(scenario_rows),
        warmup_runs=warmup_runs,
        notes=notes,
    )
    if results_dir:
        results.save(results_dir)
        (Path(results_dir) / "config.json").write_text(json.dumps({
            "runs": runs, "warmup_runs": warmup_runs, "config": asdict(config),
            "datasets": [asdict(d) for d in datasets], "notes": notes,
        }, indent=2))
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark the ORO pipeline on the paper datasets.")
    parser.add_argument("--results-dir", required=True,
                        help="Output directory (e.g. experiments/results/oro_benchmark_rerun/).")
    parser.add_argument("--data-path", default=DATA_DIR)
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--warmup-runs", type=int, default=0)
    parser.add_argument("--datasets", nargs="*", help="Subset of dataset names (default: all four).")
    parser.add_argument("--skip-category", action="append", default=[],
                        help="Leave a role category out of bottom-up (repeatable).")
    parser.add_argument("--counterexample-dir", help="Write each scenario's counterexamples here.")
    args = parser.parse_args()

    selected = paper_datasets(args.data_path)
    if args.datasets:
        selected = [d for d in selected if d.name in set(args.datasets)]
    config = BenchmarkConfig(skip_categories=tuple(args.skip_category), counterexample_dir=args.counterexample_dir)
    benchmark = run_benchmark(selected, runs=args.runs, warmup_runs=args.warmup_runs, config=config,
                              results_dir=args.results_dir)
    print(benchmark.summary().T.to_string())
    print(benchmark.latex_rows())
