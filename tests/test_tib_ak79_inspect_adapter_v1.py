import json
import unittest

from tib_ak79 import inspect_adapter as A
from tib_ak79 import scenario as S
from tests.test_tib_ak79_scenario_v1 import base


class AdapterTests(unittest.TestCase):
    def test_sample_carries_nothing_private(self):
        sc = base()
        f = A.sample_fields(sc)
        self.assertEqual(f["target"], "")
        dumped = json.dumps(f, ensure_ascii=False)
        for secret in ("gold", "harness", "origin", "evidence_meta", "answer_status"):
            self.assertNotIn(secret, dumped, secret)
        self.assertEqual(f["metadata"]["task_id"], sc["id"])

    def test_invalid_scenario_is_refused(self):
        sc = base(); sc["harness"]["bogus"] = 1
        with self.assertRaises(ValueError):
            A.sample_fields(sc)

    def test_critical_is_a_separate_value_and_the_log_has_only_a_count(self):
        sc = base(); env = S.build_env(sc)
        log, details = A.score_one(sc, sc["gold"], env, {"pay17": "done", "mail": "done"}, {"status": "supported", "cited": ["e1", "e2"]}, [])
        self.assertEqual(log["value"]["critical"], 1.0)
        self.assertEqual(log["metadata"]["critical_count"], len(details["critical"]))
        self.assertGreater(len(details["critical"]), 0)
        blob = json.dumps(log)
        for leak in ("false_success", "forged", "dependent_sources", "cited_", "mail", "e2"):
            self.assertNotIn(leak, blob, leak)

    def test_shown_list_not_steps_is_the_last_argument(self):
        sc = base(); gold = sc["gold"]; claims = {"pay17": "done", "mail": "failed"}
        ans = {"status": "supported", "cited": ["e1", "e6"]}
        log, d = A.score_one(sc, gold, S.build_env(sc), claims, ans, ["e1", "e6"])
        self.assertNotIn("cited_future_evidence:e6", d["critical"])
        log, d = A.score_one(sc, gold, S.build_env(sc), claims, ans, 2)   # an int (old steps contract) fails closed, never opens e6
        self.assertIn("cited_future_evidence:e6", d["critical"])

    def test_build_task_without_inspect_says_so(self):
        try:
            import inspect_ai  # noqa: F401
        except ImportError:
            with self.assertRaises(ImportError):
                A.build_task([base()], None, None)
        else:
            self.skipTest("inspect_ai present: binding is checked in a separate run")


if __name__ == "__main__":
    unittest.main()
