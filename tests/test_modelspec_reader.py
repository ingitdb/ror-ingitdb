"""The ModelSpec JSON reader accepts both vocabularies and refuses a document whose keys disagree."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

from modelspec_spellings import both

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("modelspec_reader", ROOT / "scripts/modelspec_reader.py")
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)

MODULE = {"id": "example.org/shop", "name": "shop", "version": "0.1.0"}


def document(identifier, reference_key):
    """A small neutral model in the vocabulary named by `identifier`."""
    earlier = identifier == reader.EARLIER
    members = {"id": {"type": "string", "required": True}, "buyer": {reference_key: "Customer"}}
    return {"modelspec": identifier, "module": dict(MODULE),
            "components": {"Audit": {"fields": {"createdAt": {"type": "datetime"}}}},
            "entities" if earlier else "records": {
                "Order": {"key": ["id"], "properties" if earlier else "fields": members},
                "Customer": {"key": ["id"], "properties" if earlier else "fields": {"id": {"type": "string"}}}}}


def earlier():
    return document(reader.EARLIER, "entity")


def current():
    return document(reader.CURRENT, "record")


class ReaderTests(unittest.TestCase):
    def test_each_identifier_reads_its_own_vocabulary(self):
        for model in (earlier(), current()):
            with self.subTest(identifier=model["modelspec"]):
                self.assertEqual(set(reader.record_types(model)), {"Order", "Customer"})
                order = reader.record_types(model)["Order"]
                self.assertEqual(order["key"], ["id"])
                self.assertEqual(set(reader.members(model, order)), {"id", "buyer"})
                self.assertIs(reader.members(model, order)["id"], order["properties" if model["modelspec"] == reader.EARLIER else "fields"]["id"])

    def test_both_vocabularies_give_the_same_answer(self):
        a, b = earlier(), current()
        self.assertEqual(both(a), (a, b))
        self.assertEqual(both(b), (a, b))
        for name in ("Order", "Customer"):
            self.assertEqual(reader.members(a, reader.record_types(a)[name]).keys(),
                             reader.members(b, reader.record_types(b)[name]).keys())

    def test_registered_model_reads_the_same_in_both_spellings(self):
        model, rewritten = both(json.loads((ROOT / "model/ror.modelspec.json").read_text()))
        self.assertEqual(set(reader.record_types(model)), set(reader.record_types(rewritten)))
        self.assertIn("organizations", reader.record_types(model))
        for name, record_type in reader.record_types(model).items():
            other = reader.record_types(rewritten)[name]
            self.assertEqual(record_type["key"], other["key"])
            self.assertEqual(reader.members(model, record_type).keys(), reader.members(rewritten, other).keys())
            for member, found in reader.members(model, record_type).items():
                renamed = {("record" if k == "entity" else k): v for k, v in found.items()}
                self.assertEqual(renamed, reader.members(rewritten, other)[member])

    def test_a_document_with_neither_key_has_no_record_types(self):
        for identifier in (reader.EARLIER, reader.CURRENT):
            model = {"modelspec": identifier, "module": dict(MODULE)}
            with self.subTest(identifier=identifier):
                self.assertEqual(reader.record_types(model), {})
                self.assertEqual(reader.members(model, {"key": ["id"]}), {})

    def test_a_document_whose_identifier_and_keys_disagree_is_refused(self):
        cases = {}
        model = earlier()
        model["records"] = {}
        cases["earlier with records"] = (model, r'"records" belongs to format 1.0-draft-2.*"entities"')
        model = earlier()
        model["entities"]["Order"]["fields"] = {}
        cases["earlier with fields"] = (model, r'"fields" belongs to format 1.0-draft-2.*"properties"')
        model = earlier()
        model["entities"]["Order"]["properties"]["buyer"] = {"record": "Customer"}
        cases["earlier with record reference"] = (model, r'"record" belongs to format 1.0-draft-2.*"entity"')
        model = earlier()
        model["entities"]["Order"]["properties"]["buyer"] = {"entity": "Customer", "record": "Customer"}
        cases["earlier with both reference words"] = (model, r'"record" belongs to format 1.0-draft-2')
        model = earlier()
        model["components"]["Audit"]["fields"]["by"] = {"record": "Customer"}
        cases["earlier component with record reference"] = (model, r'"record" belongs to format 1.0-draft-2')
        model = current()
        model["entities"] = {}
        cases["current with entities"] = (model, r'"entities" belongs to format 1.0-draft.*"records"')
        model = current()
        model["records"]["Order"]["properties"] = {}
        cases["current with properties"] = (model, r'"properties" belongs to format 1.0-draft.*"fields"')
        model = current()
        model["records"]["Order"]["fields"]["buyer"] = {"entity": "Customer"}
        cases["current with entity reference"] = (model, r'"entity" belongs to format 1.0-draft\b.*"record"')
        model = current()
        model["records"]["Order"]["fields"]["buyer"] = {"entity": "Customer", "record": "Customer"}
        cases["current with both reference words"] = (model, r'"entity" belongs to format 1.0-draft\b')
        model = current()
        model["components"]["Audit"]["fields"]["by"] = {"entity": "Customer"}
        cases["current component with entity reference"] = (model, r'"entity" belongs to format 1.0-draft\b')
        for label, (model, message) in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, message):
                    reader.record_types(model)
                with self.assertRaisesRegex(ValueError, message):
                    reader.members(model, {})

    def test_removed_constructs_and_reserved_words_are_refused_under_either_identifier(self):
        for model in (earlier(), current()):
            for word in ("collections", "recordsets"):
                with self.subTest(identifier=model["modelspec"], word=word):
                    bad = dict(model, **{word: {}})
                    with self.assertRaisesRegex(ValueError, f'"{word}" was removed'):
                        reader.record_types(bad)
            for word in ("projections", "migrations"):
                with self.subTest(identifier=model["modelspec"], word=word):
                    bad = dict(model, **{word: {}})
                    with self.assertRaisesRegex(ValueError, f'"{word}" is a reserved word'):
                        reader.record_types(bad)

    def test_identifier_must_be_one_of_the_two(self):
        for value in (None, "1.0", "1.0-draft-3", 1, ["1.0-draft"]):
            model = earlier()
            if value is None:
                del model["modelspec"]
            else:
                model["modelspec"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '"modelspec" is'):
                reader.record_types(model)

    def test_shape_errors_are_refused_not_ignored(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            reader.record_types([])
        with self.assertRaisesRegex(ValueError, '"entities" must be an object'):
            reader.record_types(dict(earlier(), entities=[]))
        with self.assertRaisesRegex(ValueError, '"fields" must be an object'):
            reader.members(current(), {"fields": []})
        # A malformed group is left to the consumer's own checks; only the keys are judged here.
        model = current()
        model["records"]["Order"] = "not an object"
        model["components"]["Audit"] = 3
        model["records"]["Customer"]["fields"] = None
        self.assertEqual(set(reader.record_types(model)), {"Order", "Customer"})


# The commit the packaging check names as the original source authority (README, check.yml).
SOURCE_AUTHORITY = "bbbec903248680caea04e68f94b9a957b6efc55b"
JSON_FILE, HCL_FILE = reader.MODEL_FILES


def authority_bytes(path):
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{SOURCE_AUTHORITY}:{path}"],
                          check=True, capture_output=True).stdout


class AcceptedStateTests(unittest.TestCase):
    """The model is the accepted file or its exact rename; the rename is recomputed, not trusted."""

    @classmethod
    def setUpClass(cls):
        cls.accepted = {path: authority_bytes(path) for path in reader.MODEL_FILES}
        cls.renamed = {path: reader.renamed(path, cls.accepted[path]) for path in reader.MODEL_FILES}

    def test_the_recomputed_rename_is_the_reference_tools_output(self):
        for path in reader.MODEL_FILES:
            with self.subTest(path=path):
                self.assertNotEqual(self.renamed[path], self.accepted[path])
                self.assertEqual(hashlib.sha256(self.renamed[path]).hexdigest(), reader.RENAMED_SHA256[path])
        model = json.loads(self.renamed[JSON_FILE])
        self.assertEqual(model["modelspec"], reader.CURRENT)
        self.assertEqual(list(model), ["modelspec", "module", "records"])
        self.assertEqual(reader.record_types(model).keys(), reader.record_types(json.loads(self.accepted[JSON_FILE])).keys())
        text = self.renamed[HCL_FILE].decode()
        self.assertNotRegex(text, r"(?m)^\s*(entity|property)\b")
        self.assertEqual(text.count("record "), self.accepted[HCL_FILE].decode().count("entity "))

    def test_the_committed_model_is_in_one_of_the_two_states(self):
        current_files = {path: (ROOT / path).read_bytes() for path in reader.MODEL_FILES}
        self.assertIn(reader.model_state(self.accepted, current_files), ("accepted", "renamed"))

    def test_both_states_are_accepted(self):
        self.assertEqual(reader.model_state(self.accepted, dict(self.accepted)), "accepted")
        self.assertEqual(reader.model_state(self.accepted, dict(self.renamed)), "renamed")

    def test_anything_else_is_refused(self):
        renamed_json, renamed_hcl = self.renamed[JSON_FILE], self.renamed[HCL_FILE]
        mixed = json.loads(renamed_json)
        mixed["entities"] = mixed.pop("records")
        cases = {
            "only the JSON renamed": {JSON_FILE: renamed_json, HCL_FILE: self.accepted[HCL_FILE]},
            "only the HCL renamed": {JSON_FILE: self.accepted[JSON_FILE], HCL_FILE: renamed_hcl},
            "rename and a trailing byte": {JSON_FILE: renamed_json + b" ", HCL_FILE: renamed_hcl},
            "rename and a changed type": {JSON_FILE: renamed_json, HCL_FILE: renamed_hcl.replace(b'"int"', b'"string"', 1)},
            "rename and a changed flag": {JSON_FILE: renamed_json.replace(b'"required": true', b'"required": false', 1), HCL_FILE: renamed_hcl},
            "mixed vocabulary": {JSON_FILE: (json.dumps(mixed, indent=2) + "\n").encode(), HCL_FILE: renamed_hcl},
            "accepted and a trailing byte": {JSON_FILE: self.accepted[JSON_FILE], HCL_FILE: self.accepted[HCL_FILE] + b"\n"},
            "empty files": {JSON_FILE: b"", HCL_FILE: b""},
        }
        for label, files in cases.items():
            with self.subTest(case=label), self.assertRaisesRegex(ValueError, "neither the accepted file nor its exact rename"):
                reader.model_state(self.accepted, files)

    def test_a_rename_the_reference_tool_would_not_write_is_refused(self):
        # Any other accepted bytes give a rename whose digest is not the pinned one.
        other = self.accepted[JSON_FILE].replace(b'"ror"', b'"other"', 1)
        with self.assertRaisesRegex(ValueError, "does not reproduce the reference tool's output"):
            reader.renamed(JSON_FILE, other)
        with self.assertRaisesRegex(ValueError, "neither the accepted file nor its exact rename"):
            reader.model_state({JSON_FILE: other, HCL_FILE: self.accepted[HCL_FILE]},
                               {JSON_FILE: reader.json_to_current(other), HCL_FILE: self.renamed[HCL_FILE]})
        with self.assertRaisesRegex(ValueError, "not one of the model's two files"):
            reader.renamed("model/ror.meaning.yaml", b"")
        with self.assertRaisesRegex(ValueError, "both model files are required"):
            reader.model_state({JSON_FILE: self.accepted[JSON_FILE]}, dict(self.renamed))


if __name__ == "__main__":
    unittest.main()
