import json
import unittest
from pathlib import Path

from tib_ak79 import spec


def _conforming():
    cases, n = [], 0
    for cat, (o, s, m) in spec.PLAN.items():
        for i in range(o + s):
            n += 1
            cases.append({"id": f"{cat}-{i:02d}", "category": cat, "split": "open" if i < o else "sealed",
                          "difficulty": 1 + i % 3, "multistep": i < m, "kind": spec.KINDS[i % 4]})
    return cases


class SpecTests(unittest.TestCase):
    def test_plan_arithmetic(self):
        p = spec.planned_distribution()
        self.assertEqual(sum(v["open"] for v in p["categories"].values()), 40)
        self.assertEqual(sum(v["sealed"] for v in p["categories"].values()), 120)
        self.assertGreaterEqual(sum(v["multistep"] for v in p["categories"].values()), 40)
        self.assertEqual(p["max_executions"], 480)
        self.assertEqual(len(p["categories"]), 12)

    def test_committed_distribution_matches_plan(self):
        committed = json.loads((Path(spec.__file__).parent / "distribution_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, spec.planned_distribution())

    def test_conforming_set_passes(self):
        self.assertEqual(spec.validate_descriptors(_conforming()), [])

    def test_non_list_input(self):
        for junk in (None, "x", 5, {"a": 1}):
            self.assertEqual(spec.validate_descriptors(junk), ["bad_descriptor_list"])

    def test_non_dict_and_bad_ids(self):
        for junk in (None, "str", 5, [1], ("a",)):
            c = _conforming() + [junk]
            self.assertIn(f"bad_descriptor:{len(c) - 1}", spec.validate_descriptors(c))
        for bad in ("x", "T1-a", "T01-", "T01-" + "a" * 33, "T01-a b", "T01-é"):
            c = _conforming(); c[0]["id"] = bad
            self.assertTrue(any(p.startswith("bad_or_duplicate_id") for p in spec.validate_descriptors(c)), bad)
        c = _conforming(); c[0]["id"] = "T02-zz"
        self.assertIn("id_category_mismatch:T02-zz", spec.validate_descriptors(c))
        c = _conforming(); c[0]["difficulty"] = True
        self.assertIn("bad_difficulty:T01-00", spec.validate_descriptors(c))

    def test_violations_detected(self):
        c = _conforming()
        self.assertTrue(any(p.startswith("total") for p in spec.validate_descriptors(c[:-1])))
        d = _conforming(); d[0]["id"] = d[1]["id"]
        self.assertTrue(any(p.startswith("bad_or_duplicate_id") for p in spec.validate_descriptors(d)))
        for key in ("gold", "prompt", "answer", "solution", "reference", "x"):
            e = _conforming(); e[0][key] = "x"
            self.assertIn(f"descriptor_extra_key:T01-00:{key}", spec.validate_descriptors(e))
        f = _conforming()
        for x in f:
            x["multistep"] = False
        self.assertTrue(any(p.startswith("multistep:0") for p in spec.validate_descriptors(f)))
        g = _conforming()
        for x in g:
            if x["category"] == "T01":
                x["kind"] = "positive"
        self.assertIn("kinds_missing:T01", spec.validate_descriptors(g))
        h = _conforming(); h[0]["split"] = "sealed"
        self.assertTrue(any(p.startswith("split_counts:T01") for p in spec.validate_descriptors(h)))


if __name__ == "__main__":
    unittest.main()
