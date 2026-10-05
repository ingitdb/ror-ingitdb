#!/usr/bin/env python3
"""Offline, recordwise ROR JSON -> faithful deterministic SQLite projection."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile

LIMIT_DB = 256 * 1024**2
LIMIT_RSS = 512 * 1024**2
LIMIT_METADATA = 2 * 1024**2
# https://ror.readme.io/docs/identifier; Crockford alphabet excludes i,l,o,u.
ROR_URL = re.compile(r"https://ror\.org/0[0-9a-hjkmnp-tv-z]{6}[0-9]{2}")
CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
SCHEMA = """
CREATE TABLE organizations(id TEXT PRIMARY KEY NOT NULL, status TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE locations(organization_id TEXT NOT NULL REFERENCES organizations(id), ordinal INTEGER NOT NULL,
 geonames_id INTEGER, geonames_details_json TEXT NOT NULL,
 PRIMARY KEY(organization_id,ordinal)) WITHOUT ROWID;
CREATE TABLE relationships(organization_id TEXT NOT NULL REFERENCES organizations(id), ordinal INTEGER NOT NULL,
 type TEXT NOT NULL, id TEXT NOT NULL, label TEXT NOT NULL,
 PRIMARY KEY(organization_id,ordinal)) WITHOUT ROWID;
CREATE INDEX locations_geonames_id ON locations(geonames_id);
CREATE INDEX relationships_id ON relationships(id);
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def finite_float(token):
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite or overflowing JSON number")
    return value


def reject_constant(token):
    raise ValueError(f"non-JSON numeric constant: {token}")


def validate_ror_id(value):
    if not isinstance(value, str) or ROR_URL.fullmatch(value) is None:
        raise ValueError("invalid canonical native ROR identifier URL")
    unique = value.removeprefix("https://ror.org/")
    number = 0
    for character in unique[1:7]:
        number = number * 32 + CROCKFORD.index(character)
    # ROR's documented generator uses ISO 7064 MOD 97-10 over the decoded value.
    if int(unique[-2:]) != 98 - (number * 100) % 97:
        raise ValueError("invalid native ROR identifier checksum")


def field(container, name, datatype, nonempty=False):
    value = container.get(name)
    if not isinstance(value, datatype) or (nonempty and not value):
        raise ValueError(f"invalid projected source field: {name}")
    return value


def validate_record(record):
    validate_ror_id(field(record, "id", str))
    field(record, "status", str, nonempty=True)
    for location in field(record, "locations", list):
        if not isinstance(location, dict) or "geonames_id" not in location:
            raise ValueError("location must be an object with geonames_id")
        gid = location["geonames_id"]
        if gid is not None and (type(gid) is not int or gid <= 0):
            raise ValueError("GeoNames ID must be native positive integer or null")
        field(location, "geonames_details", dict)
    for relationship in field(record, "relationships", list):
        if not isinstance(relationship, dict):
            raise ValueError("relationship must be an object")
        validate_ror_id(field(relationship, "id", str))
        field(relationship, "type", str, nonempty=True)
        field(relationship, "label", str)


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def records(stream):
    """Strict JSON array parser, retaining only one record and a bounded read buffer."""
    decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_float=finite_float, parse_constant=reject_constant)
    buffer = ""
    eof = False

    def fill():
        nonlocal buffer, eof
        chunk = stream.read(65536)
        eof = not chunk
        buffer += chunk

    def whitespace():
        nonlocal buffer
        buffer = buffer.lstrip()
        while not buffer and not eof:
            fill()
            buffer = buffer.lstrip()

    whitespace()
    if not buffer.startswith("["):
        raise ValueError("source must be a JSON array")
    buffer = buffer[1:]
    whitespace()
    while not buffer.startswith("]"):
        while True:
            try:
                record, end = decoder.raw_decode(buffer)
                break
            except json.JSONDecodeError:
                if eof:
                    raise ValueError("invalid or truncated source JSON")
                fill()
                if len(buffer.encode("utf-8")) > 2 * 1024**2:
                    raise ValueError("source record exceeds 2 MiB safety bound")
        if not isinstance(record, dict):
            raise ValueError("source records must be objects")
        yield record
        buffer = buffer[end:]
        whitespace()
        if buffer.startswith("]"):
            break
        if not buffer.startswith(","):
            raise ValueError("missing record separator")
        buffer = buffer[1:]
        whitespace()
        if buffer.startswith("]"):
            raise ValueError("trailing comma")
    if not buffer.startswith("]"):
        raise ValueError("unterminated source array")
    buffer = buffer[1:]
    whitespace()
    if buffer:
        raise ValueError("trailing source content")


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def build(archive, pin, output):
    """Publish a complete staged bundle only after source, key and budget checks."""
    archive, output = Path(archive), Path(output)
    start = time.monotonic()
    if archive.stat().st_size != pin["archive_bytes"] or digest(archive) != pin["archive_sha256"]:
        raise ValueError("archive bytes/hash disagree with source pin")
    if archive.stat().st_size > 1024**3:
        raise ValueError("download ceiling exceeded")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("output already exists; build a new reviewed snapshot")
    stage = Path(tempfile.mkdtemp(prefix=".ror-build-", dir=output.parent))
    try:
        with zipfile.ZipFile(archive) as source:
            members = [m for m in source.infolist() if m.filename == pin["member"]]
            if len(members) != 1 or members[0].file_size != pin["member_bytes"]:
                raise ValueError("source member missing, duplicated or wrong size")
            member_hash = hashlib.sha256()
            with source.open(pin["member"]) as stream:
                while block := stream.read(1024**2):
                    member_hash.update(block)
            if member_hash.hexdigest() != pin["member_sha256"]:
                raise ValueError("source member hash mismatch")
            database = stage / "ror.sqlite"
            db = sqlite3.connect(database)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA journal_mode=OFF")
                db.execute("PRAGMA synchronous=OFF")
                db.execute("PRAGMA cache_size=-8192")
                db.execute("PRAGMA temp_store=FILE")
                db.executescript(SCHEMA)
                counts = {"organizations": 0, "locations": 0, "relationships": 0}
                statuses = {}
                with source.open(pin["member"]) as stream:
                    for record in records(io.TextIOWrapper(stream, encoding="utf-8")):
                        validate_record(record)
                        native_id, status = record["id"], record["status"]
                        db.execute("INSERT INTO organizations VALUES (?,?,?)", (native_id, status, canonical(record)))
                        counts["organizations"] += 1
                        statuses[status] = statuses.get(status, 0) + 1
                        for ordinal, location in enumerate(record["locations"]):
                            gid = location["geonames_id"]
                            db.execute("INSERT INTO locations VALUES (?,?,?,?)",
                                       (native_id, ordinal, gid, canonical(location["geonames_details"])))
                            counts["locations"] += 1
                        for ordinal, relationship in enumerate(record["relationships"]):
                            db.execute("INSERT INTO relationships VALUES (?,?,?,?,?)",
                                       (native_id, ordinal, relationship["type"], relationship["id"], relationship["label"]))
                            counts["relationships"] += 1
                        if counts["organizations"] % 1000 == 0:
                            if rss_bytes() > LIMIT_RSS or time.monotonic() - start > 1800:
                                raise ValueError("RSS or elapsed ceiling exceeded")
                            if database.stat().st_size > LIMIT_DB:
                                raise ValueError(f"provider SQLite ceiling exceeded at {counts['organizations']} organizations: {database.stat().st_size} bytes")
                if counts["organizations"] != pin["organizations"]:
                    raise ValueError("full organization count disagrees with source pin")
                db.commit()
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or list(db.execute("PRAGMA foreign_key_check")):
                    raise ValueError("SQLite integrity/key closure failed")
                ids = stage / "ror-geonames-ids.txt"
                with ids.open("w", encoding="ascii", newline="\n") as stream:
                    for (gid,) in db.execute("SELECT DISTINCT geonames_id FROM locations WHERE geonames_id IS NOT NULL ORDER BY geonames_id"):
                        stream.write(str(gid) + "\n")
                counts.update({
                    "unique_geonames_ids": db.execute("SELECT count(DISTINCT geonames_id) FROM locations").fetchone()[0],
                    "null_location_ids": db.execute("SELECT count(*) FROM locations WHERE geonames_id IS NULL").fetchone()[0],
                    "organizations_without_locations": db.execute("SELECT count(*) FROM organizations o WHERE NOT EXISTS(SELECT 1 FROM locations l WHERE l.organization_id=o.id)").fetchone()[0],
                    "organizations_with_multiple_locations": db.execute("SELECT count(*) FROM (SELECT organization_id FROM locations GROUP BY organization_id HAVING count(*)>1)").fetchone()[0],
                    "relationship_targets_absent": db.execute("SELECT count(*) FROM relationships r WHERE NOT EXISTS(SELECT 1 FROM organizations o WHERE o.id=r.id)").fetchone()[0],
                })
            finally:
                db.close()
        manifest = {
            "source": pin,
            "parser_sha256": digest(__file__),
            "projection": {"logical_recordset": "ROR organizations", "physical_tables": ["organizations", "locations", "relationships"],
                           "raw_json": "complete source record, canonical JSON serialization; no field loss", "array_ordinals": "zero-based original order",
                           "status_policy": "all statuses", "geonames_ids": "numeric sorted distinct positive native IDs, LF", "user_contract": "pending dedicated acceptance"},
            "counts": counts, "statuses": statuses,
            "outputs": {name: {"sha256": digest(stage / name), "bytes": (stage / name).stat().st_size} for name in ("ror.sqlite", "ror-geonames-ids.txt")},
        }
        attribution = Path(__file__).resolve().parents[1] / "DATA-LICENSE.md"
        shutil.copyfile(attribution, stage / "DATA-LICENSE.md")
        manifest["outputs"]["DATA-LICENSE.md"] = {"sha256": digest(attribution), "bytes": attribution.stat().st_size}
        (stage / "snapshot.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        temporary_bytes = archive.stat().st_size + sum(p.stat().st_size for p in stage.iterdir())
        if database.stat().st_size > LIMIT_DB or rss_bytes() > LIMIT_RSS or temporary_bytes > 8 * 1024**3 or time.monotonic() - start > 1800:
            raise ValueError("full artifact resource ceiling exceeded")
        if (stage / "snapshot.json").stat().st_size > LIMIT_METADATA:
            raise ValueError("metadata ceiling exceeded")
        measurements = {"elapsed_seconds": time.monotonic() - start, "peak_rss_bytes": rss_bytes(), "download_bytes": archive.stat().st_size,
                        "temporary_bytes": temporary_bytes, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.platform()}
        (stage / "resources.json").write_text(json.dumps(measurements, indent=2, sort_keys=True) + "\n")
        os.rename(stage, output)
        return manifest, measurements
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--pin", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    manifest, measurements = build(args.archive, json.loads(args.pin.read_text()), args.out)
    print(json.dumps({"counts": manifest["counts"], "resources": measurements}, indent=2))


if __name__ == "__main__":
    main()
