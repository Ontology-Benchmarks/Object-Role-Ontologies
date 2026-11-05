"""One run of the unchanged ORO benchmark (ocel_inference.benchmark.run_single) without Docker.

Ontop 5.5.0, the DuckDB JDBC driver and Java 11.0.30 are the binaries copied out of the
project's Ontop image (runtime/); Python packages match the dev container. The only substitution
is the Docker hook: `restart` starts Ontop as a host process with the compose file's settings,
and `peak_memory_mb` reports that process's peak RSS (VmHWM). The Python peak is ru_maxrss of
this process, as in the containerized runs.

Run from the work directory (ORO resolves ./input/staging from the cwd), one process per run.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ONTOP = HERE / "runtime" / "ontop" / "ontop"
JAVA_HOME = HERE / "runtime" / "openjdk"
PORT = 8081


class NativeOntop:
    """Stands in for benchmark.DockerEngine."""

    def __init__(self, log_path: Path):
        self.proc = None
        self.log_path = log_path

    def restart(self, container: str, stop_timeout_s: int = 10) -> None:
        self.stop()
        staging = Path.cwd() / "input" / "staging"
        props = (staging / "ocel.properties").read_text()
        url = next(l.split("=", 1)[1] for l in props.splitlines() if l.startswith("jdbc.url="))
        # The generated properties use the Ontop container's mount point.
        url = url.replace("/opt/ontop/input", str(Path.cwd() / "input"))
        env = {
            **os.environ,
            "JAVA_HOME": str(JAVA_HOME),
            "ONTOP_MAPPING_FILE": str(staging / "ocel_mapping.obda"),
            "ONTOP_PROPERTIES_FILE": str(staging / "ocel.properties"),
            "ONTOP_LENSES_FILE": str(staging / "ocel_lenses.json"),
            "ONTOP_LAZY_INIT": "true",
            "ONTOP_LOG_LEVEL": "all",
            "ONTOP_DEV_MODE": "true",
            "ONTOP_DB_URL": url,
        }
        log = open(self.log_path, "a")
        self.proc = subprocess.Popen([str(ONTOP), "endpoint", "--port", str(PORT)], env=env,
                                     stdout=log, stderr=subprocess.STDOUT, cwd=ONTOP.parent)

    def peak_memory_mb(self, container: str) -> float:
        status = Path(f"/proc/{self.proc.pid}/status").read_text()
        kb = next(int(l.split()[1]) for l in status.splitlines() if l.startswith("VmHWM:"))
        return kb / 1024

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-category", action="append", default=[],
                        help="Leave a role category out of the run (e.g. transformation_role).")
    args = parser.parse_args()

    from ocel_inference import benchmark as bm

    ontop = NativeOntop(args.out.parent / "ontop.log")
    bm.DockerEngine.connect = classmethod(lambda cls, socket_path: ontop)
    dataset = next(d for d in bm.paper_datasets("input/data/") if d.name == args.dataset)
    config = bm.BenchmarkConfig(ontop_sparql_endpoint=f"http://localhost:{PORT}/sparql",
                                ontop_container="native-ontop", skip_categories=tuple(args.skip_category))
    try:
        result = bm.run_single(dataset, config, args.run)
    finally:
        ontop.stop()
    result["skipped_categories"] = args.skip_category
    with open(args.out, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")
    r = result["run"]
    print(f"{args.dataset} run {args.run}: total={r['total_s']:.2f}s bottom_up={r['bottom_up_s']:.2f}s "
          f"top_down_mean={r['top_down_mean_s']*1000:.1f}ms py_peak={r['py_peak_mb']:.0f}MB "
          f"ontop_peak={r['ontop_peak_mb']:.0f}MB", flush=True)


if __name__ == "__main__":
    sys.exit(main())
