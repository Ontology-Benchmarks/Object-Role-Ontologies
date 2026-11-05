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
| `oro_walkthrough.ipynb` | walkthrough: inference and top-down checks on the four benchmark OCEL logs covered in the paper.  |
| `input/data/` | the four OCEL 2.0 logs covered in the paper, and per log the DuckDB file that Ontop reads (generated when run) |
| `output/` | for each log, the optional visualization graphs (`oig_graph.html`, `oro_graph.html`) and the inferred ORO as an OWL 2 ontology (`oro.ttl`) |
| `input/staging/` | the mapping that Ontop currently serves. Since ontop can only load one log at a time, the library stages the currently loaded mapping files here. These files are changed as new logs are loaded and served. |
| `experiments/` | performance evaluation and comparison with OC-DECLARE, TOTeM and OCPQ (see its README) |
| `.devcontainer/`, `jdbc/` | Docker setup: a Python dev container and an Ontop endpoint with the DuckDB driver (not included, see [Prerequisites](#prerequisites)) |

## Prerequisites

**Docker and VS Code Dev Containers.** The library runs inside the dev container defined in
`.devcontainer/`. You need Docker and VS Code with the Dev Containers extension. If you have not
used dev containers before, follow the
[Dev Containers documentation](https://code.visualstudio.com/docs/devcontainers/containers)
(installation) and its [tutorial](https://code.visualstudio.com/docs/devcontainers/tutorial).

**Large files are not in the repository.** You need to provide them yourself before building the
container.

- **DuckDB JDBC driver (required).** Ontop connects to DuckDB through this driver, and the Ontop
  image will not build without it. Download
  [`duckdb_jdbc-1.4.2.0.jar`](https://repo1.maven.org/maven2/org/duckdb/duckdb_jdbc/1.4.2.0/duckdb_jdbc-1.4.2.0.jar)
  and put it in `jdbc/` with that exact file name (`.devcontainer/ontop.Dockerfile` copies
  `jdbc/duckdb_jdbc-1.4.2.0.jar`):

  ```bash
  curl -L -o jdbc/duckdb_jdbc-1.4.2.0.jar \
    https://repo1.maven.org/maven2/org/duckdb/duckdb_jdbc/1.4.2.0/duckdb_jdbc-1.4.2.0.jar
  ```

- **Datasets.** Logs are OCEL 2.0 JSON files. The four logs of the paper are included in
  `input/data/`; any other log you bring yourself and load by its path, e.g.
  `load("input/data/my_log.json")`.

- **Baselines (experiments only).** OCPQ, oc-DECLARE and TOTeM are git submodules. Clone with
  `git clone --recursive`, or run `git submodule update --init` in an existing clone. The ORO
  library itself does not need them.

## Getting started

The library is intended to be used inside a docker container, to effectively manage dependencies and ensure consistent usage across operating systems. The container setup is already handled by this repository. After cloning the repo, open the folder in VS Code and choose *Reopen in Container*. The container installs the necessary python libraries as well as the ontop instance. Then you can simply load a JSON OCEL 2.0 log, infer its role, perform top-down counterexample checks, add axioms, and export the ORO to an OWL ontology file:

```python
from ocel_inference import load

bpi17 = load("input/data/bpi17_ocel.json")  # infer, then wait until Ontop serves the log
bpi17.infer_and_display_role_categories()  # bottom-up, with each axiom in DL
bpi17.check("clos", "offer", activity="Sent (mail and online)_complete")  # top-down
bpi17.axiom("clos", otype="offer", activity="Sent (mail and online)_complete")
bpi17.export_ttl()                          # output/bpi17/oro.ttl (multiplicities=True adds the per-object/event bounds)
```

Ontop serves one log at a time. If you need to switch between multiple distinct logs while using the library, use the `.serve()` method.
