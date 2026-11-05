"""Write the OCPQ BindingBoxTree equivalents of the ORO scenario queries Q1-Q4.

Each tree is written as <out>/<QID>/ocpq-tree.json (the layout `ocpq_cli bench` expects, and the
same JSON the OCPQ editor exports, so the trees can also be opened in the OCPQ UI).

Every query is phrased as a constraint on the root box, so the counterexamples are exactly the
root situations that violate it.
"""

import argparse
import json
from pathlib import Path

# ORO compares timestamps strictly (FILTER(?t1 > ?t2)); OCPQ's TimeBetweenEvents bounds are
# inclusive at microsecond resolution, so a 1 microsecond minimum gives the strict comparison.
STRICTLY_LATER = 1e-6


def event_types(ocel_path: Path) -> list:
    with open(ocel_path) as f:
        return [t["name"] for t in json.load(f)["eventTypes"]]


def box(event_vars=None, object_vars=None, filters=(), constraints=()):
    return {
        "newEventVars": {str(k): v for k, v in (event_vars or {}).items()},
        "newObjectVars": {str(k): v for k, v in (object_vars or {}).items()},
        "filters": list(filters),
        "sizeFilters": [],
        "constraints": list(constraints),
    }


def o2e(obj, ev):
    return {"type": "O2E", "object": obj, "event": ev, "qualifier": None}


def time_between(from_ev, to_ev, min_s=None, max_s=None):
    return {"type": "TimeBetweenEvents", "from_event": from_ev, "to_event": to_ev,
            "min_seconds": min_s, "max_seconds": max_s}


def num_childs(child, min_n=None, max_n=None):
    return {"type": "SizeFilter",
            "filter": {"type": "NumChilds", "child_name": child, "min": min_n, "max": max_n}}


def tree(root, child):
    return {"nodes": [{"Box": [root, [1]]}, {"Box": [child, []]}], "edgeNames": [[[0, 1], "A"]]}


def any_child(child):
    return {"type": "ANY", "child_names": [child]}


def no_other_event(otype, activity, all_types, later):
    """(o, e) with e of `activity`, violated iff o has another event strictly earlier (later=False)
    or strictly later (later=True) than e."""
    root = box(event_vars={0: [activity]}, object_vars={0: [otype]}, filters=[o2e(0, 0)],
               constraints=[num_childs("A", max_n=0)])
    order = time_between(0, 1, STRICTLY_LATER) if later else time_between(1, 0, STRICTLY_LATER)
    child = box(event_vars={1: all_types}, filters=[o2e(0, 1), order])
    return tree(root, child)


def exactly_one_of(otype, activities):
    root = box(object_vars={0: [otype]}, constraints=[num_childs("A", min_n=1, max_n=1)])
    child = box(event_vars={0: activities}, filters=[o2e(0, 0)])
    return tree(root, child)


def budget(otype, activities, n):
    """budg(ot, activities, n): o is violated iff it has more than n events of the activities."""
    root = box(object_vars={0: [otype]}, constraints=[num_childs("A", max_n=n)])
    child = box(event_vars={0: activities}, filters=[o2e(0, 0)])
    return tree(root, child)


def carry_eventual(otype, sources, targets):
    """carr(ot, sources, targets): (o, e) with e a target event, violated iff no source event of o
    lies strictly before e."""
    root = box(event_vars={0: targets}, object_vars={0: [otype]}, filters=[o2e(0, 0)],
               constraints=[num_childs("A", min_n=1)])
    child = box(event_vars={1: sources}, filters=[o2e(0, 1), time_between(1, 0, STRICTLY_LATER)])
    return tree(root, child)


def carry_next(otype, sources, targets, all_types):
    """carr_next(ot, sources, targets): (o, e) with e a target event, violated iff no source event
    s of o lies strictly before e with no other event of o strictly between s and e."""
    root = box(event_vars={0: targets}, object_vars={0: [otype]}, filters=[o2e(0, 0)],
               constraints=[any_child("A")])
    source = box(event_vars={1: sources}, filters=[o2e(0, 1), time_between(1, 0, STRICTLY_LATER)],
                 constraints=[num_childs("B", max_n=0)])
    between = box(event_vars={2: all_types},
                  filters=[o2e(0, 2), time_between(1, 2, STRICTLY_LATER), time_between(2, 0, STRICTLY_LATER)])
    return {"nodes": [{"Box": [root, [1]]}, {"Box": [source, [2]]}, {"Box": [between, []]}],
            "edgeNames": [[[0, 1], "A"], [[1, 2], "B"]]}


def carry_forward(otype, sources, targets):
    """carr_forward(ot, sources, targets): (o, s) with s a source event, violated iff no target
    event of o lies strictly after s."""
    root = box(event_vars={0: sources}, object_vars={0: [otype]}, filters=[o2e(0, 0)],
               constraints=[num_childs("A", min_n=1)])
    child = box(event_vars={1: targets}, filters=[o2e(0, 1), time_between(0, 1, STRICTLY_LATER)])
    return tree(root, child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True, help="input/data directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    logistics_types = event_types(args.data / "ContainerLogistics.json")
    bpi_types = event_types(args.data / "bpi17_ocel.json")
    queries = {
        # init(Forklift, Weigh): every forklift Weigh role is its first role.
        "Q1": no_other_event("Forklift", "Weigh", logistics_types, later=False),
        # Every offer plays exactly one Accepted/Cancelled/Refused role.
        "Q2": exactly_one_of("offer", ["Accepted_complete", "Cancelled_complete", "Refused_complete"]),
        # clos(Offer, Sent (mail and online)) and clos(Offer, Returned): such roles are last roles.
        "Q3": no_other_event("offer", "Sent (mail and online)_complete", bpi_types, later=True),
        "Q4": no_other_event("offer", "Returned_complete", bpi_types, later=True),
    }
    # Revised Section 7 scenarios. "Sent" is both send channels, as in the ORO runs; the
    # dropped clos(Offer, Returned) query keeps its tree under Q4old.
    sent = ["Sent (mail and online)_complete", "Sent (online only)_complete"]
    queries["Q4old"] = queries.pop("Q4")
    queries.update({
        # budg(TransportDocument, {Reschedule Container, Depart}, 2)
        "Q4": budget("Transport Document", ["Reschedule Container", "Depart"], 2),
        "Q5": carry_next("offer", sent, ["Returned_complete"], bpi_types),
        "Q5e": carry_eventual("offer", sent, ["Returned_complete"]),
        "Q6": carry_forward("offer", sent, ["Returned_complete"]),
    })
    for qid, t in queries.items():
        (args.out / qid).mkdir(parents=True, exist_ok=True)
        (args.out / qid / "ocpq-tree.json").write_text(json.dumps(t, indent=2))
        print(f"wrote {args.out / qid / 'ocpq-tree.json'}")


if __name__ == "__main__":
    main()
