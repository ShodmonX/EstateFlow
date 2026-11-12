import json
import unittest
from pathlib import Path
from v2.shared.contracts import validate, canonical_hash, new_event


class Contracts(unittest.TestCase):
    def test_original_fixture_decisions(self):
        for path in Path("tests/fixtures/v2/contracts").glob("*.json"):
            for step in json.loads(path.read_text())["steps"]:
                with self.subTest(path=path.name, event=step["event"]["event_type"]):
                    if step["schema_valid"]:
                        validate(step["event"], semantic=False)
                    else:
                        with self.assertRaises(ValueError):
                            validate(step["event"], semantic=False)

    def test_semantic_identity_mismatch_denied(self):
        step = json.loads(
            Path("tests/fixtures/v2/contracts/17-semantic-mismatch.json").read_text()
        )["steps"][0]
        with self.assertRaises(ValueError):
            validate(step["event"])

    def test_hash_not_dict_insertion_order(self):
        self.assertEqual(canonical_hash({"x": 1, "y": 2}), canonical_hash({"y": 2, "x": 1}))
        self.assertNotEqual(canonical_hash({"x": 1}), canonical_hash({"x": 2}))

    def test_lab_event_is_not_privileged_by_body(self):
        e = new_event(
            "lab.synthetic.changed",
            "lab_producer",
            "synthetic",
            "00000000-0000-4000-8000-000000000001",
            1,
            {"value": 1},
        )
        validate(e)
        e["producer"] = "catalog"
        with self.assertRaises(ValueError):
            validate(e)
