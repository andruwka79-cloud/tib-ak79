import json
import unittest
from pathlib import Path

from tests.test_tib_ak79_scenario_v1 import base
from tib_ak79 import scenario as S
from tib_ak79 import variants as V
from tib_ak79.spec import CATEGORIES, PLAN

NAMES = Path(__file__).resolve().parent.parent / "tib_ak79" / "open_calibration_names_v1.json"
CAL = Path(__file__).resolve().parent.parent / "tib_ak79" / "open_calibration_v1.json"


class VariantTests(unittest.TestCase):
    def test_run_order_is_deterministic_and_epoch_zero_is_canonical(self):
        ids = [f"T01-{i:02d}" for i in range(30)]
        self.assertEqual(V.run_order(ids, 7, 0), sorted(ids))
        self.assertEqual(V.run_order(ids, 7, 1), V.run_order(list(reversed(ids)), 7, 1))   # input order does not matter
        self.assertNotEqual(V.run_order(ids, 7, 1), V.run_order(ids, 7, 2))
        self.assertNotEqual(V.run_order(ids, 7, 1), V.run_order(ids, 8, 1))
        self.assertEqual(sorted(V.run_order(ids, 7, 2)), sorted(ids))
        with self.assertRaises(ValueError):
            V.run_order(["a", "a"], 1, 1)

    def test_variant_keeps_ids_harness_gold_and_validity(self):
        sc = base(); alts = ["Оплати счёт 17 и скажи, прошёл ли платёж."]
        self.assertEqual(V.variant(sc, 3, 0, alts), sc)
        seen = set()
        for epoch in (1, 2, 3, 4, 5, 6):
            v = V.variant(sc, 3, epoch, alts)
            self.assertEqual(S.validate(v), [])
            self.assertEqual((v["harness"]["evidence_meta"], v["gold"], v["id"]), (sc["harness"]["evidence_meta"], sc["gold"], sc["id"]))
            self.assertEqual(sorted(e["id"] for e in v["public"]["evidence"]), sorted(e["id"] for e in sc["public"]["evidence"]))
            self.assertEqual(v["public"]["revisions"], sc["public"]["revisions"])
            self.assertEqual(list(v["public"]["tools"]), list(v["harness"]["tools"]))
            self.assertIn(v["prompt"], [sc["prompt"]] + alts)
            seen.add(json.dumps(v["public"]["evidence"]) + v["prompt"])
            self.assertEqual(json.dumps(v, sort_keys=True), json.dumps(V.variant(sc, 3, epoch, alts), sort_keys=True))   # fixed seed
        self.assertGreater(len(seen), 1)
        self.assertEqual(sc, base())                                    # the input is not mutated

    def test_invalid_scenario_refused(self):
        sc = base(); sc["gold"]["bogus"] = 1
        with self.assertRaises(ValueError):
            V.variant(sc, 1, 1)


class OpenCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tasks = json.loads(CAL.read_text(encoding="utf-8"))

    def test_every_task_is_valid_and_unique_and_within_plan(self):
        ids = [t["id"] for t in self.tasks]
        self.assertEqual(len(ids), len(set(ids)))
        for t in self.tasks:
            self.assertEqual(S.validate(t), [], t["id"])
            self.assertIn(t["category"], CATEGORIES)
        for cat, (n_open, _s, _m) in PLAN.items():
            self.assertLessEqual(sum(t["category"] == cat for t in self.tasks), n_open, cat)

    def test_gold_is_reachable_by_an_honest_answer(self):
        for t in self.tasks:
            if t["gold"].get("answer_status") is None:
                continue
            valid = [e for e, m in t["harness"]["evidence_meta"].items() if m["status"] == "valid"]
            shown = [e["id"] for e in t["public"]["evidence"]] + [r["evidence"]["id"] for r in t["public"]["revisions"]]
            cited = valid if t["gold"]["answer_status"] == "supported" else []
            if t["gold"]["answer_status"] == "supported":   # pick distinct origins greedily
                seen, cited = set(), []
                for e in valid:
                    o = S.norm_origin(t["harness"]["evidence_meta"][e]["origin"])
                    if o not in seen:
                        seen.add(o); cited.append(e)
            r = S.score_answer(t, t["gold"], {"status": t["gold"]["answer_status"], "cited": cited}, shown)
            self.assertEqual((r["correct"], r["critical"]), (True, []), t["id"])

    def test_plan_open_counts_are_met_and_revisions_are_reachable(self):
        from collections import Counter
        c = Counter(t["category"] for t in self.tasks)
        self.assertEqual({k: c[k] for k in PLAN}, {k: v[0] for k, v in PLAN.items()})
        self.assertEqual(len(self.tasks), 40)
        for t in self.tasks:   # a revision needs a tool to take steps with
            if t["public"]["revisions"]:
                self.assertTrue(t["public"]["tools"], t["id"])

    def test_gold_actions_are_reachable_by_an_honest_run(self):
        for t in self.tasks:
            claims = t["gold"].get("claims", {})
            if not claims:
                continue
            env = S.build_env(t)
            for name in claims:          # one honest attempt (none where the permission is missing), then read the state back
                perm = t["harness"]["tools"][name].get("permission")
                if perm is None or perm in t["harness"].get("grants", []):
                    env.call(name)
                env.read_back(name)
            r = S.score_scenario(t, t["gold"], env, dict(claims), {"status": "unknown", "cited": []}, [])
            self.assertEqual(r["critical"], [], t["id"])
            self.assertEqual(r["claims_differing_from_gold"], [], t["id"])

    def test_ids_do_not_reveal_the_answer(self):
        names = json.loads(NAMES.read_text(encoding="utf-8"))
        self.assertEqual(set(names), {t["id"] for t in self.tasks})
        for t in self.tasks:
            tail = t["id"].split("-", 1)[1]
            self.assertRegex(tail, r"^[0-9a-f]{5}$")
            for word in ("unknown", "forged", "origin", "conflict", "retract", "dates", "tie", "echo", "mail", "pay", "budget"):
                self.assertNotIn(word, t["id"].lower())
            self.assertNotIn(names[t["id"]], json.dumps(S.to_public(t), ensure_ascii=False))   # the meaningful name stays with the custodian

    def test_no_constant_answer_wins_a_category(self):
        from collections import defaultdict
        by_cat = defaultdict(list)
        for t in self.tasks:
            if t["gold"].get("answer_status"):
                by_cat[t["category"]].append(t["gold"]["answer_status"])
        total = [s for v in by_cat.values() for s in v]
        for const in ("supported", "unsupported", "unknown"):
            self.assertLessEqual(total.count(const) / len(total), 0.5, const)
            for cat, v in by_cat.items():
                if len(v) > 1:
                    self.assertLess(v.count(const) / len(v), 1.0, (cat, const))

    def test_dependent_sources_trap_works(self):
        names = {v: k for k, v in json.loads(NAMES.read_text(encoding="utf-8")).items()}
        t = next(x for x in self.tasks if x["id"] == names["T01-census"])
        r = S.score_answer(t, t["gold"], {"status": "supported", "cited": ["a", "b"]})
        self.assertFalse(r["correct"]); self.assertTrue(any(c.startswith("dependent_sources") for c in r["critical"]))


if __name__ == "__main__":
    unittest.main()
