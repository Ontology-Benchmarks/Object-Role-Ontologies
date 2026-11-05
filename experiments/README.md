# Experiments

Performance evaluation of ORO and comparison with OC-DECLARE, TOTeM (bottom-up) and OCPQ
(top-down), on the four paper logs. Every number is the mean ± sample s.d. of 4 runs, each in a
fresh process (AMD Ryzen 7 255, 32 GB, CachyOS). All LaTeX rows are in **`results/tables.tex`**.

## Layout

| path | content |
| --- | --- |
| `baselines/` | the three tools at fixed commits: OCPQ `186f599`, oc-DECLARE `6232d2e`, TOTeM `9fbfaa9`; `harness/` (Rust runner for OC-DECLARE and OCPQ) and `totem-env/` (TOTeM's Python 3.9 Docker image) |
| `queries/` | OCPQ query trees for Q1–Q6 (`ocpq/`), and SQL deriving ORO's original Q1–Q4 counterexamples (`oro_ground_truth.sql`) |
| `scripts/` | runners, equivalence check, LaTeX tables; `memory/` holds the memory study |
| `results/` | one folder per experiment, below |

| results folder | paper | content |
| --- | --- | --- |
| `oro_benchmark/` | Table 4; ORO in Tables 5 and 6 (Q1–Q3) | published ORO benchmark (Docker) |
| `baselines/` | Tables 5 and 6 (Q1–Q3) | OC-DECLARE, TOTeM, OCPQ runs; discovered models; counterexamples of both tools |
| `section7/` | Table 6 (Q1–Q6) | ORO (`oro/`) and OCPQ on the revised Section 7 scenarios |
| `native/` | Tables 4 and 5, extra rows | ORO without Docker, with and without `transformation_role` |
| `memory/` | — | ORO under DuckDB memory limits |

In `baselines/` the queries use the original numbering: Q4 there is `clos(Offer, Returned)`,
which the revision dropped (`queries/ocpq/Q4old`).

## Reproducing the benchmarks

### Requirements

- **Docker and the dev container**, set up as in the [main README](../README.md#prerequisites),
  including the DuckDB JDBC jar in `jdbc/`.
- **Linux 5.19 or newer with cgroup v2 on the Docker host**, for Ontop's peak memory. Check with

  ```bash
  docker exec ontop_service cat /sys/fs/cgroup/memory.peak   # prints a number of bytes
  ```

  If it prints an error instead (e.g. WSL2 or Docker Desktop with an older kernel, or
  Ubuntu 22.04's 5.15), the timings still reproduce, but `ontop_peak_mb` is NaN.
- **Container names unchanged.** The benchmark restarts Ontop before each run through the Docker
  socket, by the name `ontop_service` (`BenchmarkConfig.ontop_container`).
- **For the baselines only:** Rust (`cargo`), the `duckdb` CLI and Python 3 on the host, and the
  submodules (`git clone --recursive`, or `git submodule update --init`).

Absolute times depend on the hardware (ours is above). Compare the relative results across logs
and tools rather than the seconds.

### ORO benchmark only (Table 4)

In a terminal inside the dev container (e.g. VS Code's terminal after *Reopen in Container*):

```bash
python -m ocel_inference.benchmark --runs 4 --results-dir experiments/results/oro_benchmark_rerun/
```

or set `RUN_BENCHMARK = True` in the last cell of `oro_walkthrough.ipynb`. Use a new results
folder, or clear the old raw files first (see below).

From the host, wrap the command in a shell. Under a bare `docker exec`, Python's parent process
has PID 0, and pm4py fails on import with `psutil.NoSuchProcess: process PID not found (pid=0)`:

```bash
docker exec -w /workspace ontop_dev sh -c "python -m ocel_inference.benchmark --runs 4 --results-dir experiments/results/oro_benchmark_rerun/"
```

### All experiments

From this folder, on the host (the ORO steps run inside the dev container):

```bash
(cd baselines/harness && cargo build --release)
docker build -t totem-env:py3.9.13 baselines/totem-env
python3 -I scripts/make_ocpq_queries.py --data ../input/data --out queries/ocpq
for f in order-management ContainerLogistics ocel2-p2p bpi17_ocel; do
  python3 -I scripts/ocel_json_to_sqlite.py --no-event-attributes ../input/data/$f.json baselines/totem-env/sqlite/$f.sqlite
done

# ORO benchmark (Table 4), in the dev container
docker exec -w /workspace ontop_dev sh -c "python -m ocel_inference.benchmark --runs 4 --results-dir experiments/results/oro_benchmark_rerun/"

# Tables 5 and 6: baselines, then the counterexample check against ORO
python3 -I scripts/run_baselines.py --runs 4
(cd .. && duckdb -c ".read experiments/queries/oro_ground_truth.sql")
python3 -I scripts/compare_counterexamples.py --oro results/baselines/oro_counterexamples \
  --ocpq results/baselines/ocpq_counterexamples --out results/baselines/equivalence.json

# Table 6, revised Section 7 (ORO and OCPQ, then the equivalence check)
scripts/run_section7.sh

python3 -I scripts/latex_tables.py   # writes results/tables.tex
```

The runners append to `results/*/raw/*.jsonl`. Clear these files before running again to avoid errors.

## Method

- **ORO**: `ocel_inference.benchmark`. Preprocessing (load, DuckDB, OIG, mapping) plus bottom-up
  (all role-category queries). Ontop is restarted before each run. Memory is the Python/DuckDB
  peak plus the Ontop container's peak.
  The published ORO results used hand-written queries over the OCEL vocabulary (their text is
  kept as `results/section7/oro/counterexamples/*.rq`). The library now queries the ORO
  vocabulary, and generates each counterexample query from the schema's DL axiom; a rerun
  measures these queries. On all four logs they return the same sets: every Section 7
  counterexample set, every per-role init/clos/single check, and every bottom-up category.
- **OC-DECLARE**: OCEL 2.0 JSON import, linking and `discover_behavior_constraints` with library
  defaults (noise 0.2, O2O mode `None`), as the OCPQ UI calls it. It uses the `process_mining`
  revision pinned by OCPQ (rust4pm `84570b8`), where oc-DECLARE's code now lives.
- **TOTeM**: OCEL 2.0 SQLite import with TOTeM's vendored `ocpa`, then `mine_totem(tau=0.9)`,
  single-threaded in Docker. `ocpa` reads only SQLite or XML, so the JSON logs are converted
  (untimed). TOTeM's code forces three deviations:
  - timestamps are truncated to whole seconds;
  - event attributes are omitted;
  - dangling E2O/O2O references are dropped (89 in Logistics, 2028 in P2P).
- **OCPQ**: evaluation time on an already loaded log (`BindingBoxTree::evaluate` plus grouping),
  with 1 untimed warm-up per process. Each query is a constraint on the root box, and its
  violated situations are the counterexamples. OCPQ's time bounds are inclusive at µs
  resolution, so a 1 µs minimum gives ORO's strict ordering. All queries return exactly ORO's
  counterexample sets (`results/*/equivalence.json`).

| ID | schema | OCPQ tree |
| --- | --- | --- |
| Q1 | init(Forklift, Weigh) | root (forklift, Weigh e); child: any earlier event; 0 children |
| Q2 | alt(Offer, {Accepted, Cancelled, Refused}) | root offer; children: its events of the three activities; exactly 1 |
| Q3 | clos(Offer, Sent) | root (offer, Sent e); child: any later event; 0 children |
| Q4 | budg(Transport Document, {Reschedule Container, Depart}, 2) | root document; children: its events of the two activities; at most 2 |
| Q5 | carr_next(Offer, Sent, Returned) | root (offer, Returned e); `ANY` over Sent events s before e with no event strictly between s and e |
| Q5′ | carr(Offer, Sent, Returned) | root (offer, Returned e); child: an earlier Sent event; at least 1 |
| Q6 | carr→(Offer, Sent, Returned) | root (offer, Sent s); child: a later Returned event; at least 1 |

"Sent" covers both send channels. With only *Sent (mail and online)*, Q5 has 54 violations,
and both tools return the same 54.

## Memory measures (`scripts/memory/`, `results/memory/`, `results/native/`)

Ontop's JVM heap is capped at 512 MB. Most of ORO's memory is DuckDB's native memory inside the
Ontop process:

- **DuckDB limits** (`run_conditions.py`): `memory_limit` of 1 GB or less runs out of memory in
  `transformation_role`, and 2 GB hung.
- **Without Docker** (`run_native.py`, using Ontop 5.5.0 and Java 11 from the image; `venv/` and
  `runtime/` are local and not tracked): the same memory, and 7–13% slower.
- **Without `transformation_role`** (`run_native.py --skip-category transformation_role`):
  bottom-up drops to about 0.9 s, and Ontop's memory falls for Container Logistics and P2P.
