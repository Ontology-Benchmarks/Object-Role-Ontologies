"""Per-run peaks from an ontop_memory_trace.csv (one container start per benchmark run)."""

import csv
import sys
from pathlib import Path

MB = 1024 * 1024


def run_peaks(trace: Path) -> list:
    """Peaks (MB) per container start; the first start precedes the benchmark's first restart."""
    epochs, last_pid = [], None
    for row in csv.DictReader(open(trace)):
        if not row["pid"] or not row["anon"]:
            continue
        if row["pid"] != last_pid:
            epochs.append({"current": 0, "anon": 0, "file": 0, "rss": 0})
            last_pid = row["pid"]
        for k in epochs[-1]:
            epochs[-1][k] = max(epochs[-1][k], int(row[k]) / MB)
    return epochs[1:]


if __name__ == "__main__":
    for e in run_peaks(Path(sys.argv[1])):
        print({k: round(v) for k, v in e.items()})
