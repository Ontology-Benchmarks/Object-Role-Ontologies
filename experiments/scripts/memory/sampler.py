"""Sample the memory of a Docker container's cgroup from the host until terminated.

Writes CSV rows: unix time, main pid, memory.current, anon, file, summed VmRSS of its processes.
anon is process memory (JVM heap and non-heap, DuckDB's native buffers); file is page cache
charged to the container (e.g. the .duckdb file), which `memory.peak` also counts.
The main pid changes when the container restarts, which splits the trace into benchmark runs.
"""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path


def container_id(name: str) -> str:
    return subprocess.run(["docker", "inspect", name, "--format", "{{.Id}}"],
                          capture_output=True, text=True).stdout.strip()


def read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("container")
    parser.add_argument("out", type=Path)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    cgroup = None
    with open(args.out, "w", buffering=1) as out:
        out.write("t,pid,current,anon,file,rss\n")
        while True:
            if cgroup is None or not (cgroup / "memory.current").exists():
                cid = container_id(args.container)
                cgroup = Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope") if cid else None
                if cgroup is None or not cgroup.exists():
                    cgroup = None
                    time.sleep(args.interval)
                    continue
            pids = read(cgroup / "cgroup.procs").split()
            stat = dict(line.split() for line in read(cgroup / "memory.stat").splitlines() if line)
            current = read(cgroup / "memory.current").strip()
            rss = 0
            for pid in pids:
                for line in read(Path(f"/proc/{pid}/status")).splitlines():
                    if line.startswith("VmRSS:"):
                        rss += int(line.split()[1]) * 1024
            if current:
                out.write(f"{time.time():.3f},{pids[0] if pids else ''},{current},"
                          f"{stat.get('anon', '')},{stat.get('file', '')},{rss}\n")
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
