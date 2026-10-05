import hashlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("import_ror", Path(__file__).parents[1] / "scripts/import_ror.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
audit_spec = importlib.util.spec_from_file_location("verify_source", Path(__file__).parents[1] / "scripts/verify_source.py")
verifier = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(verifier)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.record = {"id": "https://ror.org/02mhbdp94", "status": "inactive", "names": [{"value": "Universidad", "types": ["ror_display"]}],
                       "locations": [{"geonames_id": 3688689, "geonames_details": {"name": "Bogotá", "country_code": "CO"}},
                                     {"geonames_id": 3688689, "geonames_details": {"name": "Bogotá"}}],
                       "relationships": [{"id": "https://ror.org/02mhbdp94", "label": "original", "type": "Related"}],
                       "future_source_field": {"preserve": [None, False, " exact "]}}

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, records=None):
        payload = json.dumps(records if records is not None else [self.record], ensure_ascii=False).encode()
        return self.fixture_payload(payload, len(records) if records is not None else 1)

    def fixture_payload(self, payload, count):
        archive = self.root / "input.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("input.json", payload)
        pin = {"archive_bytes": archive.stat().st_size, "archive_sha256": importer.digest(archive), "member": "input.json",
               "member_bytes": len(payload), "member_sha256": hashlib.sha256(payload).hexdigest(), "organizations": count}
        return archive, pin

    def assert_refused_cleanly(self, archive, pin, name):
        with self.assertRaises(ValueError):
            importer.build(archive, pin, self.root / name)
        self.assertFalse((self.root / name).exists())
        self.assertFalse(list(self.root.glob(".ror-build-*")))

    def test_duplicate_and_nonfinite_json_fail_after_valid_prefix(self):
        valid = json.dumps(self.record)
        for number, token in enumerate([
            '{"id":"https://ror.org/05dxps055","status":"active","status":"withdrawn","locations":[],"relationships":[]}',
            '{"id":"https://ror.org/05dxps055","status":"active","locations":[],"relationships":[],"x":{"nested":{"same":1,"same":2}}}',
            *[valid.replace('"inactive"', '"active"').replace('"future_source_field":', f'"number":{bad},"future_source_field":')
              for bad in ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"]],
            valid.replace('"preserve":', '"nested_number":1e400,"preserve":'),
        ]):
            with self.subTest(token=token):
                archive, pin = self.fixture_payload(("[" + valid + "," + token + "]").encode(), 2)
                self.assert_refused_cleanly(archive, pin, f"invalid-json-{number}")
        with self.assertRaises(ValueError):
            importer.canonical({"nested": [float("nan")]})
        with self.assertRaises(ValueError):
            importer.canonical({"nested": [float("inf")]})

    def test_projected_types_and_complete_native_ids_fail_without_coercion(self):
        mutations = []
        for bad_id in ["https://ror.org/", "https://ror.org/02mhbdp94?x=1", "https://ror.org/02mhbdp94#x",
                       "https://ror.org/02mhbdp94/", "http://ror.org/02mhbdp94", "02mhbdp94",
                       "https://ror.org/02MHBDP94", "https://ror.org/0|mhbdp94", "https://ror.org/02mhbdp95", 123, None]:
            mutations.extend([("id", bad_id), ("relationships.0.id", bad_id)])
        mutations.extend([
            ("status", False), ("status", ""), ("locations", {}), ("relationships", {}),
            ("locations.0", False), ("locations.0.geonames_details", []), ("locations.0.geonames_id", True),
            ("relationships.0", "not object"), ("relationships.0.type", False), ("relationships.0.type", None),
            ("relationships.0.label", 7), ("relationships.0.label", []),
        ])
        for number, (path, value) in enumerate(mutations):
            with self.subTest(path=path, value=value):
                invalid = copy.deepcopy(self.record)
                container = invalid
                parts = path.split(".")
                for part in parts[:-1]:
                    container = container[int(part)] if isinstance(container, list) else container[part]
                key = int(parts[-1]) if isinstance(container, list) else parts[-1]
                container[key] = value
                # Invalid row is reached after a valid organization has been inserted.
                valid = copy.deepcopy(self.record)
                valid["id"] = "https://ror.org/05dxps055"
                archive, pin = self.fixture([valid, invalid])
                self.assert_refused_cleanly(archive, pin, f"invalid-types-{number}")

    def test_full_shape_status_and_location_multiplicity(self):
        archive, pin = self.fixture()
        manifest, _ = importer.build(archive, pin, self.root / "out")
        with sqlite3.connect(self.root / "out/ror.sqlite") as db:
            raw, status = db.execute("SELECT raw_json,status FROM organizations").fetchone()
            self.assertEqual(json.loads(raw), self.record)
            self.assertEqual(status, "inactive")
            self.assertEqual(db.execute("SELECT ordinal,geonames_id FROM locations ORDER BY ordinal").fetchall(), [(0, 3688689), (1, 3688689)])
        self.assertEqual(manifest["counts"]["organizations_with_multiple_locations"], 1)
        self.assertEqual((self.root / "out/ror-geonames-ids.txt").read_bytes(), b"3688689\n")

    def test_same_input_identical_outputs(self):
        archive, pin = self.fixture()
        a, _ = importer.build(archive, pin, self.root / "a")
        b, _ = importer.build(archive, pin, self.root / "b")
        self.assertEqual(a, b)
        for name in ["ror.sqlite", "ror-geonames-ids.txt", "snapshot.json"]:
            self.assertEqual((self.root / "a" / name).read_bytes(), (self.root / "b" / name).read_bytes())

    def test_independent_audit_detects_projection_drift(self):
        archive, pin = self.fixture()
        importer.build(archive, pin, self.root / "out")
        database = self.root / "out/ror.sqlite"
        self.assertEqual(verifier.audit(archive, "input.json", database)["locations"], 2)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE locations SET geonames_id=1 WHERE ordinal=1")
        with self.assertRaisesRegex(ValueError, "location values/order mismatch"):
            verifier.audit(archive, "input.json", database)

    def test_model_properties_match_generated_sqlite_schema(self):
        archive, pin = self.fixture()
        importer.build(archive, pin, self.root / "out")
        model = json.loads((Path(__file__).parents[1] / "model/ror.modelspec.json").read_text())
        with sqlite3.connect(self.root / "out/ror.sqlite") as db:
            for table, entity in model["entities"].items():
                columns = {row[1]: row for row in db.execute(f"PRAGMA table_info({table})")}
                self.assertEqual(set(columns), set(entity["properties"]))
                for name, prop in entity["properties"].items():
                    self.assertEqual(columns[name][2], "INTEGER" if prop.get("type") == "int" else "TEXT")

    def test_hash_count_and_duplicate_fail_without_partial_output(self):
        for case in ["archive", "member", "count", "duplicate"]:
            with self.subTest(case=case):
                archive, pin = self.fixture([self.record, self.record] if case == "duplicate" else None)
                if case in ["archive", "member"]:
                    pin[case + "_sha256"] = "0" * 64
                if case == "count":
                    pin["organizations"] = 2
                with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                    importer.build(archive, pin, self.root / case)
                self.assertFalse((self.root / case).exists())
                self.assertFalse(list(self.root.glob(".ror-build-*")))

    def test_bad_ids_and_ceiling_do_not_publish(self):
        self.record["locations"][0]["geonames_id"] = "03688689"
        archive, pin = self.fixture()
        with self.assertRaises(ValueError):
            importer.build(archive, pin, self.root / "bad")
        self.record["locations"][0]["geonames_id"] = None
        archive, pin = self.fixture()
        with patch.object(importer, "LIMIT_DB", 1), self.assertRaises(ValueError):
            importer.build(archive, pin, self.root / "over")
        self.assertFalse((self.root / "over").exists())

    def test_statuses_and_null_references_are_preserved(self):
        self.record["status"] = "withdrawn"
        self.record["locations"][0]["geonames_id"] = None
        self.record["relationships"][0]["id"] = "https://ror.org/05dxps055"
        archive, pin = self.fixture()
        manifest, _ = importer.build(archive, pin, self.root / "out")
        self.assertEqual(manifest["statuses"], {"withdrawn": 1})
        self.assertEqual(manifest["counts"]["null_location_ids"], 1)
        self.assertEqual(manifest["counts"]["relationship_targets_absent"], 1)

    def test_streaming_boundaries_and_invalid_json(self):
        class TinyReader(io.StringIO):
            def read(self, n=-1):
                return super().read(min(n, 3) if n >= 0 else 3)
        self.assertEqual(list(importer.records(TinyReader(' [ {"x": "á"}, {"x": 2} ] '))), [{"x": "á"}, {"x": 2}])
        for bad in ["{}", "[{} {}]", "[{},]", "[{}", "[{}]x", "[null]", "["]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                list(importer.records(TinyReader(bad)))


if __name__ == "__main__":
    unittest.main()
