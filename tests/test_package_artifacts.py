import copy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

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
        self.generator = {"repository": packager.REPOSITORY, "revision": "a" * 40, "script": "scripts/package_artifacts.py", "sha256": "b" * 64}

    def tearDown(self):
        self.temp.cleanup()

    def pin_receipt(self):
        self.original["snapshot"]["outputs"]["ror.sqlite"] = {"bytes": self.database.stat().st_size, "sha256": packager.digest(self.database)}
        packager.write_json(self.root / "source/validation.json", self.original)
        packager.write_json(self.root / "source/ror-v2.13.json", self.original["snapshot"]["source"])

    def blob(self, root, revision, relative):
        if relative == "source/validation.json":
            return (json.dumps(self.original, indent=2, sort_keys=True) + "\n").encode()
        if relative == "scripts/import_ror.py":
            return (self.root / relative).read_bytes()
        return (self.root / relative).read_bytes()

    def package(self, name="out"):
        (self.root / "scripts").mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "scripts/import_ror.py", self.root / "scripts/import_ror.py")
        with patch.object(packager, "committed_generator", return_value=self.generator), patch.object(packager, "git_blob", side_effect=self.blob):
            return packager.package(self.root, self.database, Path(self.temp.name) / name, "a" * 40, "c" * 40, chunk_bytes=100)

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
        self.assertEqual(packager.verify_bundle(a), first)
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
                packager.verify_bundle(output)
        packager.write_json(snapshot_path, snapshot)
        chunk = output / snapshot["sqlite"]["chunks"][0]["path"]
        chunk.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "bytes/hash"):
            packager.verify_bundle(output)

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
                packager.verify_bundle(output)
        packager.write_json(receipt_path, original)
        duplicate = copy.deepcopy(snapshot)
        duplicate["artifacts"].append(duplicate["artifacts"][0])
        packager.write_json(output / "source/artifact-snapshot.json", duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate artifact"):
            packager.verify_bundle(output)


if __name__ == "__main__":
    unittest.main()
