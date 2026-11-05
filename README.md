# ORO: Object-Role Ontology inference for OCEL 2.0

Code and experiments for *Mind the Semantic Process Gap*. The ORO framework turns an OCEL 2.0 event log into a
virtual knowledge graph (DuckDB + Ontop) whose classes and properties are the ORO's own
(`O_ot`, `Ev_a`, `R_a(ot)`, `playsRole`, `frames`, `nextRole`, `laterRole`), and uses SPARQL to:

- **infer** the axiom schemata a log satisfies (init, clos, ext, eres, trans_life, single, reuse), and
- **check** any schema instantiation (e.g. `init`, `clos`, `alt`, `budg`, `carr`) by returning its
  counterexamples, the answers to τ(C ⊓ ¬D) for the axiom C ⊑ D;
- **export** the core T-box and the bottom-up axioms as an OWL 2 ontology (`.ttl`); top-down checks are not exported.

Each schema in `src/ocel_inference/schemata.py` holds its description, free variables, DL axiom and
bottom-up query; `dl.py` translates DL axioms into SPARQL.

## Layout

| path | content |
| --- | --- |
| `src/ocel_inference/` | the ORO library |
| `oro_walkthrough.ipynb` | walkthrough: inference on the four paper logs, the Section 7 checks, OWL export |
| `input/data/` | the four OCEL 2.0 logs, and per log the DuckDB file that Ontop reads (generated) |
| `output/` | per log: the OIG and ORO graphs (`oig_graph.html`, `oro_graph.html`) and the ORO as OWL 2 (`oro.ttl`) |
| `input/staging/` | the mapping that Ontop currently serves (generated) |
| `experiments/` | performance evaluation and comparison with OC-DECLARE, TOTeM and OCPQ (see its README) |
| `.devcontainer/`, `jdbc/` | Docker setup: a Python dev container and an Ontop endpoint with the DuckDB driver |

## Getting started

Open the folder in VS Code and choose *Reopen in Container*. The container installs the
library (`pip install -e .`), and Ontop runs at `http://ontop:8080`. Then:

```python
from ocel_inference import load

bpi17 = load("bpi17")                       # infer, then wait until Ontop serves the log
bpi17.infer_and_display_role_categories()  # bottom-up, with each axiom in DL
bpi17.check("clos", "offer", activity="Sent (mail and online)_complete")  # top-down
bpi17.axiom("clos", otype="offer", activity="Sent (mail and online)_complete")
bpi17.export_ttl()                          # output/bpi17/oro.ttl (multiplicities=True adds the per-object/event bounds)
```

Ontop serves one log at a time. To switch back to a log loaded earlier, call `.serve()` on it.
