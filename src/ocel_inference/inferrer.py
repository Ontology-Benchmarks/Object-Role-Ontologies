"""Public OCEL schema inferrer class orchestrating modular mixins."""

import os
import time
from pathlib import Path
from typing import Dict, Optional

import duckdb
import pandas as pd
from SPARQLWrapper import JSON as SPARQL_JSON
from SPARQLWrapper import SPARQLWrapper

from .data_pipeline import DataPipelineMixin
from .mapping_artifacts import MappingArtifactsMixin
from .role_ontology import OntologyRoleMixin
from .visualization import OCELSchemaVisualizer


class OCELSchemaInferrer(DataPipelineMixin, MappingArtifactsMixin, OntologyRoleMixin):
    """Infer OIG/ORO artifacts from OCEL 2.0 logs using DuckDB and Ontop."""

    def __init__(
        self,
        ocel_json_path: str,
        output_dir: Optional[str] = None,
        db_dir: Optional[str] = None,
        ontop_sparql_endpoint: str = "http://ontop:8080/sparql",
        ontop_mapping_dir: Optional[str] = None,
        sparql_max_attempts: int = 8,
        sparql_base_retry_seconds: float = 2.0,
        verbose: bool = False,
    ):
        self.input_path = Path(ocel_json_path)
        stem = self.input_path.stem

        # Results (graph HTML, ORO .ttl) go to output_dir; the DuckDB file goes to db_dir, which
        # must lie under the input folder that the Ontop container mounts. output_dir is created
        # when something is written to it.
        self.output_dir = Path(output_dir) if output_dir else Path("output") / stem
        self.db_dir = Path(db_dir) if db_dir else self.input_path.parent
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.sparql_endpoint = ontop_sparql_endpoint
        self.sparql_max_attempts = max(1, sparql_max_attempts)
        self.sparql_base_retry_seconds = max(0.1, sparql_base_retry_seconds)

        if ontop_mapping_dir is None:
            workspace_input_dir = Path.cwd() / "input"
            if workspace_input_dir.exists():
                resolved_mapping_dir = workspace_input_dir
            else:
                resolved_mapping_dir = self.input_path.parent
        else:
            resolved_mapping_dir = Path(ontop_mapping_dir)

        self.ontop_mapping_dir = resolved_mapping_dir
        self.ontop_mapping_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.db_dir / f"{stem}.duckdb"

        self.staging_dir = self.ontop_mapping_dir / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        self.obda_mapping_file = self.staging_dir / "ocel_mapping.obda"
        self.obda_template_file = self.staging_dir / "ocel_mapping_template.obda"
        self.properties_file = self.staging_dir / "ocel.properties"
        self.lenses_file = self.staging_dir / "ocel_lenses.json"

        self.verbose = verbose
        self.visualizer = OCELSchemaVisualizer(log_fn=self._log)

        self.object_types = []
        # (activity, object type) pairs and activities of the log; set when the DuckDB tables are built.
        self.role_pairs = []
        self.activities = []
        # Axioms inferred or checked on this log, written by export_ttl().
        self.axiom_records = []
        self.sparql_wrapper: Optional[SPARQLWrapper] = None
        self._custom_template = None

        # Populated by run_inference(): wall-clock seconds per pipeline stage.
        self.stage_timings: Dict[str, float] = {}
        self.event_count: Optional[int] = None
        self.oig_df: Optional[pd.DataFrame] = None

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _log_basic(self, message: str) -> None:
        print(message)

    def _cleanup_duckdb_files(self) -> None:
        import shutil

        if self.db_path.exists():
            try:
                os.remove(self.db_path)
                self._log(f"Removed existing database: {self.db_path}")
            except Exception as e:
                self._log(f"Warning: Could not remove {self.db_path}: {e}")

        wal_path = Path(str(self.db_path) + ".wal")
        if wal_path.exists():
            try:
                os.remove(wal_path)
                self._log(f"Removed WAL file: {wal_path}")
            except Exception as e:
                self._log(f"Warning: Could not remove {wal_path}: {e}")

        tmp_path = Path(str(self.db_path) + ".tmp")
        if tmp_path.exists():
            try:
                shutil.rmtree(tmp_path)
                self._log(f"Removed tmp directory: {tmp_path}")
            except Exception as e:
                self._log(f"Warning: Could not remove {tmp_path}: {e}")

    def run_inference(self, keep_db: bool = True, update_oro_artifacts: bool = True) -> pd.DataFrame:
        self.stage_timings = {}
        self.axiom_records = []
        self._lifecycle_results = {}
        start_time = time.perf_counter()
        self._log(f"--- Starting inference for {self.input_path.name} ---")

        self._log("Loading and transforming OCEL data...")
        events_df, e2o_df = self._load_and_transform()
        self.event_count = len(events_df)
        stage_start = time.perf_counter()
        self.stage_timings["load_transform_s"] = stage_start - start_time

        self._log("Initializing DuckDB and loading data...")
        self._cleanup_duckdb_files()

        con = duckdb.connect(database=str(self.db_path), read_only=False)
        try:
            self._init_db_tables(con, events_df, e2o_df)
            self._verify_duckdb_tables(con)
        finally:
            con.close()
        self.stage_timings["duckdb_load_s"] = time.perf_counter() - stage_start

        self._log("Running OIG inference...")
        stage_start = time.perf_counter()
        schema_df = self._calculate_cardinalities()
        self.stage_timings["oig_s"] = time.perf_counter() - stage_start
        self.oig_df = schema_df

        elapsed_seconds = time.perf_counter() - start_time
        self._log_basic(f"{self.input_path.name}: {len(events_df)} events, inference in {elapsed_seconds:.2f} s")

        self._init_sparql_wrapper()

        stage_start = time.perf_counter()
        if update_oro_artifacts:
            self._log("\nGenerating dynamic ORO construction mappings for Ontop...")
            self._generate_obda_mappings()
            self._generate_ontop_lenses_template()
        else:
            self._log("\nSkipping ORO construction artifact generation for this run.")
        self.stage_timings["mapping_generation_s"] = time.perf_counter() - stage_start

        return schema_df

    def serve(self, timeout_s: float = 180.0) -> "OCELSchemaInferrer":
        """Stage this log's mapping for Ontop and block until Ontop serves it.

        Ontop serves one log at a time. Call this to switch back to a log whose inference
        already ran (run_inference() leaves its DuckDB file in place). If the Docker socket is
        mounted (as in the dev container), Ontop is restarted; otherwise this relies on Ontop's
        dev-mode reload, which can stall after many switches (then restart Ontop by hand).
        """
        from .benchmark import (
            EVENT_COUNT_QUERY,
            BenchmarkConfig,
            DockerEngine,
            _sparql_scalar,
            _wait_for_dev_mode_reload,
            _wait_until_serving,
        )

        if self.event_count is None:
            raise RuntimeError("Run run_inference() first.")
        staged = [self.obda_mapping_file, self.properties_file, self.lenses_file]
        before = [f.read_bytes() if f.exists() else b"" for f in staged]
        self._generate_obda_mappings()
        self._generate_ontop_lenses_template()
        changed = before != [f.read_bytes() for f in staged]

        config = BenchmarkConfig(ontop_sparql_endpoint=self.sparql_endpoint,
                                 ready_timeout_s=timeout_s, stable_window_s=2.0)
        already_serving = not changed and (
            _sparql_scalar(self.sparql_endpoint, EVENT_COUNT_QUERY, timeout_s=10) == self.event_count
        )
        if not already_serving:
            docker = DockerEngine.connect(config.docker_socket)
            if docker is not None:
                docker.restart(config.ontop_container)
            elif changed:
                _wait_for_dev_mode_reload(self.sparql_endpoint, config)
            _wait_until_serving(self.sparql_endpoint, self.event_count, config)
        self._init_sparql_wrapper()
        self._log_basic(f"Ontop is serving {self.input_path.name}.")
        return self

    def translate_to_sql(self, sparql_query: str, timeout: int = 30) -> str:
        """The SQL Ontop runs for a SPARQL query (Ontop's /ontop/reformulate endpoint)."""
        import requests

        base_url = self.sparql_endpoint.rsplit("/sparql", 1)[0]
        response = requests.post(f"{base_url}/ontop/reformulate", data={"query": sparql_query}, timeout=timeout)
        response.raise_for_status()
        return response.text

    def query_sparql(self, sparql_query: str) -> pd.DataFrame:
        if self.sparql_wrapper is None:
            raise RuntimeError("SPARQL wrapper not initialized. Run run_inference() first.")

        max_attempts = self.sparql_max_attempts
        for attempt in range(1, max_attempts + 1):
            assert self.sparql_wrapper is not None
            self.sparql_wrapper.setQuery(sparql_query)
            try:
                results = self.sparql_wrapper.query().convert()
                if not isinstance(results, dict):
                    return pd.DataFrame()
                bindings = results.get("results", {}).get("bindings", [])
                if not bindings:
                    return pd.DataFrame()

                # Row-wise, so variables left unbound by OPTIONAL become NaN.
                columns = results.get("head", {}).get("vars") or None
                return pd.DataFrame(
                    [{var: value_obj.get("value") for var, value_obj in binding.items()} for binding in bindings],
                    columns=columns,
                )
            except Exception as e:
                is_transient_connection_issue = self._is_transient_connection_issue(e)

                if is_transient_connection_issue and attempt < max_attempts:
                    wait_seconds = self.sparql_base_retry_seconds * attempt
                    self._log_basic(
                        f"Transient SPARQL connectivity issue (attempt {attempt}/{max_attempts}): {e}. "
                        f"Retrying in {wait_seconds}s..."
                    )
                    time.sleep(wait_seconds)
                    self._init_sparql_wrapper()
                    continue

                if is_transient_connection_issue:
                    raise RuntimeError(
                        "SPARQL endpoint is still unavailable after retry attempts. "
                        f"Endpoint: {self.sparql_endpoint}. "
                        "If Ontop was recently restarted, wait a little longer and rerun. "
                        "Otherwise restart Ontop from the HOST terminal:\n"
                        "docker-compose -f .devcontainer/docker-compose.yml restart ontop\n"
                        "sleep 15"
                    ) from e

                self._log(f"Error executing SPARQL query: {e}")
                raise

        raise RuntimeError("SPARQL query failed after retry attempts.")

    @staticmethod
    def _is_transient_connection_issue(error: Exception) -> bool:
        error_text = str(error).lower()
        return any(
            token in error_text
            for token in [
                "connection refused",
                "failed to establish a new connection",
                "temporarily unavailable",
                "timed out",
                "timeout",
                "remote end closed",
                "connection reset",
            ]
        )

    def test_sparql_connection(self) -> bool:
        try:
            self._init_sparql_wrapper()
            test_query = "SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o } LIMIT 1"
            assert self.sparql_wrapper is not None
            self.sparql_wrapper.setQuery(test_query)
            self.sparql_wrapper.query().convert()
            self._log("Successfully connected to SPARQL endpoint")
            return True
        except Exception as e:
            self._log(f"Failed to connect to SPARQL endpoint: {e}")
            return False

    def regenerate_and_reload(self):
        self._log("\nRegenerating ORO construction mappings...")
        self._generate_obda_mappings()
        self._generate_ontop_lenses_template()

        self._log("\n" + "=" * 70)
        self._log("IMPORTANT: Restart Ontop to load the new ORO construction mappings")
        self._log("=" * 70)
        self._log("\nFrom your HOST terminal (not inside the container), run:")
        self._log("\n  docker-compose -f .devcontainer/docker-compose.yml restart ontop")
        self._log("  sleep 15")
        self._log("\nThen re-run your inference code.")
        self._log("=" * 70)

        self.sparql_wrapper = None

    def _init_sparql_wrapper(self):
        self.sparql_wrapper = SPARQLWrapper(self.sparql_endpoint)
        self.sparql_wrapper.setReturnFormat(SPARQL_JSON)
        self._log(f"Connected to SPARQL endpoint: {self.sparql_endpoint}")

    def visualize(self) -> Dict[str, Path]:
        """Write the OIG and ORO graphs as oig_graph.html and oro_graph.html in output_dir.

        Call before serve(): the ORO graph reads the DuckDB file, which Ontop locks while serving it.
        """
        if self.oig_df is None:
            raise RuntimeError("Run run_inference() first.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files = {"oig": self.output_dir / "oig_graph.html", "oro": self.output_dir / "oro_graph.html"}
        self.visualize_object_interaction_graph(self.oig_df, output_file=str(files["oig"]))
        self.visualize_object_role_ontology_graph(output_file=str(files["oro"]))
        return files

    def visualize_object_interaction_graph(self, schema_df: pd.DataFrame, output_file: str = "object_interaction_graph.html"):
        self.visualizer.visualize_object_interaction_graph(schema_df, output_file)

    def visualize_schema(self, schema_df: pd.DataFrame, output_file: str = "schema_graph.html"):
        self.visualizer.visualize_schema(schema_df, output_file)

    def visualize_object_role_ontology_graph(self, output_file: str = "object_role_ontology_graph.html"):
        stats_df = self._get_event_object_population_stats()
        self.visualizer.visualize_object_role_ontology_graph(stats_df, output_file)

    def visualize_event_object_schema(self, output_file: str = "event_object_schema_graph.html"):
        self.visualizer.visualize_event_object_schema(
            stats_df=self._get_event_object_population_stats(),
            output_file=output_file,
        )
