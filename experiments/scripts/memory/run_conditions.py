"""Re-run the ORO benchmark (ocel_inference.benchmark, unchanged) under memory configurations.

For every (condition, dataset): recreate ontop_service with the condition's ONTOP_DB_URL /
ONTOP_JAVA_ARGS through a compose override, sample its cgroup from the host, run the benchmark
in the dev container for that dataset, and copy its results to results/memory/. The compose
file and project are read from the running ontop_service's labels and not modified;
`--restore` recreates the service without overrides.
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / "results" / "memory"


def compose_label(key: str) -> str:
    """A docker compose label of the running ontop_service (its project name or compose file)."""
    return subprocess.run(["docker", "inspect", "-f", f'{{{{index .Config.Labels "{key}"}}}}', "ontop_service"],
                          check=True, capture_output=True, text=True).stdout.strip()

DATASETS = {
    "Order Management": "order_mgmt/order-management.duckdb",
    "Container Logistics": "logistics/ContainerLogistics.duckdb",
    "Procure-to-Pay": "p2p/ocel2-p2p.duckdb",
    "BPI 2017": "bpi17/bpi17_ocel.duckdb",
}
# name -> (DuckDB options appended to the JDBC URL, JVM args); None keeps the compose default.
CONDITIONS = {
    "default": (None, None),
    "duckdb1g": ("memory_limit=1GB", None),
    "duckdb512m": ("memory_limit=512MB", None),
    "duckdb256m": ("memory_limit=256MB", None),
    "duckdb256m_xmx256m": ("memory_limit=256MB", "-Xmx256m"),
    # Second round, after every limit up to 1 GB ran out of memory in transformation_role.
    "duckdb2g": ("memory_limit=2GB", None),
    "duckdb1g_noorder": ("memory_limit=1GB;preserve_insertion_order=false", None),
    "duckdb1g_noorder_t4": ("memory_limit=1GB;preserve_insertion_order=false;threads=4", None),
}


def recreate_ontop(override: Path):
    project = compose_label("com.docker.compose.project")
    compose = compose_label("com.docker.compose.project.config_files").split(",")[0]
    subprocess.run(["docker", "compose", "-p", project, "-f", compose, "-f", str(override), "up", "-d",
                    "--no-build", "--no-deps", "--force-recreate", "ontop"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_override(path: Path, db_url, java_args):
    env = {}
    if db_url:
        env["ONTOP_DB_URL"] = db_url
    if java_args:
        env["ONTOP_JAVA_ARGS"] = java_args
    lines = ["services:", "  ontop:"]
    if env:
        lines.append("    environment:")
        lines += [f'      {k}: "{v}"' for k, v in env.items()]
    else:
        lines[-1] += " {}"
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conditions", nargs="*", default=list(CONDITIONS))
    parser.add_argument("--datasets", nargs="*", default=list(DATASETS))
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    if args.restore:
        override = HERE / "overrides" / "none.yml"
        write_override(override, None, None)
        recreate_ontop(override)
        print("ontop_service recreated with the compose defaults")
        return

    for cond in args.conditions:
        options, java_args = CONDITIONS[cond]
        for ds in args.datasets:
            slug = DATASETS[ds].split("/")[0]
            out = RESULTS / cond / slug
            out.mkdir(parents=True, exist_ok=True)
            db_url = f"jdbc:duckdb:/opt/ontop/input/data/{DATASETS[ds]};{options}" if options else None
            override = HERE / "overrides" / f"{cond}-{slug}.yml"
            write_override(override, db_url, java_args)
            recreate_ontop(override)
            sampler = subprocess.Popen([sys.executable, "-I", str(HERE / "sampler.py"), "ontop_service",
                                        str(out / "ontop_memory_trace.csv")])
            try:
                with open(out / "benchmark.log", "w") as log:
                    proc = subprocess.run(
                        # Through sh: pm4py inspects its parent process at import, which is
                        # pid 0 (not found) for a bare `docker exec`.
                        ["docker", "exec", "-w", "/workspace", "ontop_dev", "sh", "-c",
                         'python -m ocel_inference.benchmark --runs "$0" --datasets "$1" '
                         '--results-dir "$2"', str(args.runs), ds, f"/tmp/oro_memory/{cond}/{slug}/"],
                        stdout=log, stderr=subprocess.STDOUT)
            finally:
                sampler.terminate()
                sampler.wait()
            subprocess.run(["docker", "cp", f"ontop_dev:/tmp/oro_memory/{cond}/{slug}/.", str(out)],
                           stdout=subprocess.DEVNULL)
            status = "ok" if proc.returncode == 0 else f"FAILED ({proc.returncode})"
            print(f"{cond:<20} {ds:<20} {status}", flush=True)


if __name__ == "__main__":
    main()
