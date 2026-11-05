"""OWL 2 export of an ORO: the core T-box of the log plus its bottom-up axioms.

The file holds T-box axioms only. The A-box (objects, events, role individuals) stays
virtual in the VKG, whose classes and properties have the same IRIs as the ontology.
Bottom-up axioms hold in the log by construction, so the log (as a finite interpretation) is a
model of the exported T-box. Top-down checks are not exported.

Every schema instantiation is an annotated axiom:

    oro:schema        "init"
    rdfs:label        "init(forklift, Weigh)"
    oro:dl            "R_Weigh(forklift) ⊑ R_start"

An instantiation that is a tautology once empty concepts are ⊥ (ext(ot) of a type without
init or closure roles is O_ot ⊑ ⊤) has no logical axiom; its oro:schema and oro:dl annotate O_ot.

reuse(ot, a) is not an axiom but a non-empty concept; it is exported as the named class
Reuse_a(ot) (oro:Reuse/<ot>/<a>) ≡ O_ot ⊓ (≥2 playsRole.R_a(ot)), annotated with
``oro:nonEmpty true``. eres(ot) is the conjunction of reuse(ot, a) over the activities of ot,
exported as one such class per activity.
The axioms are in OWL 2 DL (SROIQ); the core adds the SHIQ axioms nextRole ⊑ laterRole and
Trans(laterRole) of the paper's footnote, so laterRole contains the closure of nextRole.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .dl import (
    BOTTOM,
    Defined,
    EquivalentClasses,
    Exactly,
    Inverse,
    Named,
    NonEmpty,
    Only,
    Some,
    SubClassOf,
    add_owl,
    and_,
    dl,
    or_,
    simplify,
    tautology,
    walk,
)
from .schemata import (
    EVENT,
    FRAMES,
    LATER_ROLE,
    NEXT_ROLE,
    OBJECT,
    ORO,
    PLAYS_ROLE,
    R_END,
    R_START,
    ROLE,
    AxiomSchema,
    _enc,
    event_type,
    object_type,
    role,
    role_of_type,
)


@dataclass
class AxiomRecord:
    """One instantiation of a schema, with its outcome on the log."""

    schema: AxiomSchema
    bindings: Dict
    mode: str                       # "bottom-up" / "top-down"
    holds: Optional[bool] = True
    counterexamples: Optional[int] = None
    axioms: Tuple = field(init=False)

    def __post_init__(self):
        self.axioms = self.schema.axioms(**self.bindings)

    @property
    def label(self) -> str:
        return self.schema.instance_label(**self.bindings)

    @property
    def key(self) -> Tuple[str, str]:
        return self.schema.name, self.label

    @property
    def dl(self) -> str:
        return "; ".join(dl(ax) for ax in self.axioms)


def core_axioms(role_pairs: Sequence[Tuple[str, str]], activities: Sequence[str]) -> List:
    """The T-box of Section 4 for the log's object types, activities and role concepts."""
    otypes = sorted({ot for _, ot in role_pairs})
    axioms = [
        EquivalentClasses(OBJECT, or_(*(object_type(ot) for ot in otypes))),
        EquivalentClasses(EVENT, or_(*(event_type(a) for a in activities))),
        EquivalentClasses(ROLE, or_(*(role_of_type(ot) for ot in otypes))),
    ]
    for ot in otypes:
        axioms.append(EquivalentClasses(role_of_type(ot), or_(*(role(a, o) for a, o in role_pairs if o == ot))))
        for rho in (NEXT_ROLE, LATER_ROLE):
            axioms.append(SubClassOf(Some(rho, role_of_type(ot)), role_of_type(ot)))
            axioms.append(SubClassOf(role_of_type(ot), Only(rho, role_of_type(ot))))
    for a, ot in role_pairs:
        axioms.append(SubClassOf(role(a, ot), and_(Exactly(1, Inverse(FRAMES), event_type(a)),
                                                   Exactly(1, Inverse(PLAYS_ROLE), object_type(ot)))))
    axioms += [EquivalentClasses(R_START, R_START.definition), EquivalentClasses(R_END, R_END.definition)]
    return axioms


def build_graph(name: str, role_pairs: Sequence[Tuple[str, str]], activities: Sequence[str],
                records: Iterable[AxiomRecord]):
    """rdflib Graph of the ORO of one log."""
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import OWL, RDF, RDFS

    oro = lambda local: URIRef(ORO + local)
    g = Graph()
    g.bind("oro", ORO)
    g.bind("ex", "http://www.example.com/")
    g.bind("owl", OWL)
    ontology = oro(f"log/{_enc(name)}")
    g.add((ontology, RDF.type, OWL.Ontology))
    g.add((ontology, RDFS.label, Literal(f"Object Role Ontology of {name}")))
    g.add((ontology, RDFS.comment, Literal(
        "T-box of the ORO: core axioms (Section 4) and the behavioural axioms inferred bottom-up "
        "on the log. The A-box is virtual (Ontop VKG over DuckDB) and uses the same IRIs.")))

    for prop in ("schema", "dl", "nonEmpty"):
        g.add((oro(prop), RDF.type, OWL.AnnotationProperty))
    for p in (PLAYS_ROLE, FRAMES, NEXT_ROLE, LATER_ROLE):
        g.add((URIRef(p.iri), RDF.type, OWL.ObjectProperty))
        g.add((URIRef(p.iri), RDFS.label, Literal(p.label)))
    g.add((URIRef(PLAYS_ROLE.iri), RDFS.domain, URIRef(OBJECT.iri)))
    g.add((URIRef(FRAMES.iri), RDFS.domain, URIRef(EVENT.iri)))
    for p in (PLAYS_ROLE, FRAMES, NEXT_ROLE, LATER_ROLE):
        g.add((URIRef(p.iri), RDFS.range, URIRef(ROLE.iri)))
    for p in (NEXT_ROLE, LATER_ROLE):
        g.add((URIRef(p.iri), RDFS.domain, URIRef(ROLE.iri)))
    g.add((URIRef(NEXT_ROLE.iri), RDFS.subPropertyOf, URIRef(LATER_ROLE.iri)))
    g.add((URIRef(LATER_ROLE.iri), RDF.type, OWL.TransitiveProperty))

    named: Dict[str, Named] = {}
    defined: Dict[str, Defined] = {}

    def note(axiom):
        for x in walk(axiom):
            if isinstance(x, Named):
                named.setdefault(x.iri, x)
            elif isinstance(x, Defined) and simplify(x.definition) != BOTTOM:  # empty ones become owl:Nothing
                defined.setdefault(x.iri, x)

    for axiom in core_axioms(role_pairs, activities):
        note(axiom)
        add_owl(g, axiom)

    for record in records:
        if record.mode != "bottom-up":
            continue  # top-down checks are not part of the exported ORO
        annotations = {
            str(oro("schema")): record.schema.label,
            str(RDFS.label): record.label,
            str(oro("dl")): record.dl,
        }
        for axiom in record.axioms:
            if isinstance(axiom, NonEmpty):
                values = "/".join(_enc(record.bindings[v]) for v in record.schema.free_variables)
                concept = axiom.name or Named(ORO + f"{record.schema.label}/{values}", record.label)
                axiom = EquivalentClasses(concept, axiom.concept)
                annotations[str(oro("nonEmpty"))] = Literal(True)
            if tautology(axiom):
                # E.g. ext(ot) for a type without init or closure roles: O_ot ⊑ ∀playsRole.¬(⊥ ⊔ ⊥)
                # is O_ot ⊑ ⊤. No logical axiom; the instantiation is an annotation of O_ot.
                if isinstance(axiom, SubClassOf) and isinstance(axiom.sub, Named):
                    note(axiom.sub)
                    for key in (str(oro("schema")), str(oro("dl"))):
                        g.add((URIRef(axiom.sub.iri), URIRef(key), Literal(annotations[key])))
                continue
            note(axiom)
            add_owl(g, axiom, annotations)

    # walk() descends into definitions, so `defined` already holds every nested defined concept.
    for d in list(defined.values()):
        if d.iri not in (R_START.iri, R_END.iri):
            add_owl(g, EquivalentClasses(d, d.definition))
    for c in [*named.values(), *defined.values()]:
        g.add((URIRef(c.iri), RDF.type, OWL.Class))
        g.add((URIRef(c.iri), RDFS.label, Literal(c.label)))
    return g


def write_ttl(path: Path, name: str, role_pairs, activities, records: Iterable[AxiomRecord]) -> Path:
    graph = build_graph(name, role_pairs, activities, records)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(path), format="turtle")
    return path
