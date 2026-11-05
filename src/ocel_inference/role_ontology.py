"""Bottom-up inference, top-down checks and OWL export of ORO axiom schemata (schemata.py)."""

import re
import time
from pathlib import Path
from typing import Dict, List, NoReturn, Optional, Protocol, Sequence, Union, cast
from urllib.parse import unquote

import pandas as pd

from .export import AxiomRecord, write_ttl
from .schemata import (
    CLOS_QUERY,
    EX,
    INIT_QUERY,
    MULTIPLICITIES,
    OCEL,
    ORO,
    PREFIXES,
    AxiomSchema,
    activity_from_iri,
    get_default_role_categories,
    get_schema,
    otype_from_iri,
)

ROLE_PAIRS_QUERY = """
PREFIX ex: <http://www.example.com/>
SELECT DISTINCT ?activity ?otype
WHERE { ?role a ex:EventObjectRole ; ex:playsRoleIn ?activity ; ex:isRoleOf ?otype . }
""".strip()

# Keyword arguments of check() and the free variables (paper notation) they bind.
_ARGUMENT_NAMES = {"otype": "ot", "activity": "a", "activities": "A", "budget": "N",
                   "sources": "a1", "targets": "a2"}
_OTYPE_VARIABLES = ("ot", "ot_in", "ot_out")
_ACTIVITY_VARIABLES = ("a", "A", "a1", "a2")


class _OntologyRoleHost(Protocol):
    sparql_wrapper: object
    role_pairs: list
    activities: list
    axiom_records: list
    input_path: Path
    output_dir: Path

    def query_sparql(self, sparql_query: str) -> pd.DataFrame:
        ...

    def _log_basic(self, message: str) -> None:
        ...


class OntologyRoleMixin:
    # ------------------------------------------------------------------
    # Bottom-up
    # ------------------------------------------------------------------

    def get_default_ontological_role_categories(self) -> List[AxiomSchema]:
        """The bottom-up schemata (init, clos, ext, eres, trans_life, single, reuse)."""
        return get_default_role_categories()

    def categorize_roles_by_ontology(self, categories: Optional[List[AxiomSchema]] = None) -> pd.DataFrame:
        """Run each bottom-up schema's query: one row per satisfied instantiation.

        Columns: category (schema name), schema (instantiation, e.g. ``init(forklift, Weigh)``),
        dl_axiom, category_description, and the query's own columns (activity, otype, ...).
        Each instantiation is also recorded for export_ttl().
        """
        host = self._require_endpoint()
        frames: List[pd.DataFrame] = []
        for schema in categories or self.get_default_ontological_role_categories():
            if schema.query is None:
                raise ValueError(f"{schema.label} is a top-down schema; use check().")
            start = time.perf_counter()
            try:
                inferred = host.query_sparql(schema.query)
            except Exception as error:
                self._raise_ontology_query_error(error)
            self._log(f"[ontology-role] {schema.name} query took {time.perf_counter() - start:.4f} seconds")
            if schema.label in ("init", "clos"):
                self._lifecycle_cache()[schema.label] = inferred
            if inferred.empty:
                continue
            labels, dls, short = [], [], []
            for _, row in inferred.iterrows():
                record = self._record(schema, self._row_bindings(schema, row), "bottom-up", holds=True)
                labels.append(record.label)
                dls.append(record.dl)
                short.append(schema.display_dl(**record.bindings))
            inferred.insert(0, "category", schema.name)
            inferred.insert(1, "schema", labels)
            inferred.insert(2, "dl_axiom", dls)
            inferred.insert(3, "display_axiom", short)
            inferred.insert(4, "category_description", schema.description)
            frames.append(inferred)
        if not frames:
            return pd.DataFrame(columns=["category", "schema", "dl_axiom", "activity", "otype"])
        return pd.concat(frames, ignore_index=True)

    def infer_and_display_role_categories(
        self,
        categories: Optional[List[AxiomSchema]] = None,
        export_oro: bool = False,
        descriptions: bool = False,
        filter_redundant_categories: bool = True,
    ) -> pd.DataFrame:
        """Infer the bottom-up schemata, display one row per inferred axiom, and return the table.

        With ``export_oro`` the ORO is also written as OWL 2 Turtle (export_ttl(), by default to
        ``output/<log>/oro.ttl``). ``descriptions`` adds each schema's description.
        """
        role_categories_df = self.categorize_roles_by_ontology(categories=categories)
        table = self.display_role_category_table(role_categories_df, descriptions=descriptions,
                                                 filter_redundant_categories=filter_redundant_categories)
        if export_oro:
            self.export_ttl()
        return table

    def instantiate_role_pattern(self, category_name: str, bindings: Dict[str, str],
                                 categories: Optional[List[AxiomSchema]] = None) -> pd.DataFrame:
        """Rows of a bottom-up schema matching ``bindings`` (columns of its query, e.g. activity)."""
        schema = get_schema(category_name)
        inferred = self.categorize_roles_by_ontology(categories=[schema])
        for column, expected in bindings.items():
            if column not in inferred.columns:
                raise ValueError(f"Column '{column}' is not in the {schema.label} output: {', '.join(inferred.columns)}")
            inferred = inferred[inferred[column].map(self._compact_iri).astype(str).str.lower()
                                == str(self._compact_iri(expected)).lower()]
        return inferred.reset_index(drop=True)

    def pattern_instance_holds(self, category_name: str, bindings: Dict[str, str],
                               categories: Optional[List[AxiomSchema]] = None) -> bool:
        return not self.instantiate_role_pattern(category_name, bindings, categories).empty

    def print_avg_ontology_role_query_times(self, categories: Optional[List[AxiomSchema]] = None,
                                            runs_per_query: int = 1) -> pd.DataFrame:
        """Run each bottom-up query ``runs_per_query`` times and print the mean runtime."""
        host = self._require_endpoint()
        if runs_per_query < 1:
            raise ValueError("runs_per_query must be >= 1")
        rows = []
        for schema in categories or self.get_default_ontological_role_categories():
            durations = []
            for _ in range(runs_per_query):
                start = time.perf_counter()
                host.query_sparql(schema.query)
                durations.append(time.perf_counter() - start)
            mean = sum(durations) / len(durations)
            host._log_basic(f"  - {schema.name}: avg={mean:.4f}s (min={min(durations):.4f}s, max={max(durations):.4f}s)")
            rows.append({"category": schema.name, "runs": runs_per_query, "avg_seconds": mean,
                         "min_seconds": min(durations), "max_seconds": max(durations)})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Top-down
    # ------------------------------------------------------------------

    def check(self, schema: str, otype: Optional[str] = None, activity: Optional[str] = None,
              activities: Optional[Sequence[str]] = None, sources=None, targets=None,
              budget: Optional[int] = None, limit: Optional[int] = None, compact: bool = True,
              **variables) -> pd.DataFrame:
        """Counterexamples of an instantiated schema; empty means the axiom holds in the log.

        ``schema`` is the paper's name (init, clos, ext, eres, single, alt, budg, excl, shar,
        carr, carr_next, carr_fwd / carr→, trans_life, oto, split, merge, gtrans, trans_nec).
        Free variables can be given by their paper names (ot, a, A, N, a1, a2, ot_in, ot_out)
        or by the keywords otype, activity, activities, budget, sources (a1), targets (a2):

            check("init", "forklift", activity="Weigh")
            check("budg", "Transport Document", activities=["Reschedule Container", "Depart"], budget=2)
            check("carr", "offer", sources=[...Sent...], targets="Returned_complete")
            check("oto", ot_in="customer_order", ot_out="transport_document", a="Create Transport Document")

        Labels are matched to the log's names case- and punctuation-insensitively; a full IRI also
        works. ext and eres use the bottom-up results for R_init/R_close and the activities of ot,
        unless A_init/A_close or A are given. The DL axiom and outcome are recorded for export_ttl().
        Result columns depend on what the counterexamples are: roles (obj, event, activity,
        timestamp), objects (obj) or events (event, activity, timestamp).
        """
        spec = get_schema(schema)
        bindings = self._bindings(spec, otype=otype, activity=activity, activities=activities,
                                  sources=sources, targets=targets, budget=budget, **variables)
        result = self.evaluate(spec, bindings, limit)
        complete = limit is None or len(result) < limit
        self._record(spec, bindings, "top-down", holds=result.empty,
                     counterexamples=len(result) if complete else None)
        return self.compact_iris(result) if compact else result

    def evaluate(self, spec: AxiomSchema, bindings: Dict, limit: Optional[int] = None) -> pd.DataFrame:
        """Counterexamples of an instantiation with resolved bindings (no recording).

        Subsumptions: the answers of the generated counterexample query. NonEmpty axioms
        (reuse, eres): the concepts without a witness, one row each (columns concept, dl_axiom).
        """
        host = self._require_endpoint()
        if spec.subject == "concept":
            from .dl import dl
            empty = [ax for ax, query in spec.witness_queries(**bindings) if host.query_sparql(query).empty]
            rows = [{"concept": ax.name.label if ax.name else dl(ax.concept), "dl_axiom": dl(ax)} for ax in empty]
            return pd.DataFrame(rows[:limit] if limit else rows, columns=["concept", "dl_axiom"])
        query = spec.counterexample_query(**bindings)
        if query is None:  # no individual can violate it (e.g. ext without init/closure roles)
            return pd.DataFrame(columns=["event"] if spec.subject == "event" else ["obj"])
        return host.query_sparql(self._with_limit(query, limit))

    def counterexample_query(self, schema: str, **variables) -> Optional[str]:
        """The SPARQL that check() runs for the same arguments, without running it."""
        spec = get_schema(schema)
        return spec.counterexample_query(**self._bindings(spec, **variables))

    def axiom(self, schema: str, **variables) -> str:
        """DL axiom(s) of an instantiation, e.g. axiom("init", otype="forklift", activity="Weigh")."""
        spec = get_schema(schema)
        return spec.dl(**self._bindings(spec, **variables))

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def recorded_axioms(self, mode: Optional[str] = None) -> pd.DataFrame:
        """The instantiations inferred or checked so far (``mode``: "bottom-up" or "top-down")."""
        host = cast(_OntologyRoleHost, self)
        return pd.DataFrame(
            [{"schema": r.label, "dl_axiom": r.dl, "mode": r.mode, "holds": r.holds,
              "counterexamples": r.counterexamples}
             for r in host.axiom_records if mode in (None, r.mode)],
            columns=["schema", "dl_axiom", "mode", "holds", "counterexamples"])

    def export_ttl(self, path: Optional[Union[str, Path]] = None, infer: bool = True,
                   multiplicities: bool = False) -> Path:
        """Write the ORO of this log as OWL 2 in Turtle and return the file path.

        Contents: the core T-box (Section 4) for the log's object types, activities and role
        concepts; the bottom-up axioms (inferred now if ``infer`` and not inferred yet); the
        tightest per-object and per-event multiplicity axioms if ``multiplicities``. These are off by
        default: their bounds are observed maxima (e.g. ≤994 playsRole.R_a(ot)), and tableau
        reasoners such as HermiT run out of memory on them. Top-down checks
        (check()) are not exported; recorded_axioms("top-down") lists them.
        Default path: ``<output_dir>/oro.ttl`` (``output/<log>/oro.ttl`` for load()).
        """
        host = self._require_endpoint()
        inferred_names = {r.schema.name for r in host.axiom_records if r.mode == "bottom-up"}
        if infer and not inferred_names & {s.name for s in self.get_default_ontological_role_categories()}:
            self.categorize_roles_by_ontology()
        if multiplicities:
            for schema in MULTIPLICITIES:
                for _, row in host.query_sparql(schema.query).iterrows():
                    self._record(schema, self._row_bindings(schema, row), "bottom-up", holds=True)
        role_pairs, activities = self._catalog()
        target = Path(path) if path else Path(host.output_dir) / "oro.ttl"
        records = [r for r in host.axiom_records if r.mode == "bottom-up"
                   and (multiplicities or r.schema not in MULTIPLICITIES)]
        written = write_ttl(target, host.input_path.stem, role_pairs, activities, records)
        host._log_basic(f"ORO written to {written} ({len(records)} schema instantiations)")
        return written

    # ------------------------------------------------------------------
    # Timelines and display
    # ------------------------------------------------------------------

    def object_timeline(self, obj: str, related_types: Sequence[str] = ()) -> pd.DataFrame:
        """Events of one object, time-ordered, with compact labels.

        ``related_types`` (substrings of object types, e.g. ("application", "workflow")) adds the
        events of objects of those types that co-occur with ``obj`` in some event.
        """
        host = self._require_endpoint()
        obj_iri = self._coerce_prefixed_or_iri(obj, default_prefix="ex")
        related = ""
        if related_types:
            pattern = "|".join(t.lower() for t in related_types)
            related = f"""
      UNION
      {{
        ?sharedEvent ocel:involves {obj_iri} , ?object .
        FILTER(?object != {obj_iri})
        FILTER(REGEX(LCASE(STR(?object)), "{pattern}"))
      }}"""
        query = f"""
{PREFIXES}
SELECT DISTINCT ?timestamp ?event ?activity ?object
WHERE {{
      {{ BIND({obj_iri} AS ?object) }}{related}
      ?event ocel:involves ?object ;
             ocel:hasActivity ?activity ;
             ocel:timestamp ?timestamp .
}}
ORDER BY ?timestamp ?event ?object
""".strip()
        return self.compact_iris(host.query_sparql(query))

    @classmethod
    def compact_iris(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Copy of ``df`` with ORO/OCEL IRIs shortened to their labels (missing values become "")."""
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
                out[col] = out[col].map(lambda v: "" if cls._compact_iri(v) is None else cls._compact_iri(v))
        return out

    def prepare_role_category_display_table(self, role_categories_df: pd.DataFrame, descriptions: bool = False,
                                            filter_redundant_categories: bool = True) -> pd.DataFrame:
        """One row per inferred axiom: object type, activity, schema, DL axiom (and description).

        Rows are sorted by object type, then schema (in the paper's order), then activity.
        """
        columns = ["object type", "activity", "schema", "axiom"] + (["description"] if descriptions else [])
        if role_categories_df.empty:
            return pd.DataFrame(columns=columns)
        df = role_categories_df
        if filter_redundant_categories:
            df = self.filter_redundant_role_categories(df)
        text = lambda col: (df[col].map(self._compact_iri).fillna("").astype(str)
                            if col in df.columns else pd.Series("", index=df.index))
        object_type = text("otype")
        paired = text("pairedOType")
        object_type = object_type.where(paired == "", object_type + " → " + paired)
        order = {s.name: i for i, s in enumerate(self.get_default_ontological_role_categories())}
        table = pd.DataFrame({
            "object type": object_type,
            "activity": text("activity"),
            "schema": df["category"].map(lambda name: get_schema(name).label),
            "axiom": df["display_axiom"] if "display_axiom" in df.columns else df["dl_axiom"],
            "description": df["category_description"],
            "_order": df["category"].map(order),
        })
        table = table.sort_values(["object type", "_order", "activity"])
        return table[columns].reset_index(drop=True)

    def display_role_category_table(self, role_categories_df: pd.DataFrame, descriptions: bool = False,
                                    filter_redundant_categories: bool = True) -> pd.DataFrame:
        """Display the inferred axioms as a plain table (no index) and return it."""
        table = self.prepare_role_category_display_table(role_categories_df, descriptions,
                                                         filter_redundant_categories)
        if table.empty:
            print("No axioms were inferred.")
            return table
        print(f"{len(table)} inferred axioms")
        try:
            from IPython.display import display
            display(table.style.hide(axis="index").set_properties(**{"text-align": "left"})
                    .set_table_styles([{"selector": "th", "props": [("text-align", "left")]}]))
        except Exception:
            print(table.to_string(index=False))
        return table

    def filter_redundant_role_categories(self, role_categories_df: pd.DataFrame,
                                         redundant_category: str = "single_use_role",
                                         dominant_categories: Optional[List[str]] = None) -> pd.DataFrame:
        """Drop single-use rows of roles that are init or closure roles (which imply single use)."""
        if (role_categories_df.empty or "category" not in role_categories_df.columns
                or "role" not in role_categories_df.columns):
            return role_categories_df.copy()
        dominant = set(dominant_categories or ["creation_role", "closure_role"])
        dominant_roles = set(role_categories_df.loc[role_categories_df["category"].isin(dominant), "role"].astype(str))
        redundant = ((role_categories_df["category"] == redundant_category)
                     & role_categories_df["role"].astype(str).isin(dominant_roles))
        return role_categories_df[~redundant].copy()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record(self, schema: AxiomSchema, bindings: Dict, mode: str, holds: Optional[bool],
                counterexamples: Optional[int] = None) -> AxiomRecord:
        """Record (or replace) an instantiation for export_ttl()."""
        host = cast(_OntologyRoleHost, self)
        record = AxiomRecord(schema, bindings, mode, holds, counterexamples)
        host.axiom_records = [r for r in host.axiom_records if r.key != record.key] + [record]
        return record

    def _catalog(self):
        """(role pairs, activities) of the log: from inference, or else from the VKG."""
        host = cast(_OntologyRoleHost, self)
        if not host.role_pairs:
            pairs = host.query_sparql(ROLE_PAIRS_QUERY)
            host.role_pairs = sorted({(activity_from_iri(a), otype_from_iri(o))
                                      for a, o in zip(pairs["activity"], pairs["otype"])},
                                     key=lambda p: (p[1], p[0]))
        if not host.activities:
            host.activities = sorted({a for a, _ in host.role_pairs})
        return host.role_pairs, host.activities

    def _lifecycle_cache(self) -> Dict[str, pd.DataFrame]:
        if not hasattr(self, "_lifecycle_results"):
            self._lifecycle_results = {}
        return self._lifecycle_results

    def _lifecycle_activities(self, label: str, ot: str) -> List[str]:
        """A^init_ot or A^close_ot from the bottom-up init/clos results (queried if not yet run)."""
        cache = self._lifecycle_cache()
        if label not in cache:
            cache[label] = self._require_endpoint().query_sparql(INIT_QUERY if label == "init" else CLOS_QUERY)
        df = cache[label]
        if df.empty:
            return []
        return sorted(activity_from_iri(a) for a, o in zip(df["activity"], df["otype"]) if otype_from_iri(o) == ot)

    def _row_bindings(self, schema: AxiomSchema, row: pd.Series) -> Dict:
        """Free-variable values of a bottom-up result row."""
        if schema.label == "trans_life":
            values = {"ot_in": otype_from_iri(row["otype"]), "ot_out": otype_from_iri(row["pairedOType"]),
                      "a": activity_from_iri(row["activity"])}
        else:
            values = {"ot": otype_from_iri(row["otype"])}
            if "a" in schema.free_variables:
                values["a"] = activity_from_iri(row["activity"])
            for bound in ("n", "m"):
                if bound in schema.free_variables:
                    values[bound] = int(row[bound])
        return self._with_derived(schema, values)

    def _with_derived(self, schema: AxiomSchema, values: Dict) -> Dict:
        if "A_init" in schema.derived and "A_init" not in values:
            values["A_init"] = self._lifecycle_activities("init", values["ot"])
        if "A_close" in schema.derived and "A_close" not in values:
            values["A_close"] = self._lifecycle_activities("clos", values["ot"])
        if "A" in schema.derived and "A" not in values:
            role_pairs, _ = self._catalog()
            values["A"] = sorted(a for a, o in role_pairs if o == values["ot"])
        return values

    def _bindings(self, schema: AxiomSchema, **arguments) -> Dict:
        """Map check() arguments to the schema's free variables, resolving labels to log names."""
        values = {_ARGUMENT_NAMES.get(k, k): v for k, v in arguments.items() if v is not None}
        unknown = set(values) - set(schema.free_variables) - set(schema.derived)
        if unknown:
            raise ValueError(f"{schema.label} has no variable(s) {', '.join(sorted(unknown))}; "
                             f"its free variables are {', '.join(schema.free_variables)}")
        for var in _OTYPE_VARIABLES:
            if var in values:
                values[var] = self._resolve_otype(values[var])
        types = [values[v] for v in _OTYPE_VARIABLES if v in values]
        for var in (*_ACTIVITY_VARIABLES, "A_init", "A_close"):
            if var in values:
                raw = values[var]
                resolved = [self._resolve_activity(v, types) for v in ([raw] if isinstance(raw, str) else raw)]
                values[var] = resolved[0] if isinstance(raw, str) else resolved
        return self._with_derived(schema, values)

    def _resolve_otype(self, label: str) -> str:
        role_pairs, _ = self._catalog()
        return self._resolve(otype_from_iri(str(label).strip("<>")), sorted({o for _, o in role_pairs}), "object type")

    def _resolve_activity(self, label: str, types: Sequence[str] = ()) -> str:
        role_pairs, activities = self._catalog()
        candidates = sorted({a for a, o in role_pairs if o in types}) or list(activities)
        what = f"activity of {'/'.join(types)}" if types else "activity"
        return self._resolve(activity_from_iri(str(label).strip("<>")), candidates, what)

    @staticmethod
    def _resolve(label: str, candidates: Sequence[str], what: str) -> str:
        """Exact, then case-insensitive, then punctuation-insensitive, then unique-substring match."""
        if label in candidates:
            return label
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
        for matches in ([c for c in candidates if c.lower() == label.lower()],
                        [c for c in candidates if norm(c) == norm(label)],
                        [c for c in candidates if norm(label) and norm(label) in norm(c)]):
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(f"'{label}' matches several {what}s: {matches}")
        raise ValueError(f"No {what} matches '{label}'. Available: {list(candidates)}")

    def _require_endpoint(self) -> _OntologyRoleHost:
        host = cast(_OntologyRoleHost, self)
        if host.sparql_wrapper is None:
            raise RuntimeError("SPARQL wrapper not initialized. Run run_inference() first.")
        return host

    @staticmethod
    def _with_limit(query: str, limit: Optional[int]) -> str:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1 when provided")
        return query if limit is None else f"{query}\nLIMIT {int(limit)}"

    @staticmethod
    def _coerce_prefixed_or_iri(value: str, default_prefix: str) -> str:
        """Normalize a value into a SPARQL token (IRI or prefixed name)."""
        if value.startswith("<") and value.endswith(">"):
            return value
        if value.startswith("http://") or value.startswith("https://"):
            return f"<{value}>"
        if ":" in value:
            return value
        return f"{default_prefix}:{value}"

    @staticmethod
    def _compact_iri(value: object) -> object:
        """Short display label of an IRI: R_a(ot) for role classes, local names otherwise."""
        if value is None:
            return value
        text = str(value)
        if text in {"nan", "NaN", "<NA>", "NaT", "None"}:
            return None
        if text.startswith(ORO):
            parts = [unquote(p) for p in text[len(ORO):].split("/")]
            if parts[0] == "R" and len(parts) == 3:
                return f"R_{parts[2]}({parts[1]})"
            if parts[0] == "Ev" and len(parts) == 2:
                return f"Ev_{parts[1]}"
            if parts[0] == "role" and len(parts) == 3:
                return f"r({parts[1]}, {parts[2]})"
            return "/".join(parts)
        for prefix in (OCEL, EX + "otype_", EX):
            if text.startswith(prefix):
                return unquote(text[len(prefix):])
        return text

    @staticmethod
    def _raise_ontology_query_error(error: Exception) -> NoReturn:
        """Translate common Ontop/SPARQL failures into notebook-friendly RuntimeErrors."""
        message = str(error)
        if "InvalidMappingSourceQueriesException" in message or "Cannot find relation" in message:
            raise RuntimeError("Ontop is using a stale DB/mapping combination from another log. "
                               "Call .serve() on the log you want to query.") from error
        if "Connection refused" in message or "Failed to establish a new connection" in message:
            raise RuntimeError("The SPARQL endpoint is not reachable. Call .serve() and retry.") from error
        raise error
