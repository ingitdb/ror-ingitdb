import copy
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modelspec_spellings import both

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("package_artifacts", ROOT / "scripts/package_artifacts.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "provider"
        self.root.mkdir()
        for relative in packager.FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        database = self.root / "ror.sqlite"
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript(packager.source_importer.SCHEMA)
            self.record = {"id": "https://ror.org/02mhbdp94", "status": "active", "locations": [], "relationships": [], "source_shape": {"exact": " Bogotá "}}
            db.execute("INSERT INTO organizations VALUES(?,?,?)", (self.record["id"], self.record["status"], json.dumps(self.record)))
        self.database = database
        self.original = {"snapshot": {"outputs": {"ror.sqlite": {"bytes": database.stat().st_size, "sha256": packager.digest(database)}},
                                      "counts": {"organizations": 1}, "source": {"organizations": 1}}, "historical": "unchanged"}
        self.pin_receipt()
        self.code = (ROOT / "scripts/package_artifacts.py").read_bytes()
        self.generator = {"repository": packager.REPOSITORY, "revision": "a" * 40, "script": "scripts/package_artifacts.py", "sha256": hashlib.sha256(self.code).hexdigest()}
        self.authority = {name: (self.root / name).read_bytes() for name in [*packager.FILES, "source/validation.json"]}

    def tearDown(self):
        self.temp.cleanup()

    def pin_receipt(self):
        self.original["snapshot"]["outputs"]["ror.sqlite"] = {"bytes": self.database.stat().st_size, "sha256": packager.digest(self.database)}
        packager.write_json(self.root / "source/validation.json", self.original)
        packager.write_json(self.root / "source/ror-v2.13.json", self.original["snapshot"]["source"])

    def check(self, root):
        return packager.verify_bundle(root, source_revision="c" * 40, resolve=self.resolve)

    def resolve(self, repository, revision, relative):
        self.assertEqual(repository, packager.REPOSITORY)
        if revision == "a" * 40 and relative == "scripts/package_artifacts.py":
            return self.code
        if revision == "c" * 40 and relative in self.authority:
            return self.authority[relative]
        raise ValueError("unresolved immutable authority")

    def blob(self, root, revision, relative):
        if relative == "scripts/package_artifacts.py":
            return self.resolve(packager.REPOSITORY, revision, relative)
        if relative == "source/validation.json":
            return (json.dumps(self.original, indent=2, sort_keys=True) + "\n").encode()
        if relative == "scripts/import_ror.py":
            return (self.root / relative).read_bytes()
        return self.authority[relative]

    def package(self, name="out"):
        (self.root / "scripts").mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "scripts/import_ror.py", self.root / "scripts/import_ror.py")
        with patch.object(packager, "committed_generator", return_value=self.generator), patch.object(packager, "git_blob", side_effect=self.blob):
            return packager.package(self.root, self.database, Path(self.temp.name) / name, "a" * 40, "c" * 40, chunk_bytes=100)

    def test_real_later_representation_pair_is_exact_bounded_and_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            for folder in ("model", "source", "artifacts"):
                shutil.copytree(ROOT / folder, copy / folder)
            for name in ("DATA-LICENSE.md",):
                shutil.copyfile(ROOT / name, copy / name)
            def check():
                return packager.verify_bundle(copy, source_revision="bbbec903248680caea04e68f94b9a957b6efc55b",
                    resolve=lambda repository, ref, path: packager.git_blob(ROOT, ref, path))
            snapshot = check()
            pair = ("model/representations.json", "source/representation-attachment.json")
            self.assertFalse(any(pin["path"] in pair for pin in snapshot["artifacts"]))
            for name in pair:
                file = copy / name
                original = file.read_bytes()
                for value in (original + b" ", b" " * (packager.METADATA_LIMIT + 1)):
                    file.write_bytes(value)
                    with self.assertRaises(ValueError):
                        check()
                file.unlink()
                with self.assertRaises((ValueError, OSError)):
                    check()
                file.symlink_to(ROOT / name)
                with self.assertRaises((ValueError, OSError)):
                    check()
                file.unlink()
                file.write_bytes(original)
            extra = copy / "source/untracked-representation.json"
            extra.write_text("{}")
            with self.assertRaises(ValueError):
                check()

    def test_actual_native_key_source_association_and_deterministic_reconstruction(self):
        first, _ = self.package("a")
        second, _ = self.package("b")
        self.assertEqual(first, second)
        self.assertGreater(len(first["sqlite"]["chunks"]), 1)
        a, b = Path(self.temp.name) / "a", Path(self.temp.name) / "b"
        for artifact in first["artifacts"]:
            if artifact["path"] != "ror.sqlite":
                self.assertEqual((a / artifact["path"]).read_bytes(), (b / artifact["path"]).read_bytes())
        proof = packager.read_json(a / "source/validation.json")
        self.assertEqual(proof["snapshot"], self.original["snapshot"])
        self.assertEqual(proof["native_key"]["records"], 1)
        self.assertEqual(proof["native_key"]["duplicates"], 0)
        self.assertEqual(self.check(a), first)
        self.assertEqual(first["artifacts"][0]["kind"], "reconstructed")
        self.assertFalse((a / "ror.sqlite").exists())
        self.assertNotIn("artifact-snapshot", json.dumps(proof))

    def test_chunk_hash_order_missing_and_path_controls(self):
        snapshot, _ = self.package()
        output = Path(self.temp.name) / "out"
        snapshot_path = output / "source/artifact-snapshot.json"
        for case in ["order", "duplicate", "missing", "escape", "encoded", "decoded", "generator", "physical-dataset"]:
            altered = copy.deepcopy(snapshot)
            if case == "order":
                altered["sqlite"]["chunks"].reverse()
            elif case == "duplicate":
                altered["sqlite"]["chunks"].append(altered["sqlite"]["chunks"][0])
            elif case == "missing":
                altered["sqlite"]["chunks"].pop()
            elif case == "escape":
                altered["artifacts"][1]["path"] = "../model"
            elif case in ["encoded", "decoded"]:
                altered["sqlite"]["sha256" if case == "encoded" else "decodedSha256"] = "0" * 64
            elif case == "physical-dataset":
                altered["artifacts"][0]["kind"] = "file"
            else:
                altered["generator"]["revision"] = "main"
            packager.write_json(snapshot_path, altered)
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.check(output)
        packager.write_json(snapshot_path, snapshot)
        chunk = output / snapshot["sqlite"]["chunks"][0]["path"]
        chunk.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "bytes/hash"):
            self.check(output)

    def test_native_duplicate_constraint_format_count_and_source_controls(self):
        for case in ["duplicate", "format", "association", "count", "hash", "model"]:
            with self.subTest(case=case):
                if case == "duplicate":
                    with closing(sqlite3.connect(self.database)) as db, db:
                        db.execute("ALTER TABLE organizations RENAME TO old")
                        db.execute("CREATE TABLE organizations AS SELECT * FROM old")
                        db.execute("INSERT INTO organizations SELECT * FROM old")
                elif case in ["format", "association"]:
                    with closing(sqlite3.connect(self.database)) as db, db:
                        db.execute("UPDATE organizations SET id=?", ("https://ror.org/" if case == "format" else "https://ror.org/05dxps055",))
                elif case == "count":
                    self.original["snapshot"]["counts"]["organizations"] = 9
                elif case == "model":
                    (self.root / "model/ror.modelspec.json").unlink()
                self.pin_receipt()
                if case == "hash":
                    self.original["snapshot"]["outputs"]["ror.sqlite"]["sha256"] = "0" * 64
                with self.assertRaises((ValueError, FileNotFoundError)):
                    packager.native_key(self.root, self.database, self.original)
                # Restore the complete independent fixture for the next mutation.
                self.tearDown()
                self.setUp()

    def test_model_key_reads_either_vocabulary_and_refuses_a_mixed_document(self):
        path = self.root / "model/ror.modelspec.json"
        earlier, current = both(json.loads(path.read_text()))
        mixed = dict(current, modelspec="1.0-draft")
        for label, model, message in [("earlier", earlier, None), ("current", current, None),
                                      ("mixed", mixed, '"records" belongs to format 1.0-draft-2'),
                                      ("removed", dict(earlier, recordsets={}), '"recordsets" was removed'),
                                      ("reserved", dict(current, projections={}), '"projections" is a reserved word'),
                                      ("neither", {"modelspec": "1.0-draft-2", "module": earlier["module"]}, "required string native key")]:
            path.write_text(json.dumps(model))
            with self.subTest(label=label):
                if message is None:
                    packager.model_key(self.root)
                else:
                    with self.assertRaisesRegex(ValueError, message):
                        packager.model_key(self.root)

    def test_committed_generator_and_staging_cleanup_controls(self):
        with patch.object(packager, "git_blob", return_value=b"different"):
            with self.assertRaisesRegex(ValueError, "differs"):
                packager.committed_generator(ROOT, "a" * 40)
        with patch.object(packager, "FILE_LIMIT", 1):
            with self.assertRaises(ValueError):
                self.package()
        self.assertFalse((Path(self.temp.name) / "out").exists())
        self.assertFalse(list(Path(self.temp.name).glob(".ror-package-*")))
        with patch.object(packager, "verify_bundle", side_effect=ValueError("reconstruction failed")):
            with self.assertRaisesRegex(ValueError, "reconstruction failed"):
                self.package("after-chunks")
        self.assertFalse((Path(self.temp.name) / "after-chunks").exists())
        self.assertFalse(list(Path(self.temp.name).glob(".ror-package-*")))
        with self.assertRaises(ValueError):
            packager.safe_path(self.root, "../escape")
        (self.root / "escape").symlink_to(Path(self.temp.name))
        with self.assertRaises(ValueError):
            packager.safe_path(self.root, "escape/file")

    def test_receipt_scope_and_artifact_collision_controls(self):
        snapshot, _ = self.package()
        output = Path(self.temp.name) / "out"
        receipt_path = output / "source/validation.json"
        original = packager.read_json(receipt_path)
        for key, bad in [("namespace", "serving:id"), ("records", 2), ("duplicates", 1), ("duplicates", False),
                         ("model", {"path": "model/ror.modelspec.hcl", "sha256": packager.digest(output / "model/ror.modelspec.hcl")})]:
            receipt = copy.deepcopy(original)
            receipt["native_key"][key] = bad
            packager.write_json(receipt_path, receipt)
            updated = copy.deepcopy(snapshot)
            for artifact in updated["artifacts"]:
                if artifact["path"] == "source/validation.json":
                    artifact.update(bytes=receipt_path.stat().st_size, sha256=packager.digest(receipt_path))
            packager.write_json(output / "source/artifact-snapshot.json", updated)
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                self.check(output)
        packager.write_json(receipt_path, original)
        duplicate = copy.deepcopy(snapshot)
        duplicate["artifacts"].append(duplicate["artifacts"][0])
        packager.write_json(output / "source/artifact-snapshot.json", duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate artifact"):
            self.check(output)

    def test_authoritative_source_proof_and_generator_associations(self):
        snapshot, _ = self.package()
        output = Path(self.temp.name) / "out"
        receipt_path = output / "source/validation.json"
        receipt = packager.read_json(receipt_path)
        for case in ["source-revision", "source-repository", "original-hash", "generator-hash", "generator-revision", "generator-script",
                     "proof-revision", "proof-repository", "preserved-receipt", "counts"]:
            changed, proof = copy.deepcopy(snapshot), copy.deepcopy(receipt)
            if case.startswith("source-"):
                changed["source_generation"][case.split("-")[1]] = "0" * 40 if case.endswith("revision") else "https://github.com/other/source"
            elif case == "original-hash":
                changed["source_generation"]["original_validation_sha256"] = "0" * 64
            elif case.startswith("generator-"):
                key = {"hash": "sha256", "revision": "revision", "script": "script"}[case.split("-")[1]]
                changed["generator"][key] = "0" * (64 if key == "sha256" else 40) if key != "script" else "scripts/import_ror.py"
            elif case.startswith("proof-"):
                proof["native_key_checks"]["generation_provider"][case.split("-")[1]] = "0" * 40 if case.endswith("revision") else "https://github.com/other/source"
            elif case == "preserved-receipt":
                proof["historical"] = "rewritten"
            else:
                changed["counts"]["organizations"] = 9
            packager.write_json(receipt_path, proof)
            for artifact in changed["artifacts"]:
                if artifact["path"] == "source/validation.json":
                    artifact.update(bytes=receipt_path.stat().st_size, sha256=packager.digest(receipt_path))
            packager.write_json(output / "source/artifact-snapshot.json", changed)
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.check(output)
        with self.assertRaisesRegex(ValueError, "explicit immutable"):
            packager.verify_bundle(output)
        packager.write_json(receipt_path, receipt)
        packager.write_json(output / "source/artifact-snapshot.json", snapshot)
        with self.assertRaisesRegex(ValueError, "immutable code authority"):
            packager.verify_bundle(output, source_revision="c" * 40, resolve=lambda *args: b"x" * (packager.METADATA_LIMIT + 1))
        self.assertEqual(self.check(output), snapshot)

    def test_mandatory_metadata_regular_bounds_and_unbound_controls(self):
        for case in ["pin", "file-and-pin", "source-pin", "hcl", "unbound-file", "unbound-pin", "rehashed-attribution", "directory", "fifo", "symlink", "bounds"]:
            snapshot, _ = self.package(case)
            output = Path(self.temp.name) / case
            license = output / "DATA-LICENSE.md"
            if case in ["pin", "file-and-pin", "source-pin", "hcl"]:
                missing = {"source-pin": "source/ror-v2.13.json", "hcl": "model/ror.modelspec.hcl"}.get(case, "DATA-LICENSE.md")
                snapshot["artifacts"] = [a for a in snapshot["artifacts"] if a["path"] != missing]
                if case != "pin":
                    (output / missing).unlink()
            elif case in ["unbound-file", "unbound-pin"]:
                path = output / "source/unbound.json"
                path.write_text("{}")
                if case == "unbound-pin":
                    snapshot["artifacts"].append({"path": "source/unbound.json", "bytes": 2, "sha256": packager.digest(path)})
            else:
                license.unlink()
                if case == "directory":
                    license.mkdir()
                elif case == "fifo":
                    os.mkfifo(license)
                elif case == "symlink":
                    license.symlink_to(ROOT / "DATA-LICENSE.md")
                else:
                    license.write_bytes(b"altered attribution" if case == "rehashed-attribution" else b"x" * (packager.METADATA_LIMIT + 1))
                    for a in snapshot["artifacts"]:
                        if a["path"] == "DATA-LICENSE.md":
                            a.update(bytes=license.stat().st_size, sha256=packager.digest(license))
            packager.write_json(output / "source/artifact-snapshot.json", snapshot)
            with self.subTest(case=case), self.assertRaises((ValueError, FileNotFoundError)):
                self.check(output)


if __name__ == "__main__":
    unittest.main()
