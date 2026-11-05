"""Convert an OCEL 2.0 JSON log to the OCEL 2.0 SQLite layout read by TOTeM's vendored ocpa.

Only the standard library is used. Timestamps are written as naive UTC text with whole seconds
("%Y-%m-%d %H:%M:%S"), the only format TOTeM's mine_totem() can parse (it calls
datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")). E2O/O2O references to unknown objects are
dropped, as the OCEL 2.0 SQLite foreign keys would reject them.

--no-event-attributes leaves event attributes out: ocpa's importer merges the per-type event
tables on the event id and fails when two event types share an attribute name (e.g. P2P's
"resource"/"lifecycle", BPI 2017's "case:*"). TOTeM does not use event attributes.
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def to_utc_seconds(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def type_map(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", name.title()) or "Type"


def sql_type(value) -> str:
    return "REAL" if isinstance(value, (int, float)) and not isinstance(value, bool) else "TEXT"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--no-event-attributes", action="store_true")
    args = parser.parse_args()

    with open(args.json_path) as f:
        log = json.load(f)
    args.sqlite_path.unlink(missing_ok=True)
    db = sqlite3.connect(args.sqlite_path)

    def unique_maps(types):
        maps, used = {}, set()
        for t in types:
            base = m = type_map(t["name"])
            i = 1
            while m in used:
                i += 1
                m = f"{base}{i}"
            used.add(m)
            maps[t["name"]] = m
        return maps

    ev_maps = unique_maps(log["eventTypes"])
    ob_maps = unique_maps(log["objectTypes"])
    db.execute("CREATE TABLE event_map_type (ocel_type TEXT, ocel_type_map TEXT)")
    db.execute("CREATE TABLE object_map_type (ocel_type TEXT, ocel_type_map TEXT)")
    db.executemany("INSERT INTO event_map_type VALUES (?, ?)", ev_maps.items())
    db.executemany("INSERT INTO object_map_type VALUES (?, ?)", ob_maps.items())
    db.execute("CREATE TABLE event (ocel_id TEXT, ocel_type TEXT)")
    db.execute("CREATE TABLE object (ocel_id TEXT, ocel_type TEXT)")
    db.execute("CREATE TABLE event_object (ocel_event_id TEXT, ocel_object_id TEXT, ocel_qualifier TEXT)")
    db.execute("CREATE TABLE object_object (ocel_source_id TEXT, ocel_target_id TEXT, ocel_qualifier TEXT)")

    # Per-type tables carry the attributes declared for the type, plus any found on instances.
    def attr_columns(types, instances, key):
        cols = {t["name"]: {a["name"]: ("REAL" if a.get("type") in ("float", "integer") else "TEXT")
                            for a in t.get("attributes", [])} for t in types}
        for inst in instances:
            for a in inst.get("attributes") or []:
                cols[inst[key]].setdefault(a["name"], sql_type(a["value"]))
        return cols

    ev_cols = attr_columns(log["eventTypes"], log["events"], "type")
    if args.no_event_attributes:
        ev_cols = {name: {} for name in ev_cols}
    ob_cols = attr_columns(log["objectTypes"], log["objects"], "type")
    for name, cols in ev_cols.items():
        extra = "".join(f', "{c}" {t}' for c, t in cols.items())
        db.execute(f'CREATE TABLE "event_{ev_maps[name]}" (ocel_id TEXT, ocel_time TEXT{extra})')
    for name, cols in ob_cols.items():
        extra = "".join(f', "{c}" {t}' for c, t in cols.items())
        db.execute(f'CREATE TABLE "object_{ob_maps[name]}" (ocel_id TEXT, ocel_time TEXT, '
                   f'ocel_changed_field TEXT{extra})')

    object_ids = {o["id"] for o in log["objects"]}
    dropped = 0
    for o in log["objects"]:
        db.execute("INSERT INTO object VALUES (?, ?)", (o["id"], o["type"]))
        table = f'"object_{ob_maps[o["type"]]}"'
        db.execute(f"INSERT INTO {table} (ocel_id, ocel_time) VALUES (?, ?)",
                   (o["id"], "1970-01-01 00:00:00"))
        for a in o.get("attributes") or []:
            db.execute(f'INSERT INTO {table} (ocel_id, ocel_time, ocel_changed_field, "{a["name"]}") '
                       f"VALUES (?, ?, ?, ?)", (o["id"], to_utc_seconds(a["time"]), a["name"], a["value"]))
        for r in o.get("relationships") or []:
            if r["objectId"] not in object_ids:
                dropped += 1
                continue
            db.execute("INSERT INTO object_object VALUES (?, ?, ?)", (o["id"], r["objectId"], r.get("qualifier")))

    for e in log["events"]:
        db.execute("INSERT INTO event VALUES (?, ?)", (e["id"], e["type"]))
        attrs = {} if args.no_event_attributes else {a["name"]: a["value"] for a in e.get("attributes") or []}
        cols = ", ".join(['ocel_id', 'ocel_time', *[f'"{c}"' for c in attrs]])
        marks = ", ".join("?" * (2 + len(attrs)))
        db.execute(f'INSERT INTO "event_{ev_maps[e["type"]]}" ({cols}) VALUES ({marks})',
                   (e["id"], to_utc_seconds(e["time"]), *attrs.values()))
        for r in e.get("relationships") or []:
            if r["objectId"] not in object_ids:
                dropped += 1
                continue
            db.execute("INSERT INTO event_object VALUES (?, ?, ?)", (e["id"], r["objectId"], r.get("qualifier")))

    db.commit()
    db.close()
    print(f"{args.json_path.name}: {len(log['events'])} events, {len(log['objects'])} objects, "
          f"{dropped} dangling references dropped -> {args.sqlite_path}")


if __name__ == "__main__":
    main()
