#!/usr/bin/env python3
"""Package the reviewed native SQLite without changing source generation or semantics."""
import argparse
from contextlib import closing
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time

REPOSITORY = "https://github.com/ingitdb/ror-ingitdb"
METADATA_LIMIT = 2 * 1024**2
FILE_LIMIT = 25 * 1024**2
ENCODED_LIMIT = 512 * 1024**2
DECODED_LIMIT = 2 * 1024**3
SOURCE_LIMIT = 256 * 1024**2
CHUNK_BYTES = 20 * 1024**2
PREFIX = "artifacts/ror-v2.13/native/ror.sqlite.gz.part-"
FILES = ["model/ror.modelspec.json", "model/ror.modelspec.hcl", "model/ror.meaning.yaml", "source/ror-v2.13.json", "DATA-LICENSE.md"]
loader = importlib.util.spec_from_file_location("source_importer", Path(__file__).with_name("import_ror.py"))
source_importer = importlib.util.module_from_spec(loader)
loader.loader.exec_module(source_importer)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(root, relative):
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", relative):
        raise ValueError("invalid artifact relative path")
    if any(part in ("", ".", "..") for part in relative.split("/")) or PurePosixPath(relative).is_absolute():
        raise ValueError("unsafe artifact relative path")
    path = Path(root) / relative
    cursor = Path(root)
    for part in relative.split("/"):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("artifact path uses symlink")
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("artifact path escapes root or uses symlink")
    return path


def read_json(path):
    if not Path(path).is_file() or Path(path).stat().st_size > METADATA_LIMIT:
        raise ValueError("metadata exceeds 2 MiB")
    return decode_metadata(Path(path).read_bytes())


def decode_metadata(data):
    if not isinstance(data, bytes) or len(data) > METADATA_LIMIT:
        raise ValueError("authority metadata exceeds 2 MiB or is not bytes")
    return json.loads(data.decode("utf-8"), object_pairs_hook=source_importer.unique_object,
                      parse_float=source_importer.finite_float, parse_constant=source_importer.reject_constant)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(data.encode()) > METADATA_LIMIT:
        raise ValueError("metadata exceeds 2 MiB")
    path.write_text(data)


def git_blob(root, revision, path):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("generator/source revision must be an immutable commit")
    kind = subprocess.run(["git", "-C", str(root), "cat-file", "-t", revision], check=True, capture_output=True).stdout.strip()
    if kind != b"commit":
        raise ValueError("revision does not identify a Git commit")
    tree = subprocess.run(["git", "-C", str(root), "ls-tree", revision, "--", path], check=True, capture_output=True).stdout
    fields = tree.rstrip(b"\n").split(b"\t")
    if len(fields) != 2 or fields[1] != path.encode() or fields[0].split()[:2] not in ([b"100644", b"blob"], [b"100755", b"blob"]):
        raise ValueError("authority path must be a regular committed file")
    size = int(subprocess.run(["git", "-C", str(root), "cat-file", "-s", f"{revision}:{path}"], check=True, capture_output=True).stdout)
    if size > METADATA_LIMIT:
        raise ValueError("authority metadata exceeds 2 MiB")
    return subprocess.run(["git", "-C", str(root), "show", f"{revision}:{path}"], check=True, capture_output=True).stdout


def committed_generator(root, revision):
    script = "scripts/package_artifacts.py"
    if git_blob(root, revision, script) != safe_path(root, script).read_bytes():
        raise ValueError("packaging script differs from committed generator revision")
    return {"repository": REPOSITORY, "revision": revision, "script": script, "sha256": digest(safe_path(root, script))}


def model_key(root):
    model = read_json(safe_path(root, "model/ror.modelspec.json"))
    entity = model.get("entities", {}).get("organizations", {})
    prop = entity.get("properties", {}).get("id", {})
    if model.get("module", {}).get("name") != "ror" or entity.get("key") != ["id"] or prop.get("type") != "string" or prop.get("required") is not True:
        raise ValueError("model must declare organizations.id as required string native key")


def native_key(root, database, original):
    expected = original["snapshot"]["outputs"]["ror.sqlite"]
    if database.stat().st_size > SOURCE_LIMIT or database.stat().st_size != expected["bytes"] or digest(database) != expected["sha256"]:
        raise ValueError("SQLite differs from reviewed source-generation dataset")
    model_key(root)
    if read_json(safe_path(root, "source/ror-v2.13.json")) != original["snapshot"]["source"]:
        raise ValueError("source pin disagrees with original generation")
    with closing(sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("native SQLite integrity failure")
        columns = {row[1]: row for row in db.execute("PRAGMA table_info(organizations)")}
        if columns.get("id", (None,) * 6)[2:6] != ("TEXT", 1, None, 1):
            raise ValueError("native id must be TEXT NOT NULL PRIMARY KEY")
        duplicates = db.execute("SELECT count(*) FROM (SELECT id FROM organizations GROUP BY id HAVING count(*)>1)").fetchone()[0]
        if duplicates:
            raise ValueError("duplicate native ROR identifiers")
        count = 0
        for native, status, raw in db.execute("SELECT id,status,raw_json FROM organizations"):
            source_importer.validate_ror_id(native)
            record = json.loads(raw, object_pairs_hook=source_importer.unique_object,
                                parse_float=source_importer.finite_float, parse_constant=source_importer.reject_constant)
            if record.get("id") != native or record.get("status") != status:
                raise ValueError("native key/status disagrees with original source JSON")
            count += 1
        expected_count = original["snapshot"]["counts"]["organizations"]
        if count != expected_count or count != original["snapshot"]["source"]["organizations"]:
            raise ValueError("native organization count disagrees with full source")
    return {"module": "ror", "entity": "organizations", "property": "id", "namespace": "ROR:URL",
            "model": {"path": "model/ror.modelspec.json", "sha256": digest(safe_path(root, "model/ror.modelspec.json"))},
            "binding": {"path": "model/ror.meaning.yaml", "sha256": digest(safe_path(root, "model/ror.meaning.yaml"))},
            "dataset": {"path": "ror.sqlite", "sha256": expected["sha256"]}, "records": count, "duplicates": duplicates}


class ChunkWriter:
    def __init__(self, root, limit):
        if type(limit) is not int or not 0 < limit <= FILE_LIMIT:
            raise ValueError("chunk size exceeds per-file guard")
        self.root, self.limit = root, limit
        self.chunks, self.stream, self.size = [], None, 0
        self.hash, self.aggregate, self.total = None, hashlib.sha256(), 0

    def close_chunk(self):
        if self.stream:
            self.stream.close()
            self.chunks[-1].update(bytes=self.size, sha256=self.hash.hexdigest())
            self.stream = None

    def write(self, data):
        size = len(data)
        self.aggregate.update(data)
        self.total += size
        if self.total > ENCODED_LIMIT:
            raise ValueError("encoded stream ceiling exceeded")
        while data:
            if self.stream is None:
                relative = PREFIX + f"{len(self.chunks) + 1:04d}"
                path = safe_path(self.root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                self.stream = path.open("wb")
                self.chunks.append({"path": relative})
                self.size, self.hash = 0, hashlib.sha256()
            piece, data = data[:self.limit - self.size], data[self.limit - self.size:]
            self.stream.write(piece)
            self.hash.update(piece)
            self.size += len(piece)
            if self.size == self.limit:
                self.close_chunk()
        return size


def verify_bundle(root, *, source_revision=None, resolve=None):
    """Check reconstruction and immutable provenance; caller supplies source authority.

    resolve(repository, revision, path) returns bounded bytes from that immutable
    authority. This verifies associations, not semantic acceptance of a source.
    """
    if not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", source_revision) or not callable(resolve):
        raise ValueError("explicit immutable source revision and authority resolver required")
    snapshot = read_json(safe_path(root, "source/artifact-snapshot.json"))
    sqlite = snapshot["sqlite"]
    artifacts = snapshot["artifacts"]
    generator = snapshot["generator"]
    if generator.get("repository") != REPOSITORY or not re.fullmatch(r"[0-9a-f]{40}", generator.get("revision", "")):
        raise ValueError("generator must name immutable provider code revision")
    if generator.get("script") != "scripts/package_artifacts.py" or not re.fullmatch(r"[0-9a-f]{64}", generator.get("sha256", "")):
        raise ValueError("invalid immutable generator script/hash")
    code = resolve(REPOSITORY, generator["revision"], generator["script"])
    if not isinstance(code, bytes) or len(code) > METADATA_LIMIT or hashlib.sha256(code).hexdigest() != generator["sha256"]:
        raise ValueError("generator hash disagrees with immutable code authority")
    generation = snapshot["source_generation"]
    authority_bytes = resolve(REPOSITORY, source_revision, "source/validation.json")
    authority = decode_metadata(authority_bytes)
    if generation != {"repository": REPOSITORY, "revision": source_revision, "original_validation_sha256": hashlib.sha256(authority_bytes).hexdigest()}:
        raise ValueError("source generation disagrees with explicit immutable authority")
    if not isinstance(artifacts, list) or not 0 < len(artifacts) <= 10000:
        raise ValueError("invalid artifact list")
    pins = {}
    for artifact in artifacts:
        path = artifact["path"]
        safe_path(root, path)
        if path in pins or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
            raise ValueError("duplicate artifact path or malformed hash")
        pins[path] = artifact
        if path == "ror.sqlite":
            if artifact.get("kind") != "reconstructed":
                raise ValueError("native dataset must identify a reconstructed decoded artifact")
        else:
            file = safe_path(root, path)
            if not file.is_file() or type(artifact.get("bytes")) is not int or not 0 <= artifact["bytes"] <= (FILE_LIMIT if path.startswith("artifacts/") else METADATA_LIMIT):
                raise ValueError("artifact must be a bounded regular file")
            if file.stat().st_size != artifact["bytes"] or digest(file) != artifact["sha256"]:
                raise ValueError("artifact bytes/hash mismatch")
    chunks = sqlite["chunks"]
    if not isinstance(chunks, list) or not chunks or len(chunks) > 10000:
        raise ValueError("invalid chunk list")
    required = {*FILES, "source/validation.json", "ror.sqlite", *(chunk["path"] for chunk in chunks)}
    if set(pins) != required:
        raise ValueError("mandatory metadata/chunk artifact closure mismatch")
    metadata = {*FILES, "source/validation.json", "source/artifact-snapshot.json"}
    resource = safe_path(root, "source/artifact-packaging-resources.json")
    if resource.exists():
        read_json(resource)
        metadata.add("source/artifact-packaging-resources.json")
    # Later representation metadata is a separate exact pair, never inserted
    # into or admitted by the original native artifact snapshot.
    attachment_files = {"model/representations.json", "source/representation-attachment.json"}
    if any(safe_path(root, path).exists() for path in attachment_files):
        for path in attachment_files:
            file = safe_path(root, path)
            if not file.is_file() or file.stat().st_size > METADATA_LIMIT:
                raise ValueError("representation metadata requires its exact bounded regular pair")
        spec = importlib.util.spec_from_file_location("representation_generator", Path(__file__).with_name("generate_representations.py"))
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        generator.generate(check=True, root=root)
        metadata.update(attachment_files)
    if sum(safe_path(root, path).stat().st_size for path in metadata) > METADATA_LIMIT:
        raise ValueError("aggregate metadata exceeds 2 MiB")
    for folder in ("model", "source", "artifacts"):
        for file in safe_path(root, folder).rglob("*"):
            relative = str(file.relative_to(root))
            safe_path(root, relative)
            if not file.is_dir() and (not file.is_file() or relative not in set(pins) | metadata):
                raise ValueError("unbound or nonregular distributable file")
    original = read_json(safe_path(root, "source/validation.json"))
    if {k: v for k, v in original.items() if k not in ("native_key", "native_key_checks")} != authority:
        raise ValueError("preserved original receipt disagrees with source authority")
    if original["native_key_checks"].get("generation_provider") != {"repository": REPOSITORY, "revision": source_revision}:
        raise ValueError("native proof generation provider disagrees with source authority")
    for path in FILES:
        data = resolve(REPOSITORY, source_revision, path)
        if not isinstance(data, bytes) or len(data) > METADATA_LIMIT or safe_path(root, path).read_bytes() != data:
            raise ValueError("mandatory metadata differs from original source authority")
    if snapshot["counts"] != authority["snapshot"]["counts"]:
        raise ValueError("packaging counts disagree with original generation")
    if original["native_key_checks"].get("dataset_storage") != {"kind": "reconstructed", "encoding": "gzip", "decoded_path": "ror.sqlite"}:
        raise ValueError("native provenance must identify decoded dataset storage")
    native = original["native_key"]
    expected = original["snapshot"]["outputs"]["ror.sqlite"]
    if (native["module"], native["entity"], native["property"], native["namespace"]) != ("ror", "organizations", "id", "ROR:URL") or type(native["duplicates"]) is not int or native["duplicates"] != 0:
        raise ValueError("wrong native key scope or duplicate count")
    if type(native["records"]) is not int or native["records"] < 0 or native["records"] != original["snapshot"]["counts"]["organizations"]:
        raise ValueError("wrong native record count")
    model_key(root)
    if native["model"]["path"] != "model/ror.modelspec.json" or native["binding"]["path"] != "model/ror.meaning.yaml":
        raise ValueError("wrong native model/binding path")
    for ref in [native["dataset"], native["model"], native["binding"]]:
        if pins.get(ref["path"], {}).get("sha256") != ref["sha256"]:
            raise ValueError("native receipt disagrees with snapshot artifacts")
    if native["dataset"] != {"path": "ror.sqlite", "sha256": expected["sha256"]} or pins["ror.sqlite"]["bytes"] != expected["bytes"]:
        raise ValueError("native receipt disagrees with original generation")
    if sqlite["path"] != "ror.sqlite" or sqlite["encodedPath"] != "ror.sqlite.gz" or sqlite["compression"] != "gzip" or sqlite["decodedSha256"] != expected["sha256"] or sqlite["decodedBytes"] != expected["bytes"] or not 0 < sqlite["decodedBytes"] <= DECODED_LIMIT:
        raise ValueError("invalid native reconstruction descriptor")
    seen, encoded_hash, total = set(), hashlib.sha256(), 0
    with tempfile.TemporaryFile() as encoded:
        for index, chunk in enumerate(chunks, 1):
            path = chunk["path"]
            if path in seen or path != PREFIX + f"{index:04d}" or pins.get(path) != chunk:
                raise ValueError("missing, duplicate, out-of-order or unbound chunk")
            seen.add(path)
            if type(chunk["bytes"]) is not int or not 0 < chunk["bytes"] <= FILE_LIMIT:
                raise ValueError("chunk per-file ceiling exceeded")
            with safe_path(root, path).open("rb") as stream:
                while block := stream.read(1024**2):
                    encoded.write(block)
                    encoded_hash.update(block)
                    total += len(block)
                    if total > ENCODED_LIMIT:
                        raise ValueError("encoded stream ceiling exceeded")
        if total != sqlite["bytes"] or encoded_hash.hexdigest() != sqlite["sha256"]:
            raise ValueError("encoded aggregate bytes/hash mismatch")
        encoded.seek(0)
        decoded_hash, count = hashlib.sha256(), 0
        with gzip.GzipFile(fileobj=encoded, mode="rb") as stream:
            while block := stream.read(1024**2):
                decoded_hash.update(block)
                count += len(block)
                if count > sqlite["decodedBytes"] or count > DECODED_LIMIT:
                    raise ValueError("decoded size ceiling exceeded")
        if count != sqlite["decodedBytes"] or decoded_hash.hexdigest() != sqlite["decodedSha256"]:
            raise ValueError("decoded SQLite bytes/hash mismatch")
    return snapshot


def package(root, database, output, revision, source_revision, chunk_bytes=CHUNK_BYTES):
    root, database, output = Path(root), Path(database), Path(output)
    start = time.monotonic()
    generator = committed_generator(root, revision)
    original_bytes = git_blob(root, source_revision, "source/validation.json")
    original = json.loads(original_bytes)
    current = read_json(safe_path(root, "source/validation.json"))
    if {k: v for k, v in current.items() if k not in ("native_key", "native_key_checks")} != original:
        raise ValueError("historical source-generation receipt changed")
    for relative in [*FILES, "scripts/import_ror.py"]:
        if git_blob(root, source_revision, relative) != safe_path(root, relative).read_bytes():
            raise ValueError("source model/binding/licence/parser changed from reviewed pin")
    proof = native_key(root, database, original)
    if output.exists():
        raise ValueError("output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ror-package-", dir=output.parent))
    writer = None
    try:
        for relative in FILES:
            destination = safe_path(stage, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(root, relative), destination)
        receipt = dict(original)
        receipt["native_key"] = proof
        receipt["native_key_checks"] = {"sqlite_integrity": "ok", "constraint": "TEXT NOT NULL PRIMARY KEY", "source_association": "every native id/status equals full raw source JSON", "generation_provider": {"repository": REPOSITORY, "revision": source_revision}}
        receipt["native_key_checks"]["dataset_storage"] = {"kind": "reconstructed", "encoding": "gzip", "decoded_path": "ror.sqlite"}
        write_json(stage / "source/validation.json", receipt)
        writer = ChunkWriter(stage, chunk_bytes)
        with database.open("rb") as source, gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0, compresslevel=9) as compressed:
            shutil.copyfileobj(source, compressed, 1024**2)
        writer.close_chunk()
        sqlite = {"path": "ror.sqlite", "compression": "gzip", "encodedPath": "ror.sqlite.gz", "sha256": writer.aggregate.hexdigest(), "bytes": writer.total,
                  "decodedBytes": database.stat().st_size, "decodedSha256": proof["dataset"]["sha256"], "chunks": writer.chunks}
        metadata = [*FILES, "source/validation.json"]
        artifacts = [{"path": "ror.sqlite", "kind": "reconstructed", "sha256": proof["dataset"]["sha256"], "bytes": database.stat().st_size}]
        artifacts += [{"path": name, "sha256": digest(stage / name), "bytes": (stage / name).stat().st_size} for name in metadata]
        artifacts += writer.chunks
        snapshot = {"generator": generator, "source_generation": {"repository": REPOSITORY, "revision": source_revision, "original_validation_sha256": hashlib.sha256(original_bytes).hexdigest()},
                    "artifacts": artifacts, "sqlite": sqlite, "counts": original["snapshot"]["counts"], "dataset_role": "unchanged native source projection; serving adapter has separate future checksums"}
        write_json(stage / "source/artifact-snapshot.json", snapshot)
        verify_bundle(stage, source_revision=source_revision, resolve=lambda repository, ref, path: git_blob(root, ref, path))
        measurements = {"elapsed_seconds": time.monotonic() - start, "peak_rss_bytes": source_importer.rss_bytes(), "new_download_bytes": 0,
                        "generated_bytes": sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()), "native_input_bytes": database.stat().st_size,
                        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.platform(), "measurement_scope": "new metadata packaging only; original capture/build receipts remain historical"}
        if measurements["elapsed_seconds"] > 1800 or measurements["peak_rss_bytes"] > 512 * 1024**2 or measurements["generated_bytes"] + database.stat().st_size + writer.total > 8 * 1024**3:
            raise ValueError("packaging resource ceiling exceeded")
        write_json(stage / "source/artifact-packaging-resources.json", measurements)
        stage.rename(output)
        return snapshot, measurements
    except BaseException:
        if writer:
            writer.close_chunk()
        shutil.rmtree(stage)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    build.add_argument("--database", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--generator-revision", required=True)
    build.add_argument("--source-revision", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    check.add_argument("--source-revision", required=True, help="explicit original source commit authority; not a semantic acceptance decision")
    check.add_argument("--authority-repository", type=Path, default=Path(__file__).resolve().parents[1], help="local provider Git repository containing immutable source/generator objects")
    args = parser.parse_args()
    if args.command == "build":
        snapshot, resources = package(args.root, args.database, args.out, args.generator_revision, args.source_revision)
        print(json.dumps({"chunks": len(snapshot["sqlite"]["chunks"]), "resources": resources}, indent=2))
    else:
        snapshot = verify_bundle(args.root, source_revision=args.source_revision,
                                 resolve=lambda repository, ref, path: git_blob(args.authority_repository, ref, path))
        print(json.dumps({"decoded_bytes": snapshot["sqlite"]["decodedBytes"], "chunks": len(snapshot["sqlite"]["chunks"])}, indent=2))
