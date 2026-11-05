"""Checks that read the raw OCEL 2.0 JSON instead of the VKG.

The VKG's object table is built from the E2O relation, so objects without any event
(e.g. orphan documents) never reach Ontop. These checks see them.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Tuple

import pandas as pd


@dataclass
class TransformationCoverage:
    """Is ``source -> target`` at ``activity`` a necessary transformation?

    (a) every source object is transformed into a target object, and
    (b) every target object is created from a source object.
    """

    source_otype: str
    target_otype: str
    activity: str
    sources: int
    targets: int
    untransformed_sources: pd.DataFrame
    uncreated_targets: pd.DataFrame
    event_pairs: int
    o2o_pairs: Optional[int]
    pairs_in_only_one: Optional[int]
    log_end: pd.Timestamp

    def report(self) -> None:
        print(f"Log end: {self.log_end}")
        print(f"(a) {self.sources - len(self.untransformed_sources)}/{self.sources} {self.source_otype} "
              f"objects are transformed into a {self.target_otype}")
        print(f"(b) {self.targets - len(self.uncreated_targets)}/{self.targets} {self.target_otype} "
              f"objects are created from a {self.source_otype}")
        if self.o2o_pairs is not None:
            print(f"Transformation pairs from events: {self.event_pairs}, from O2O: {self.o2o_pairs}, "
                  f"in only one of the two: {self.pairs_in_only_one}")


def transformation_coverage(
    ocel_json_path: str,
    source_otype: str,
    target_otype: str,
    activity: str,
    o2o_qualifier: Optional[str] = None,
) -> TransformationCoverage:
    """Check (a) and (b) of TransformationCoverage on the raw log.

    The returned tables summarise each offending object (event count, first/last activity,
    days before the log ends). With ``o2o_qualifier`` the event-based pairs are compared with
    the O2O relation, and uncreated targets are flagged if an O2O link names a source.
    """
    log = json.loads(Path(ocel_json_path).read_text())
    object_types = {o["id"]: o["type"] for o in log["objects"]}
    e2o = pd.DataFrame(
        [(e["id"], e["type"], pd.Timestamp(e["time"]), r["objectId"])
         for e in log["events"] for r in e.get("relationships") or []],
        columns=["event", "activity", "timestamp", "obj"],
    )
    log_end = e2o["timestamp"].max()

    sources = {o for o, t in object_types.items() if t == source_otype}
    targets = {o for o, t in object_types.items() if t == target_otype}
    if not sources or not targets:
        raise ValueError(f"No objects of type '{source_otype}' or '{target_otype}'. "
                         f"Types in the log: {sorted(set(object_types.values()))}")

    pairs: Set[Tuple[str, str]] = set()
    for _, objs in e2o[e2o["activity"] == activity].groupby("event")["obj"]:
        pairs |= {(s, t) for s in objs if s in sources for t in objs if t in targets}

    def summary(obj_ids) -> pd.DataFrame:
        by_obj = e2o[e2o["obj"].isin(obj_ids)].sort_values("timestamp").groupby("obj")
        df = pd.DataFrame({
            "events": by_obj.size(),
            "first_activity": by_obj["activity"].first(),
            "last_activity": by_obj["activity"].last(),
            "last_timestamp": by_obj["timestamp"].last(),
        }).reindex(sorted(obj_ids))
        df["events"] = df["events"].fillna(0).astype(int)
        df["days_before_log_end"] = (log_end - df["last_timestamp"]).dt.total_seconds() / 86400
        return df

    untransformed = summary(sources - {s for s, _ in pairs})
    uncreated = summary(targets - {t for _, t in pairs})

    o2o_pairs = None
    if o2o_qualifier is not None:
        o2o_pairs = {
            (o["id"], r["objectId"])
            for o in log["objects"] if o["id"] in sources
            for r in o.get("relationships") or [] if r.get("qualifier") == o2o_qualifier
        }
        linked = {t for _, t in o2o_pairs}
        uncreated["o2o_linked_source"] = [t in linked for t in uncreated.index]
    # Objects without events only carry attribute timestamps; show the latest one.
    attribute_times = {
        o["id"]: max((pd.Timestamp(a["time"]) for a in o.get("attributes") or []), default=pd.NaT)
        for o in log["objects"] if o["id"] in uncreated.index
    }
    uncreated["latest_attribute_time"] = uncreated.index.map(attribute_times)
    uncreated["attribute_after_log_end"] = uncreated["latest_attribute_time"] > log_end

    return TransformationCoverage(
        source_otype=source_otype,
        target_otype=target_otype,
        activity=activity,
        sources=len(sources),
        targets=len(targets),
        untransformed_sources=untransformed,
        uncreated_targets=uncreated,
        event_pairs=len(pairs),
        o2o_pairs=None if o2o_pairs is None else len(o2o_pairs),
        pairs_in_only_one=None if o2o_pairs is None else len(pairs ^ o2o_pairs),
        log_end=log_end,
    )
