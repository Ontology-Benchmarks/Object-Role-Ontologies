#!/usr/bin/env bash
# Table 6 (revised Section 7, Q1-Q6): ORO and OCPQ, 4 fresh-process runs each, then the
# counterexample equivalence check. Run on the host with the dev containers up; ontop_dev must
# mount this repository at /workspace. Results go to results/section7/.
set -euo pipefail
cd "$(dirname "$0")/.."
docker exec -w /workspace ontop_dev sh -c 'python -m ocel_inference.benchmark --runs 4 \
  --datasets "Container Logistics" "BPI 2017" \
  --results-dir experiments/results/section7/oro \
  --counterexample-dir experiments/results/section7/oro/counterexamples'
python3 -I scripts/run_baselines.py --runs 4 --tools ocpq --scenario-set s7
python3 -I scripts/compare_counterexamples.py --oro results/section7/oro/counterexamples \
  --ocpq results/section7/ocpq_counterexamples --queries Q1 Q2 Q3 Q4 Q5 Q5e Q6 \
  --out results/section7/equivalence.json
