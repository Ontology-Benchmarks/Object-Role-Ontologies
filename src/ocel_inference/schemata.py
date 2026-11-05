"""ORO vocabulary and behavioural axiom schemata (paper Sections 4-5).

Every name below is a class or property of the VKG mapping (mapping_artifacts.py):

    O_ot        ex:otype_<ot>                 objects of type ot
    Ev_a        oro:Ev/<a>                    events of activity a
    R_a(ot)     oro:R/<ot>/<a>                role individuals r_{o,e} of ot-objects in a-events
    Role(ot)    oro:Role/<ot>,  Role          all roles of ot, all roles
    playsRole   oro:playsRole                 object -> role
    frames      oro:frames                    event  -> role
    nextRole    oro:nextRole                  role -> directly following role of the same object
    laterRole   oro:laterRole                 role -> every later role of the same object

Roles are ordered by strictly increasing timestamp: roles of one object with equal timestamps
are not ordered, and all of them start (end) its trace if that timestamp is its first (last).

An ``AxiomSchema`` holds what the paper lists for a schema: its free variables, a natural-
language description, the DL axiom(s) it instantiates and, for bottom-up schemata, the SPARQL
query returning all satisfied instantiations at once. The counterexample query of an
instantiation is generated from its DL axiom (dl.Translator, Table X).
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote, unquote

from .dl import (
    BOTTOM,
    AtLeast,
    AtMost,
    Axiom,
    Defined,
    EquivalentClasses,
    Exactly,
    Inverse,
    Named,
    NonEmpty,
    Not,
    Only,
    Property,
    Some,
    SubClassOf,
    Translator,
    and_,
    counterexample_pattern,
    dl,
    or_,
)

EX = "http://www.example.com/"
OCEL = "https://stl.mie.utoronto.ca/ontologies/ocel/"
ORO = "http://www.example.com/oro/"

PREFIXES = f"""PREFIX ex: <{EX}>
PREFIX oro: <{ORO}>
PREFIX ocel: <{OCEL}>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>"""

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

PLAYS_ROLE = Property(ORO + "playsRole", "playsRole")
FRAMES = Property(ORO + "frames", "frames")
NEXT_ROLE = Property(ORO + "nextRole", "nextRole")
LATER_ROLE = Property(ORO + "laterRole", "laterRole")
# Links a role individual to the type-level role ex:eventRole_<a>_<ot> (the punned R_a(ot)),
# so that one query can evaluate a schema for all (a, ot) candidates.
MEMBER_OF = ORO + "memberOf"

OBJECT = Named(ORO + "Object", "O")
EVENT = Named(ORO + "Event", "Event")
ROLE = Named(ORO + "Role", "Role")

R_START = Defined(ORO + "RStart", "R_start", and_(ROLE, Not(Some(Inverse(NEXT_ROLE), ROLE))), r"R_{start}")
R_END = Defined(ORO + "REnd", "R_end", and_(ROLE, Not(Some(NEXT_ROLE, ROLE))), r"R_{end}")


def _enc(text: str) -> str:
    return quote(str(text), safe="")


def _label(text: str) -> str:
    """``Weigh`` stays as is; names with spaces or symbols are braced: ``{Load Truck}``."""
    return text if re.fullmatch(r"[A-Za-z0-9]+", text) else "{" + text + "}"


def _tex(text: str) -> str:
    escaped = re.sub(r"([_&%$#{}])", r"\\\1", str(text))
    return r"\mathit{" + escaped.replace(" ", r"\ ") + "}"


def object_type(ot: str) -> Named:
    return Named(EX + "otype_" + ot, f"O_{_label(ot)}", rf"O_{{{_tex(ot)}}}")


def event_type(a: str) -> Named:
    return Named(ORO + "Ev/" + _enc(a), f"Ev_{_label(a)}", rf"Ev_{{{_tex(a)}}}")


def role(a: str, ot: str) -> Named:
    return Named(ORO + f"R/{_enc(ot)}/{_enc(a)}", f"R_{_label(a)}({ot})", rf"R_{{{_tex(a)}}}({_tex(ot)})")


def role_of_type(ot: str) -> Named:
    return Named(ORO + "Role/" + _enc(ot), f"Role({ot})", rf"Role({_tex(ot)})")


def roles(activities: Union[str, Sequence[str]], ot: str):
    """R_a1(ot) ⊔ ... ⊔ R_am(ot)."""
    return or_(*(role(a, ot) for a in _as_list(activities)))


def r_init(ot: str, activities: Sequence[str]) -> Defined:
    return Defined(ORO + "RInit/" + _enc(ot), f"R_init({ot})", roles(activities, ot), rf"R_{{init}}({_tex(ot)})")


def r_close(ot: str, activities: Sequence[str]) -> Defined:
    return Defined(ORO + "RClose/" + _enc(ot), f"R_close({ot})", roles(activities, ot), rf"R_{{close}}({_tex(ot)})")


def reuse(a: str, ot: str) -> NonEmpty:
    """reuse(ot, a): some ot-object plays two or more roles in a, i.e. R_a(ot) is not single-use.

    O_ot ⊓ (≥2 playsRole.R_a(ot)) ⋢ ⊥; exported as the named concept Reuse_a(ot).
    """
    name = Named(ORO + f"Reuse/{_enc(ot)}/{_enc(a)}", f"Reuse_{_label(a)}({ot})",
                 rf"Reuse_{{{_tex(a)}}}({_tex(ot)})")
    return NonEmpty(and_(object_type(ot), AtLeast(2, PLAYS_ROLE, role(a, ot))), name)


def transforming_events(a: str, ot_in: str, ot_out: str) -> Defined:
    """T_a ≡ Ev_a ⊓ ∃frames.(R_a(ot_in) ⊔ R_a(ot_out))."""
    return Defined(ORO + f"T/{_enc(ot_in)}/{_enc(ot_out)}/{_enc(a)}", f"T_{_label(a)}",
                   and_(event_type(a), Some(FRAMES, or_(role(a, ot_in), role(a, ot_out)))),
                   rf"T_{{{_tex(a)}}}")


def activity_from_iri(iri: str) -> str:
    return unquote(str(iri)[len(OCEL):]) if str(iri).startswith(OCEL) else str(iri)


def otype_from_iri(iri: str) -> str:
    prefix = EX + "otype_"
    return unquote(str(iri)[len(prefix):]) if str(iri).startswith(prefix) else str(iri)


def _as_list(value: Union[str, Sequence[str]]) -> List[str]:
    return [value] if isinstance(value, str) else list(value)


# ---------------------------------------------------------------------------
# Schemata
# ---------------------------------------------------------------------------

# Counterexample query per subject kind: what the individuals in tau_x(C ⊓ ¬D) are.
_SUBJECT_QUERIES = {
    "role": """SELECT ?obj ?event ?activity ?timestamp
WHERE {{
  {{ SELECT DISTINCT ?x WHERE {{
{body}
  }} }}
  ?obj oro:playsRole ?x .
  ?event oro:frames ?x ;
         ocel:hasActivity ?activity ;
         ocel:timestamp ?timestamp .
}}
ORDER BY ?obj ?timestamp ?event""",
    "object": """SELECT DISTINCT (?x AS ?obj)
WHERE {{
{body}
}}
ORDER BY ?obj""",
    "event": """SELECT (?x AS ?event) ?activity ?timestamp
WHERE {{
  {{ SELECT DISTINCT ?x WHERE {{
{body}
  }} }}
  ?x ocel:hasActivity ?activity ;
     ocel:timestamp ?timestamp .
}}
ORDER BY ?timestamp ?event""",
}


@dataclass(frozen=True)
class AxiomSchema:
    """A behavioural axiom schema of the paper.

    name             identifier in result tables (e.g. "creation_role")
    label            the paper's name (e.g. "init")
    description      natural-language reading
    free_variables   variables an instantiation binds, e.g. ("ot", "a"); activity sets are lists
    dl_axiom         the schema in DL notation, as in the paper
    instantiate      free-variable values -> the DL axiom(s) of that instantiation
    subject          what the counterexamples are: "role", "object", "event", or "concept"
                     (NonEmpty schemata: the concepts without a witness)
    query            bottom-up: SPARQL returning every satisfied instantiation (None: top-down only)
    mode             "bottom-up" or "top-down" (primary use, paper Table 2)
    derived          extra values an instantiation needs that bottom-up inference supplies
                     (e.g. ext needs A_init and A_close)
    """

    name: str
    label: str
    description: str
    free_variables: Tuple[str, ...]
    dl_axiom: str
    instantiate: Callable[..., Tuple[Axiom, ...]] = field(repr=False, compare=False)
    subject: str = "role"
    query: Optional[str] = None
    mode: str = "top-down"
    derived: Tuple[str, ...] = ()

    def axioms(self, **bindings) -> Tuple[Axiom, ...]:
        missing = [v for v in (*self.free_variables, *self.derived) if v not in bindings]
        if missing:
            raise ValueError(f"{self.label} needs {', '.join(missing)} (free variables: {', '.join(self.free_variables)})")
        return self.instantiate(**{k: bindings[k] for k in (*self.free_variables, *self.derived)})

    def instance_label(self, **bindings) -> str:
        """The instantiation in the paper's notation, e.g. ``init(forklift, Weigh)``."""
        def show(v):
            return "{" + ", ".join(map(str, v)) + "}" if isinstance(v, (list, tuple)) else str(v)
        return f"{self.label}({', '.join(show(bindings[v]) for v in self.free_variables)})"

    def dl(self, **bindings) -> str:
        """DL notation of the instantiated axiom(s), joined by ``;``."""
        return "; ".join(dl(ax) for ax in self.axioms(**bindings))

    def witness_queries(self, **bindings) -> List[Tuple[NonEmpty, str]]:
        """For each NonEmpty axiom (reuse, eres): a query returning one witness of its concept.

        Such an axiom is violated by the absence of individuals, so its "counterexample" is the
        concept itself (e.g. a single-use role concept R_a(ot) for eres), reported when its
        witness query is empty.
        """
        translator = Translator()
        return [(ax, f"SELECT ?x WHERE {{\n{translator.pattern(ax.concept, '?x')}\n}}\nLIMIT 1")
                for ax in self.axioms(**bindings) if isinstance(ax, NonEmpty)]

    def display_dl(self, **bindings) -> str:
        """Like dl(), but a conjunction over all activities of ot (eres) is shown schematically."""
        axioms = self.axioms(**bindings)
        if len(axioms) > 1 and all(isinstance(ax, NonEmpty) for ax in axioms):
            ot = _label(bindings["ot"])
            return self.dl_axiom.replace("(ot)", f"({bindings['ot']})").replace("O_ot", f"O_{ot}").replace("A_ot", f"A_{ot}")
        return self.dl(**bindings)

    def counterexample_query(self, **bindings) -> Optional[str]:
        """SPARQL selecting the counterexamples: tau_x(C ⊓ ¬D) for each axiom C ⊑ D.

        None when no individual can violate the instantiation (e.g. ext with no init or
        closure roles), and for NonEmpty schemata (reuse, eres): see witness_queries().
        """
        translator = Translator()
        patterns = [p for p in (counterexample_pattern(ax, "?x", translator) for ax in self.axioms(**bindings)) if p]
        if not patterns:
            return None
        body = patterns[0] if len(patterns) == 1 else "\nUNION\n".join("{\n" + p + "\n}" for p in patterns)
        indented = "\n".join("    " + line if line else line for line in body.splitlines())
        return PREFIXES + "\n" + _SUBJECT_QUERIES[self.subject].format(body=indented)


# Bottom-up queries: every candidate (a, ot) is a type-level role ex:eventRole_<a>_<ot>; the role
# individuals of R_a(ot) are linked to it by oro:memberOf. Each query keeps the candidates without
# a counterexample, i.e. it evaluates the schema for all instantiations at once.
_CANDIDATES = """?role a ex:EventObjectRole ;
          ex:playsRoleIn ?activity ;
          ex:isRoleOf ?otype ."""

INIT_QUERY = f"""{PREFIXES}
SELECT ?role ?activity ?otype
WHERE {{
    {_CANDIDATES}
    # a role of R_a(ot) that is not a starting role
    MINUS {{ ?earlier oro:nextRole ?r . ?r oro:memberOf ?role . }}
}}
ORDER BY ?otype ?activity"""

CLOS_QUERY = f"""{PREFIXES}
SELECT ?role ?activity ?otype
WHERE {{
    {_CANDIDATES}
    # a role of R_a(ot) that is not an ending role
    MINUS {{ ?r oro:nextRole ?later ; oro:memberOf ?role . }}
}}
ORDER BY ?otype ?activity"""

TRANS_LIFE_QUERY = f"""{PREFIXES}
SELECT ?activity ?otype ?pairedOType ?role ?pairedRole
WHERE {{
    ?role a ex:EventObjectRole ; ex:playsRoleIn ?activity ; ex:isRoleOf ?otype .
    ?pairedRole a ex:EventObjectRole ; ex:playsRoleIn ?activity ; ex:isRoleOf ?pairedOType .
    FILTER(?otype != ?pairedOType)
    # clos(ot_in, a): no role of R_a(ot_in) is followed by another role
    MINUS {{ ?rIn oro:nextRole ?later ; oro:memberOf ?role . }}
    # init(ot_out, a): no role of R_a(ot_out) is preceded by another role
    MINUS {{ ?earlier oro:nextRole ?rOut . ?rOut oro:memberOf ?pairedRole . }}
}}
ORDER BY ?activity ?otype ?pairedOType"""

_TWO_ROLES_IN_A = """{ SELECT ?role WHERE {
            ?obj oro:playsRole ?r .
            ?r oro:memberOf ?role .
        } GROUP BY ?obj ?role HAVING (COUNT(DISTINCT ?r) >= 2) }"""

SINGLE_QUERY = f"""{PREFIXES}
SELECT ?role ?activity ?otype
WHERE {{
    {_CANDIDATES}
    # an object playing two roles of R_a(ot)
    MINUS {{ {_TWO_ROLES_IN_A} }}
}}
ORDER BY ?otype ?activity"""

REUSE_QUERY = f"""{PREFIXES}
SELECT DISTINCT ?role ?activity ?otype
WHERE {{
    {_CANDIDATES}
    # witness: an object playing two roles of R_a(ot)
    {_TWO_ROLES_IN_A}
}}
ORDER BY ?otype ?activity"""

EXT_QUERY = f"""{PREFIXES}
SELECT DISTINCT ?otype
WHERE {{
    ?role a ex:EventObjectRole ; ex:isRoleOf ?otype .
    # ot has an init role: some R_a(ot) all of whose roles are starting roles
    MINUS {{
        ?initRole a ex:EventObjectRole ; ex:isRoleOf ?otype .
        MINUS {{ ?earlier oro:nextRole ?r1 . ?r1 oro:memberOf ?initRole . }}
    }}
    # ot has a closure role
    MINUS {{
        ?closeRole a ex:EventObjectRole ; ex:isRoleOf ?otype .
        MINUS {{ ?r2 oro:nextRole ?later . ?r2 oro:memberOf ?closeRole . }}
    }}
}}
ORDER BY ?otype"""

ERES_QUERY = f"""{PREFIXES}
SELECT DISTINCT ?otype
WHERE {{
    ?role a ex:EventObjectRole ; ex:isRoleOf ?otype .
    # ot has a single-use role concept R_a(ot): no object plays two of its roles
    MINUS {{
        ?single a ex:EventObjectRole ; ex:isRoleOf ?otype .
        MINUS {{ {_TWO_ROLES_IN_A.replace("?role", "?single")} }}
    }}
}}
ORDER BY ?otype"""

# Multiplicity bounds: the tightest (n, m) per (a, ot), counting objects/events with 0 roles.
# These read the role statistics computed when the VKG is built (data_pipeline.py).
OBJECT_MULTIPLICITY_QUERY = f"""{PREFIXES}
SELECT ?role ?activity ?otype ?n ?m
WHERE {{
    {_CANDIDATES}
    ?role ex:objectRoleMinCardinality ?n ; ex:objectRoleMaxCardinality ?m .
}}
ORDER BY ?otype ?activity"""

EVENT_MULTIPLICITY_QUERY = f"""{PREFIXES}
SELECT ?role ?activity ?otype ?n ?m
WHERE {{
    {_CANDIDATES}
    ?role ex:eventRoleMinCardinality ?n ; ex:eventRoleMaxCardinality ?m .
}}
ORDER BY ?otype ?activity"""


def _bounds(prop, filler, n: int, m: int):
    return and_(AtLeast(int(n), prop, filler), AtMost(int(m), prop, filler))


SCHEMATA: Tuple[AxiomSchema, ...] = (
    # --- Lifecycle boundaries (bottom-up) ---
    AxiomSchema(
        "creation_role", "init",
        "Activity a consistently starts the lifecycle of ot-objects: every role of ot in a is a starting role.",
        ("ot", "a"), "R_a(ot) ⊑ R_start",
        lambda ot, a: (SubClassOf(roles(a, ot), R_START),),
        subject="role", query=INIT_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "closure_role", "clos",
        "Activity a consistently ends the lifecycle of ot-objects: every role of ot in a is an ending role.",
        ("ot", "a"), "R_a(ot) ⊑ R_end",
        lambda ot, a: (SubClassOf(roles(a, ot), R_END),),
        subject="role", query=CLOS_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "external_object_type", "ext",
        "ot-objects play no init or closure role: their lifecycle starts and ends outside the log.",
        ("ot",), "O_ot ⊑ ∀playsRole.¬(R_init(ot) ⊔ R_close(ot))",
        lambda ot, A_init, A_close: (
            SubClassOf(object_type(ot), Only(PLAYS_ROLE, Not(or_(r_init(ot, A_init), r_close(ot, A_close))))),),
        subject="object", query=EXT_QUERY, mode="bottom-up", derived=("A_init", "A_close"),
    ),
    AxiomSchema(
        "resource_object_type", "eres",
        "ot is an external resource type: all its role concepts are re-usable, i.e. no activity a "
        "has a role of ot that is single-use across the log (reuse(ot, a) for every a ∈ A_ot).",
        ("ot",), "O_ot ⊓ (≥2 playsRole.R_a(ot)) ⋢ ⊥ for every a ∈ A_ot",
        lambda ot, A: tuple(reuse(a, ot) for a in A),
        subject="concept", query=ERES_QUERY, mode="bottom-up", derived=("A",),
    ),
    # --- Lifecycle transformations ---
    AxiomSchema(
        "transformation_role", "trans_life",
        "Activity a closes ot_in-objects and initializes ot_out-objects (clos(ot_in, a) and init(ot_out, a)).",
        ("ot_in", "ot_out", "a"), "R_a(ot_in) ⊑ R_end; R_a(ot_out) ⊑ R_start",
        lambda ot_in, ot_out, a: (SubClassOf(roles(a, ot_in), R_END), SubClassOf(roles(a, ot_out), R_START)),
        subject="role", query=TRANS_LIFE_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "one_to_one_transformation", "oto",
        "Each transforming a-event frames exactly one ot_in-object and one ot_out-object.",
        ("ot_in", "ot_out", "a"), "T_a ⊑ (=1 frames.R_a(ot_in)); T_a ⊑ (=1 frames.R_a(ot_out))",
        lambda ot_in, ot_out, a: (
            SubClassOf(transforming_events(a, ot_in, ot_out), Exactly(1, FRAMES, role(a, ot_in))),
            SubClassOf(transforming_events(a, ot_in, ot_out), Exactly(1, FRAMES, role(a, ot_out)))),
        subject="event",
    ),
    AxiomSchema(
        "split_transformation", "split",
        "Each transforming a-event frames one ot_in-object and at least two ot_out-objects.",
        ("ot_in", "ot_out", "a"), "T_a ⊑ (=1 frames.R_a(ot_in)); T_a ⊑ (≥2 frames.R_a(ot_out))",
        lambda ot_in, ot_out, a: (
            SubClassOf(transforming_events(a, ot_in, ot_out), Exactly(1, FRAMES, role(a, ot_in))),
            SubClassOf(transforming_events(a, ot_in, ot_out), AtLeast(2, FRAMES, role(a, ot_out)))),
        subject="event",
    ),
    AxiomSchema(
        "merge_transformation", "merge",
        "Each transforming a-event frames at least two ot_in-objects and one ot_out-object.",
        ("ot_in", "ot_out", "a"), "T_a ⊑ (≥2 frames.R_a(ot_in)); T_a ⊑ (=1 frames.R_a(ot_out))",
        lambda ot_in, ot_out, a: (
            SubClassOf(transforming_events(a, ot_in, ot_out), AtLeast(2, FRAMES, role(a, ot_in))),
            SubClassOf(transforming_events(a, ot_in, ot_out), Exactly(1, FRAMES, role(a, ot_out)))),
        subject="event",
    ),
    AxiomSchema(
        "any_transformation", "gtrans",
        "Each transforming a-event frames at least one ot_in-object and one ot_out-object.",
        ("ot_in", "ot_out", "a"), "T_a ⊑ (≥1 frames.R_a(ot_in)); T_a ⊑ (≥1 frames.R_a(ot_out))",
        lambda ot_in, ot_out, a: (
            SubClassOf(transforming_events(a, ot_in, ot_out), AtLeast(1, FRAMES, role(a, ot_in))),
            SubClassOf(transforming_events(a, ot_in, ot_out), AtLeast(1, FRAMES, role(a, ot_out)))),
        subject="event",
    ),
    AxiomSchema(
        "necessary_transformation", "trans_nec",
        "Every ot_in-object is transformed in a, and every ot_out-object results from a, exactly once.",
        ("ot_in", "ot_out", "a"), "O_ot_in ⊑ (=1 playsRole.R_a(ot_in)); O_ot_out ⊑ (=1 playsRole.R_a(ot_out))",
        lambda ot_in, ot_out, a: (
            SubClassOf(object_type(ot_in), Exactly(1, PLAYS_ROLE, role(a, ot_in))),
            SubClassOf(object_type(ot_out), Exactly(1, PLAYS_ROLE, role(a, ot_out)))),
        subject="object",
    ),
    # --- Lifecycle participation ---
    AxiomSchema(
        "single_use_role", "single",
        "Each ot-object plays at most one role in activity a.",
        ("ot", "a"), "O_ot ⊑ (≤1 playsRole.R_a(ot))",
        lambda ot, a: (SubClassOf(object_type(ot), AtMost(1, PLAYS_ROLE, roles(a, ot))),),
        subject="object", query=SINGLE_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "resource_like_role", "reuse",
        "Some ot-object plays several roles in activity a (re-use; the complement of single).",
        ("ot", "a"), "O_ot ⊓ (≥2 playsRole.R_a(ot)) ⋢ ⊥",
        lambda ot, a: (reuse(a, ot),),
        subject="concept", query=REUSE_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "exclusive_roles", "excl",
        "No ot-object plays roles in both a1 and a2.",
        ("ot", "a1", "a2"), "O_ot ⊑ ¬(∃playsRole.R_a1(ot) ⊓ ∃playsRole.R_a2(ot))",
        lambda ot, a1, a2: (SubClassOf(object_type(ot), Not(and_(Some(PLAYS_ROLE, roles(a1, ot)),
                                                                 Some(PLAYS_ROLE, roles(a2, ot))))),),
        subject="object",
    ),
    AxiomSchema(
        "alternative_roles", "alt",
        "Each ot-object plays exactly one role among the activities A (e.g. alternative closures).",
        ("ot", "A"), "O_ot ⊑ (=1 playsRole.(R_a1(ot) ⊔ … ⊔ R_am(ot)))",
        lambda ot, A: (SubClassOf(object_type(ot), Exactly(1, PLAYS_ROLE, roles(A, ot))),),
        subject="object",
    ),
    AxiomSchema(
        "role_budget", "budg",
        "Each ot-object plays at most N roles among the activities A.",
        ("ot", "A", "N"), "O_ot ⊑ (≤N playsRole.(R_a1(ot) ⊔ … ⊔ R_am(ot)))",
        lambda ot, A, N: (SubClassOf(object_type(ot), AtMost(int(N), PLAYS_ROLE, roles(A, ot))),),
        subject="object",
    ),
    AxiomSchema(
        "shared_roles", "shar",
        "Every ot-object playing a role in a2 also plays a role in a1.",
        ("ot", "a1", "a2"), "∃playsRole.R_a2(ot) ⊑ ∃playsRole.R_a1(ot)",
        lambda ot, a1, a2: (SubClassOf(Some(PLAYS_ROLE, roles(a2, ot)), Some(PLAYS_ROLE, roles(a1, ot))),),
        subject="object",
    ),
    AxiomSchema(
        "carry_over", "carr",
        "Every role of ot in a2 is preceded by a role of the same object in a1.",
        ("ot", "a1", "a2"), "R_a2(ot) ⊑ ∃laterRole⁻.R_a1(ot)",
        lambda ot, a1, a2: (SubClassOf(roles(a2, ot), Some(Inverse(LATER_ROLE), roles(a1, ot))),),
        subject="role",
    ),
    AxiomSchema(
        "carry_over_next", "carr_next",
        "Every role of ot in a2 directly follows a role of the same object in a1.",
        ("ot", "a1", "a2"), "R_a2(ot) ⊑ ∃nextRole⁻.R_a1(ot)",
        lambda ot, a1, a2: (SubClassOf(roles(a2, ot), Some(Inverse(NEXT_ROLE), roles(a1, ot))),),
        subject="role",
    ),
    AxiomSchema(
        "carry_over_forward", "carr_fwd",
        "Every role of ot in a1 is followed by a role of the same object in a2.",
        ("ot", "a1", "a2"), "R_a1(ot) ⊑ ∃laterRole.R_a2(ot)",
        lambda ot, a1, a2: (SubClassOf(roles(a1, ot), Some(LATER_ROLE, roles(a2, ot))),),
        subject="role",
    ),
    AxiomSchema(
        "object_multiplicity", "PerObjMult",
        "Each ot-object plays between n and m roles in activity a.",
        ("ot", "a", "n", "m"), "O_ot ⊑ (≥n playsRole.R_a(ot)) ⊓ (≤m playsRole.R_a(ot))",
        lambda ot, a, n, m: (SubClassOf(object_type(ot), _bounds(PLAYS_ROLE, role(a, ot), n, m)),),
        subject="object", query=OBJECT_MULTIPLICITY_QUERY, mode="bottom-up",
    ),
    AxiomSchema(
        "event_multiplicity", "PerEventMult",
        "Each a-event frames between n and m roles of ot-objects.",
        ("ot", "a", "n", "m"), "Ev_a ⊑ (≥n frames.R_a(ot)) ⊓ (≤m frames.R_a(ot))",
        lambda ot, a, n, m: (SubClassOf(event_type(a), _bounds(FRAMES, role(a, ot), n, m)),),
        subject="event", query=EVENT_MULTIPLICITY_QUERY, mode="bottom-up",
    ),
)

SCHEMA_BY_NAME: Dict[str, AxiomSchema] = {s.name: s for s in SCHEMATA}
SCHEMA_BY_LABEL: Dict[str, AxiomSchema] = {s.label.lower(): s for s in SCHEMATA}
SCHEMA_BY_LABEL.update({"carr→": SCHEMA_BY_NAME["carry_over_forward"], "carr->": SCHEMA_BY_NAME["carry_over_forward"]})

# The role categories of the bottom-up table (multiplicity bounds go only into the ontology export).
BOTTOM_UP = tuple(s for s in SCHEMATA if s.mode == "bottom-up" and s.label not in ("PerObjMult", "PerEventMult"))
MULTIPLICITIES = (SCHEMA_BY_NAME["object_multiplicity"], SCHEMA_BY_NAME["event_multiplicity"])


def get_schema(key: str) -> AxiomSchema:
    """A schema by paper label (``init``, ``carr→``) or name (``creation_role``)."""
    k = key.strip()
    schema = SCHEMA_BY_LABEL.get(k.lower()) or SCHEMA_BY_NAME.get(k)
    if schema is None:
        known = ", ".join(s.label for s in SCHEMATA)
        raise ValueError(f"Unknown schema '{key}'. Known: {known} (or their names, e.g. creation_role).")
    return schema


def get_default_role_categories() -> List[AxiomSchema]:
    """The bottom-up schemata, evaluated by ``infer_and_display_role_categories``."""
    return list(BOTTOM_UP)


# for compatibility with previously named version
RoleCategory = AxiomSchema
OntologicalRoleCategory = AxiomSchema
