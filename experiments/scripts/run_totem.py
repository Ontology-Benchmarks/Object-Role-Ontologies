"""One end-to-end TOTeM run: OCEL 2.0 SQLite import (TOTeM's vendored ocpa) + mine_totem().

Meant to run inside the totem-env image with the TOTeM checkout on sys.path; one run per process
so that ru_maxrss is the peak of this run only. mine_totem() reports its model by printing, so
its stdout goes to /dev/null rather than being timed against a terminal.
"""

import argparse
import contextlib
import json
import os
import resource
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--totem", required=True, help="TOTeM repository checkout")
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--tau", type=float, default=0.9)
    args = parser.parse_args()

    sys.path.insert(0, args.totem)
    from ocpa.objects.log.importer.ocel2.sqlite import factory as ocel_import_factory
    from algorithm import mine_totem

    start = time.perf_counter()
    ocel = ocel_import_factory.apply(args.sqlite)
    import_s = time.perf_counter() - start

    t = time.perf_counter()
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        mine_totem(ocel, tau=args.tau)
    mine_s = time.perf_counter() - t

    print(json.dumps({
        "tool": "TOTeM",
        "sqlite": args.sqlite,
        "tau": args.tau,
        "events": len(ocel.log.log),
        "process_executions": len(ocel.process_executions),
        "import_s": import_s,
        "mine_s": mine_s,
        "total_s": time.perf_counter() - start,
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }))


if __name__ == "__main__":
    main()
