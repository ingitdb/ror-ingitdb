#!/usr/bin/env python3
"""Independent character-framing audit of every source record and projection row."""
import argparse
import io
import json
from pathlib import Path
import sqlite3
import zipfile


def framed_objects(stream):
    """Frame top-level objects by depth; independent of importer's raw_decode loop."""
    depth = 0
    quoted = escaped = False
    record = []
    while chunk := stream.read(65536):
        for char in chunk:
            if depth:
                record.append(char)
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                elif char == '"':
                    quoted = True
                elif char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                    if depth == 0:
                        yield json.loads("".join(record))
                        record = []
            elif char == "{":
                depth = 1
                record = [char]
    if depth:
        raise ValueError("unfinished object")


def audit(archive, member, database):
    counts = {"organizations": 0, "locations": 0, "relationships": 0}
    with sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True) as db:
        with zipfile.ZipFile(archive) as source, source.open(member) as stream:
            for record in framed_objects(io.TextIOWrapper(stream, encoding="utf-8")):
                native_id = record["id"]
                row = db.execute("SELECT status,raw_json FROM organizations WHERE id=?", (native_id,)).fetchone()
                if row is None or row[0] != record["status"] or json.loads(row[1]) != record:
                    raise ValueError(f"full source JSON/status mismatch: {native_id}")
                locations = db.execute("SELECT ordinal,geonames_id,geonames_details_json FROM locations WHERE organization_id=? ORDER BY ordinal", (native_id,)).fetchall()
                expected = [(n, r["geonames_id"], r["geonames_details"]) for n, r in enumerate(record["locations"])]
                if [(n, gid, json.loads(raw)) for n, gid, raw in locations] != expected:
                    raise ValueError(f"location values/order mismatch: {native_id}")
                relationships = db.execute("SELECT ordinal,type,id,label FROM relationships WHERE organization_id=? ORDER BY ordinal", (native_id,)).fetchall()
                if relationships != [(n, r["type"], r["id"], r["label"]) for n, r in enumerate(record["relationships"])]:
                    raise ValueError(f"relationship values/order mismatch: {native_id}")
                counts["organizations"] += 1
                counts["locations"] += len(expected)
                counts["relationships"] += len(relationships)
        for table, expected in counts.items():
            if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != expected:
                raise ValueError(f"extra/missing rows in {table}")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.archive, args.member, args.database), indent=2))
