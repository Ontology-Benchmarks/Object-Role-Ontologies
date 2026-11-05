"""The ALCQI concept language of the ORO, with three renderings of each expression.

* ``dl(x)``: DL notation (``R_Weigh(forklift) ⊑ R_start``); ``latex(x)`` for the paper.
* ``add_owl(graph, axiom)``: the OWL 2 axiom as RDF triples (for the Turtle export).
* ``Translator``: the SPARQL graph pattern tau_x(C) of Table X, whose solutions for ?x are
  the extension of C in the log, read as a finite interpretation (closed world).
  Counterexamples to C ⊑ D are the answers to tau_x(C ⊓ ¬D).

Translation rules (``bound``: ?x is already bound by the enclosing pattern):

    A               ?x a <A> .
    C ⊓ D           tau_x(C) tau_x(D)                 (binding conjuncts first)
    C ⊔ D           { tau_x(C) } UNION { tau_x(D) }
    ¬C              MINUS { tau_x(C) }                (bound)
    ∃r.C            ?x <r> ?y . tau_y(C)              (r⁻ swaps ?x and ?y)
    ∀r.C            MINUS { ?x <r> ?y . tau_y(¬C) }   (bound)
    (≥n r.C)        { SELECT ?x { ?x <r> ?y . tau_y(C) } GROUP BY ?x HAVING (COUNT(DISTINCT ?y) >= n) }
    (=n r.C)        as (≥n r.C), with = n
    (≤n r.C)        OPTIONAL { { SELECT ?x (COUNT(DISTINCT ?y) AS ?c) { ?x <r> ?y . tau_y(C) } GROUP BY ?x } }
                    FILTER(!BOUND(?c) || ?c <= n)     (bound; no r-successor counts as 0)

Negation uses MINUS rather than FILTER NOT EXISTS because Ontop does not support NOT EXISTS
over variables of the enclosing pattern; with ?x shared and bound, the two coincide.
"""

import itertools
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Properties (DL roles; "property" avoids a clash with the ORO's object roles)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Property:
    iri: str
    label: str


@dataclass(frozen=True)
class Inverse:
    prop: Property

    @property
    def label(self) -> str:
        return f"{self.prop.label}⁻"


PropertyExpr = Union[Property, Inverse]


# ---------------------------------------------------------------------------
# Concepts
# ---------------------------------------------------------------------------


class Concept:
    def __and__(self, other: "Concept") -> "Concept":
        return and_(self, other)

    def __or__(self, other: "Concept") -> "Concept":
        return or_(self, other)

    def __invert__(self) -> "Concept":
        return Not(self)

    def __str__(self) -> str:
        return dl(self)


@dataclass(frozen=True, eq=True)
class Named(Concept):
    """A concept name; in the ORO, a class exposed by the VKG mapping."""

    iri: str
    label: str
    tex: Optional[str] = None


@dataclass(frozen=True, eq=True)
class Defined(Concept):
    """A defined concept name, ``label ≡ definition``. SPARQL expands the definition."""

    iri: str
    label: str
    definition: Concept
    tex: Optional[str] = None


@dataclass(frozen=True, eq=True)
class _Constant(Concept):
    symbol: str


TOP = _Constant("⊤")
BOTTOM = _Constant("⊥")


@dataclass(frozen=True, eq=True)
class Not(Concept):
    operand: Concept


@dataclass(frozen=True, eq=True)
class And(Concept):
    operands: Tuple[Concept, ...]


@dataclass(frozen=True, eq=True)
class Or(Concept):
    operands: Tuple[Concept, ...]


@dataclass(frozen=True, eq=True)
class Some(Concept):
    prop: PropertyExpr
    filler: Concept = TOP


@dataclass(frozen=True, eq=True)
class Only(Concept):
    prop: PropertyExpr
    filler: Concept


@dataclass(frozen=True, eq=True)
class AtLeast(Concept):
    n: int
    prop: PropertyExpr
    filler: Concept = TOP


@dataclass(frozen=True, eq=True)
class AtMost(Concept):
    n: int
    prop: PropertyExpr
    filler: Concept = TOP


@dataclass(frozen=True, eq=True)
class Exactly(Concept):
    n: int
    prop: PropertyExpr
    filler: Concept = TOP


def and_(*operands: Concept) -> Concept:
    """Conjunction, flattened; ⊤ is dropped and ⊥ absorbs."""
    flat = []
    for c in operands:
        flat.extend(c.operands if isinstance(c, And) else (c,))
    if BOTTOM in flat:
        return BOTTOM
    flat = list(dict.fromkeys(c for c in flat if c != TOP))
    return TOP if not flat else flat[0] if len(flat) == 1 else And(tuple(flat))


def or_(*operands: Concept) -> Concept:
    """Disjunction, flattened; ⊥ is dropped and ⊤ absorbs."""
    flat = []
    for c in operands:
        flat.extend(c.operands if isinstance(c, Or) else (c,))
    if TOP in flat:
        return TOP
    flat = list(dict.fromkeys(c for c in flat if c != BOTTOM))
    return BOTTOM if not flat else flat[0] if len(flat) == 1 else Or(tuple(flat))


# ---------------------------------------------------------------------------
# Axioms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubClassOf:
    sub: Concept
    sup: Concept


@dataclass(frozen=True)
class EquivalentClasses:
    left: Concept
    right: Concept


@dataclass(frozen=True)
class NonEmpty:
    """The extension of ``concept`` is non-empty in the log (e.g. reuse). Not an OWL axiom.

    ``name`` (optional) names the concept in the OWL export.
    """

    concept: Concept
    name: Optional[Named] = None


Axiom = Union[SubClassOf, EquivalentClasses, NonEmpty]


def walk(x):
    """All concepts and properties inside a concept or axiom (pre-order, with repeats)."""
    yield x
    children = ()
    if isinstance(x, (Not,)):
        children = (x.operand,)
    elif isinstance(x, (And, Or)):
        children = x.operands
    elif isinstance(x, (Some, Only, AtLeast, AtMost, Exactly)):
        children = (x.prop, x.filler)
    elif isinstance(x, Inverse):
        children = (x.prop,)
    elif isinstance(x, Defined):
        children = (x.definition,)
    elif isinstance(x, SubClassOf):
        children = (x.sub, x.sup)
    elif isinstance(x, EquivalentClasses):
        children = (x.left, x.right)
    elif isinstance(x, NonEmpty):
        children = (x.concept,)
    for child in children:
        yield from walk(child)


# ---------------------------------------------------------------------------
# DL and LaTeX notation
# ---------------------------------------------------------------------------

_UNICODE = {"and": " ⊓ ", "or": " ⊔ ", "not": "¬", "some": "∃", "only": "∀", "ge": "≥", "le": "≤",
            "eq": "=", "sub": " ⊑ ", "equiv": " ≡ ", "nonempty": " ⋢ ⊥", "top": "⊤", "bottom": "⊥"}
_LATEX = {"and": r" \sqcap ", "or": r" \sqcup ", "not": r"\neg ", "some": r"\exists ", "only": r"\forall ",
          "ge": r"\geq ", "le": r"\leq ", "eq": "=", "sub": r" \sqsubseteq ", "equiv": r" \equiv ",
          "nonempty": r" \not\sqsubseteq \bot", "top": r"\top", "bottom": r"\bot"}


def dl(x) -> str:
    """DL notation of a concept, property or axiom."""
    return _render(x, _UNICODE)


def latex(x) -> str:
    """LaTeX math notation (without $...$) of a concept, property or axiom."""
    return _render(x, _LATEX)


def _render(x, sym: Dict[str, str]) -> str:
    tex = sym is _LATEX

    def prop(p: PropertyExpr) -> str:
        if isinstance(p, Inverse):
            return rf"\mathit{{{p.prop.label}}}^{{-}}" if tex else p.label
        return rf"\mathit{{{p.label}}}" if tex else p.label

    def wrap(c: Concept) -> str:
        return f"({go(c)})" if isinstance(c, (And, Or)) else go(c)

    def go(c) -> str:
        if isinstance(c, (Named, Defined)):
            return (c.tex or rf"\mathit{{{c.label}}}") if tex else c.label
        if c == TOP:
            return sym["top"]
        if c == BOTTOM:
            return sym["bottom"]
        if isinstance(c, Not):
            return sym["not"] + wrap(c.operand)
        if isinstance(c, And):
            return sym["and"].join(wrap(o) for o in c.operands)
        if isinstance(c, Or):
            return sym["or"].join(wrap(o) for o in c.operands)
        if isinstance(c, Some):
            return f"{sym['some']}{prop(c.prop)}.{wrap(c.filler)}"
        if isinstance(c, Only):
            return f"{sym['only']}{prop(c.prop)}.{wrap(c.filler)}"
        for cls, key in ((AtLeast, "ge"), (AtMost, "le"), (Exactly, "eq")):
            if isinstance(c, cls):
                space = r"\ " if tex else " "
                return f"({sym[key]}{c.n}{space}{prop(c.prop)}.{wrap(c.filler)})"
        if isinstance(c, SubClassOf):
            return go(c.sub) + sym["sub"] + go(c.sup)
        if isinstance(c, EquivalentClasses):
            return go(c.left) + sym["equiv"] + go(c.right)
        if isinstance(c, NonEmpty):
            return go(c.concept) + sym["nonempty"]
        if isinstance(c, (Property, Inverse)):
            return prop(c)
        raise TypeError(f"Cannot render {c!r}")

    return go(x)


# ---------------------------------------------------------------------------
# OWL 2 (RDF)
# ---------------------------------------------------------------------------


def prune(c: Concept) -> Concept:
    """Fold ⊤ and ⊥ out of a concept for OWL output, keeping its shape otherwise.

    An empty defined concept (e.g. R_init(ot) of a type without init roles) becomes ⊥, so the
    export has no named class ≡ owl:Nothing (an unsatisfiable class to a reasoner) and no
    degenerate expressions such as ∀playsRole.¬(⊥ ⊔ ⊥).
    """
    if isinstance(c, Defined):
        return BOTTOM if simplify(c.definition) == BOTTOM else c
    if isinstance(c, Not):
        inner = prune(c.operand)
        return BOTTOM if inner == TOP else TOP if inner == BOTTOM else Not(inner)
    if isinstance(c, And):
        return and_(*(prune(o) for o in c.operands))
    if isinstance(c, Or):
        return or_(*(prune(o) for o in c.operands))
    if isinstance(c, Some):
        filler = prune(c.filler)
        return BOTTOM if filler == BOTTOM else Some(c.prop, filler)
    if isinstance(c, Only):
        filler = prune(c.filler)
        return TOP if filler == TOP else Only(c.prop, filler)
    if isinstance(c, (AtLeast, AtMost, Exactly)):
        filler = prune(c.filler)
        if isinstance(c, AtMost) and filler == BOTTOM:
            return TOP
        if isinstance(c, AtLeast) and c.n == 0:
            return TOP
        if filler == BOTTOM:
            return TOP if c.n == 0 else BOTTOM
        return type(c)(c.n, c.prop, filler)
    return c


def tautology(axiom: Axiom) -> bool:
    """Whether a class axiom holds in every interpretation after pruning (C ⊑ ⊤, ⊥ ⊑ D, C ≡ C)."""
    if isinstance(axiom, SubClassOf):
        return prune(axiom.sub) == BOTTOM or prune(axiom.sup) == TOP
    if isinstance(axiom, EquivalentClasses):
        return prune(axiom.left) == prune(axiom.right)
    return False


def owl_node(graph, c: Concept):
    """The RDF node of a class expression, adding its triples to ``graph`` (an rdflib Graph)."""
    from rdflib import BNode, Literal, URIRef
    from rdflib.collection import Collection
    from rdflib.namespace import OWL, RDF, XSD

    def prop(p: PropertyExpr):
        if isinstance(p, Inverse):
            node = BNode()
            graph.add((node, OWL.inverseOf, URIRef(p.prop.iri)))
            return node
        return URIRef(p.iri)

    c = prune(c)
    if isinstance(c, (Named, Defined)):
        return URIRef(c.iri)
    if c == TOP:
        return OWL.Thing
    if c == BOTTOM:
        return OWL.Nothing
    node = BNode()
    if isinstance(c, (Not, And, Or)):
        graph.add((node, RDF.type, OWL.Class))
        if isinstance(c, Not):
            graph.add((node, OWL.complementOf, owl_node(graph, c.operand)))
        else:
            members = [owl_node(graph, o) for o in c.operands]
            head = BNode()
            Collection(graph, head, members)
            graph.add((node, OWL.intersectionOf if isinstance(c, And) else OWL.unionOf, head))
        return node
    graph.add((node, RDF.type, OWL.Restriction))
    graph.add((node, OWL.onProperty, prop(c.prop)))
    if isinstance(c, Some):
        graph.add((node, OWL.someValuesFrom, owl_node(graph, c.filler)))
    elif isinstance(c, Only):
        graph.add((node, OWL.allValuesFrom, owl_node(graph, c.filler)))
    else:
        predicate = {AtLeast: OWL.minQualifiedCardinality, AtMost: OWL.maxQualifiedCardinality,
                     Exactly: OWL.qualifiedCardinality}[type(c)]
        graph.add((node, predicate, Literal(int(c.n), datatype=XSD.nonNegativeInteger)))
        graph.add((node, OWL.onClass, owl_node(graph, c.filler)))
    return node


def add_owl(graph, axiom: Axiom, annotations: Optional[Dict] = None) -> None:
    """Add an axiom (and, if given, its annotations as a reified owl:Axiom) to ``graph``.

    ``annotations`` maps annotation-property IRIs to Python values or rdflib terms.
    ``NonEmpty`` has no OWL form; only its annotations are added, on the concept.
    """
    from rdflib import BNode, Literal, URIRef
    from rdflib.namespace import OWL, RDF, RDFS

    if isinstance(axiom, NonEmpty):
        subject = owl_node(graph, axiom.concept)
        for key, value in (annotations or {}).items():
            graph.add((subject, URIRef(key), value if hasattr(value, "n3") else Literal(value)))
        return
    if isinstance(axiom, SubClassOf):
        triple = (owl_node(graph, axiom.sub), RDFS.subClassOf, owl_node(graph, axiom.sup))
    else:
        triple = (owl_node(graph, axiom.left), OWL.equivalentClass, owl_node(graph, axiom.right))
    graph.add(triple)
    if annotations:
        reified = BNode()
        graph.add((reified, RDF.type, OWL.Axiom))
        graph.add((reified, OWL.annotatedSource, triple[0]))
        graph.add((reified, OWL.annotatedProperty, triple[1]))
        graph.add((reified, OWL.annotatedTarget, triple[2]))
        for key, value in annotations.items():
            graph.add((reified, URIRef(key), value if hasattr(value, "n3") else Literal(value)))


# ---------------------------------------------------------------------------
# SPARQL (Translations)
# ---------------------------------------------------------------------------


class UntranslatableConcept(ValueError):
    ''' Fall-back in case it cannot be mapped. (should not arise for anything in ALCQI)'''
    """The concept needs a domain for ?x (e.g. ¬C on its own) that the context does not give."""


def simplify(c: Concept) -> Concept:
    """Equivalence-preserving rewriting before translation.

    Negation is pushed through ¬¬, ⊤/⊥, number restrictions and ∀; ⊓/⊔ are flattened; and
    ∃ distributes over ⊔.
    """
    if isinstance(c, Not):
        inner = simplify(c.operand)
        if isinstance(inner, Not):
            return inner.operand
        if inner == TOP:
            return BOTTOM
        if inner == BOTTOM:
            return TOP
        if isinstance(inner, AtMost):
            return AtLeast(inner.n + 1, inner.prop, inner.filler)
        if isinstance(inner, AtLeast):
            return BOTTOM if inner.n == 0 else AtMost(inner.n - 1, inner.prop, inner.filler)
        if isinstance(inner, Only):
            return simplify(Some(inner.prop, Not(inner.filler)))
        if isinstance(inner, Or) and not _binds(inner):
            return simplify(and_(*(Not(o) for o in inner.operands)))
        return Not(inner)
    if isinstance(c, And):
        return and_(*(simplify(o) for o in c.operands))
    if isinstance(c, Or):
        return or_(*(simplify(o) for o in c.operands))
    if isinstance(c, Some):
        filler = simplify(c.filler)
        if isinstance(filler, Or):
            # ∃r.(C ⊔ D) ≡ ∃r.C ⊔ ∃r.D: each UNION branch joins r with one class, which Ontop
            # pushes into the SQL of r (a union filler makes it materialize r, e.g. laterRole).
            return or_(*(simplify(Some(c.prop, o)) for o in filler.operands))
        return BOTTOM if filler == BOTTOM else Some(c.prop, filler)
    if isinstance(c, Only):
        filler = simplify(c.filler)
        return TOP if filler == TOP else Only(c.prop, filler)
    if isinstance(c, AtLeast):
        filler = simplify(c.filler)
        if c.n == 0:
            return TOP
        return BOTTOM if filler == BOTTOM else AtLeast(c.n, c.prop, filler)
    if isinstance(c, AtMost):
        filler = simplify(c.filler)
        return TOP if filler == BOTTOM else AtMost(c.n, c.prop, filler)
    if isinstance(c, Exactly):
        filler = simplify(c.filler)
        if c.n == 0:
            return simplify(AtMost(0, c.prop, filler))
        return BOTTOM if filler == BOTTOM else Exactly(c.n, c.prop, filler)
    return c


def _binds(c: Concept) -> bool:
    """Whether tau_x(c) selects its own ?x (and so can stand alone)."""
    if isinstance(c, (Named, Some, AtLeast, Exactly)) or c == BOTTOM:
        return True
    if isinstance(c, Defined):
        return _binds(simplify(c.definition))
    if isinstance(c, And):
        return any(_binds(o) for o in c.operands)
    if isinstance(c, Or):
        return all(_binds(o) for o in c.operands)
    return False  # ⊤, ¬, ∀, ≤


class Translator:
    """Compositional translation"""

    def __init__(self):
        self._counter = itertools.count()

    def fresh(self, base: str = "y") -> str:
        return f"?{base}{next(self._counter)}"

    def pattern(self, c: Concept, x: str = "?x", bound: bool = False) -> str:
        c = simplify(c)
        if not bound and not _binds(c):
            raise UntranslatableConcept(f"{dl(c)} does not select its own individuals; conjoin it with a concept that does.")
        return self._tau(c, x, bound)

    def _edge(self, p: PropertyExpr, x: str, y: str) -> str:
        if isinstance(p, Inverse):
            return f"{y} <{p.prop.iri}> {x} ."
        return f"{x} <{p.iri}> {y} ."

    def _tau(self, c: Concept, x: str, bound: bool) -> str:
        if isinstance(c, Named):
            return f"{x} a <{c.iri}> ."
        if isinstance(c, Defined):
            return self.pattern(c.definition, x, bound)
        if c == TOP:
            return ""
        if c == BOTTOM:
            return "FILTER(false)"
        if isinstance(c, And):
            binding = [o for o in c.operands if _binds(o)]
            filtering = [o for o in c.operands if not _binds(o)]
            parts = [self._tau(o, x, bound) for o in binding]
            parts += [self._tau(o, x, True) for o in filtering]
            return "\n".join(p for p in parts if p)
        if isinstance(c, Or):
            return "\nUNION\n".join("{\n" + _indent(self.pattern(o, x, False)) + "\n}" for o in c.operands)
        if isinstance(c, Not):
            return "MINUS {\n" + _indent(self.pattern(c.operand, x, False)) + "\n}"
        if isinstance(c, Some):
            y = self.fresh()
            return "\n".join(p for p in (self._edge(c.prop, x, y), self.pattern(c.filler, y, True)) if p)
        if isinstance(c, Only):
            y = self.fresh()
            body = "\n".join(p for p in (self._edge(c.prop, x, y), self.pattern(Not(c.filler), y, True)) if p)
            return "MINUS {\n" + _indent(body) + "\n}"
        if isinstance(c, (AtLeast, Exactly)):
            y = self.fresh()
            body = "\n".join(p for p in (self._edge(c.prop, x, y), self.pattern(c.filler, y, True)) if p)
            op = ">=" if isinstance(c, AtLeast) else "="
            return (f"{{ SELECT {x} WHERE {{\n{_indent(body)}\n}} GROUP BY {x} "
                    f"HAVING (COUNT(DISTINCT {y}) {op} {c.n}) }}")
        if isinstance(c, AtMost):
            y, count = self.fresh(), self.fresh("n")
            body = "\n".join(p for p in (self._edge(c.prop, x, y), self.pattern(c.filler, y, True)) if p)
            return (f"OPTIONAL {{ {{ SELECT {x} (COUNT(DISTINCT {y}) AS {count}) WHERE {{\n{_indent(body)}\n}} "
                    f"GROUP BY {x} }} }}\nFILTER(!BOUND({count}) || {count} <= {c.n})")
        raise TypeError(f"Cannot translate {c!r}")


def _indent(text: str, by: str = "    ") -> str:
    return "\n".join(by + line if line else line for line in text.splitlines())


def violations(axiom: Axiom) -> Optional[Concept]:
    """The concept whose instances are counterexamples (C ⊓ ¬D); None if it is trivially empty."""
    if isinstance(axiom, SubClassOf):
        target = simplify(and_(axiom.sub, Not(axiom.sup)))
    elif isinstance(axiom, EquivalentClasses):
        target = simplify(or_(and_(axiom.left, Not(axiom.right)), and_(axiom.right, Not(axiom.left))))
    else:
        return None  # NonEmpty has witnesses, not counterexamples.
    return None if target == BOTTOM else target


def counterexample_pattern(axiom: Axiom, x: str = "?x", translator: Optional[Translator] = None) -> Optional[str]:
    """tau_x(C ⊓ ¬D) as a group graph pattern; None if the axiom holds trivially."""
    target = violations(axiom)
    if target is None:
        return None
    return (translator or Translator()).pattern(target, x)
