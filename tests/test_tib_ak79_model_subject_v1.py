"""model_subject.py + model_ask.py + runner `ask` on a FAKE model command (a python script; no vendor CLI, no network)."""
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tib_ak79 import harness as H
from tib_ak79 import runner as R
from tib_ak79.action_env import MockEnv
from tib_ak79.model_ask import cli_ask
from tib_ak79.scenario import EvidenceFeed, build_env, to_public
from tib_ak79.variants import variant

CAL = Path(__file__).resolve().parent.parent / "tib_ak79" / "open_calibration_v1.json"
SUBJECT = [sys.executable, str(Path(__file__).resolve().parent.parent / "tib_ak79" / "model_subject.py")]

FAKE = """
import json, re, sys
p = sys.stdin.read()
tools = re.search(r"^TOOLS: (.*)$", p, re.M).group(1)
names = [t.split(" (")[0] for t in tools.split(", ")] if tools != "none" else []
MODE = %r
def out(o): print(json.dumps(o))
if MODE == "garbage":
    print("I would rather not say"); sys.exit(0)
if MODE == "never_final":
    out({"read_back": "nope"}); sys.exit(0)
done = {n: ("[call %%s]" %% n) in p for n in names}
read = {n: re.search(r"\\[read_back %%s\\] exists=(true|false)" %% re.escape(n), p) for n in names}
if MODE == "liar":
    out({"final": {"claims": {n: "done" for n in names}, "answer": {"status": "supported", "cited": []}}}); sys.exit(0)
for n in names:
    if not done[n]: out({"call": n}); sys.exit(0)
    if not read[n]: out({"read_back": n}); sys.exit(0)
claims = {n: ("done" if read[n].group(1) == "true" else "failed") for n in names}
out({"final": {"claims": claims, "answer": {"status": "unknown", "cited": []}}})
"""


class ModelSubjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.tasks = json.loads(CAL.read_text(encoding="utf-8"))
        cls.cmd = {}
        for mode in ("honest", "liar", "garbage", "never_final"):
            path = Path(cls.dir.name) / (mode + ".py")
            path.write_text(textwrap.dedent(FAKE % mode))
            cls.cmd[mode] = [sys.executable, "-I", str(path)]

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def run_one(self, category, mode, **kw):
        task = next(t for t in self.tasks if t["category"] == category)
        sc = variant(task, 7, 0, None)
        env = build_env(sc)
        feed = EvidenceFeed(sc, env)
        return sc, env, R.run_subject(SUBJECT, env, timeout=30.0, feed=feed, public=to_public(sc), ask=cli_ask(self.cmd[mode]), **kw)

    def test_honest_model_does_the_action_and_reads_it_back(self):
        _, env, res = self.run_one("T07", "honest")
        self.assertEqual(res["protocol_violation"], None)
        self.assertGreaterEqual(res["asks"], 3)
        self.assertEqual(res["critical"], [])
        self.assertEqual(res["claims"], {"pay17": "done"})
        self.assertNotIn("UNVERIFIED_CLAIM", res["per_action"].values())

    def test_a_model_that_claims_without_acting_is_caught_by_real_state(self):
        _, env, res = self.run_one("T07", "liar")
        self.assertEqual(res["per_action"], {"pay17": "UNVERIFIED_CLAIM"})

    def test_unparsable_model_output_ends_in_unknown_with_no_crash(self):
        _, env, res = self.run_one("T01", "garbage")
        self.assertEqual(res["protocol_violation"], None)
        self.assertEqual(res["answer"], {"status": "unknown", "cited": []})
        self.assertLessEqual(res["asks"], R.MAX_ASKS)

    def test_a_model_that_never_finishes_is_cut_by_the_turn_cap(self):
        _, env, res = self.run_one("T01", "never_final")
        self.assertEqual(res["protocol_violation"], None)       # the script stops itself first
        self.assertLessEqual(res["asks"], R.MAX_ASKS)

    def test_ask_is_refused_when_the_evaluator_gave_no_model(self):
        task = next(t for t in self.tasks if t["category"] == "T01")
        sc = variant(task, 7, 0, None); env = build_env(sc)
        res = R.run_subject(SUBJECT, env, timeout=10.0, feed=EvidenceFeed(sc, env), public=to_public(sc))
        self.assertEqual(res["protocol_violation"], "protocol_unknown_op")

    def test_harness_ask_cap_applies_to_a_greedy_subject(self):
        flood = Path(self.dir.name) / "flood.py"
        flood.write_text("import json,sys\nfor _ in range(%d):\n    print(json.dumps({'op':'ask','prompt':'x'}), flush=True); sys.stdin.readline()\n" % (R.MAX_ASKS + 5))
        task = next(t for t in self.tasks if t["category"] == "T01")
        sc = variant(task, 7, 0, None); env = build_env(sc)
        res = R.run_subject([sys.executable, str(flood)], env, timeout=20.0, feed=EvidenceFeed(sc, env), public=to_public(sc), ask=lambda p: "ok")
        self.assertEqual(res["protocol_violation"], "ask_limit")

    def test_a_failing_model_call_is_reported_not_raised(self):
        task = next(t for t in self.tasks if t["category"] == "T01")
        sc = variant(task, 7, 0, None); env = build_env(sc)

        def boom(prompt):
            raise TimeoutError("slow")
        res = R.run_subject(SUBJECT, env, timeout=20.0, feed=EvidenceFeed(sc, env), public=to_public(sc), ask=boom)
        self.assertEqual(res["protocol_violation"], None)
        self.assertEqual(res["answer"], {"status": "unknown", "cited": []})

    def test_model_command_gets_an_allowlisted_environment_not_the_harness_secrets(self):
        spy = Path(self.dir.name) / "spy.py"
        spy.write_text("import json,os,sys\nsys.stdin.read()\nprint(json.dumps({'keys': sorted(os.environ), 'cwd': os.getcwd()}))\n")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret", "AK79_SECRET": "s3", "GITHUB_TOKEN": "t", "PATH": os.environ.get("PATH", "")}):
            out = json.loads(cli_ask([sys.executable, str(spy)])("hi"))
            keys = set(out["keys"])
            for secret in ("OPENAI_API_KEY", "AK79_SECRET", "GITHUB_TOKEN"):
                self.assertNotIn(secret, keys)
            self.assertIn("PATH", keys)
            self.assertNotEqual(os.path.realpath(out["cwd"]), os.path.realpath(os.getcwd()))     # not the repository
            named = json.loads(cli_ask([sys.executable, str(spy)], extra_env={"MODEL_PROFILE": "x"})("hi"))
            self.assertIn("MODEL_PROFILE", named["keys"])                                  # only what the operator names is added
            self.assertNotIn("OPENAI_API_KEY", named["keys"])

    def test_the_real_home_is_never_passed_only_an_empty_or_a_named_one(self):
        spy = Path(self.dir.name) / "spyhome.py"
        spy.write_text("import json,os,sys\nsys.stdin.read()\nprint(json.dumps({k: os.environ.get(k) for k in ('HOME','USERPROFILE','APPDATA')}))\n")
        real = os.path.expanduser("~")
        out = json.loads(cli_ask([sys.executable, str(spy)])("hi"))
        for v in out.values():
            self.assertFalse(v is None or v.startswith(real.rstrip(os.sep) + os.sep + ".") or v == real, out)
        self.assertNotEqual(out["HOME"], real)
        with tempfile.TemporaryDirectory() as prepared:
            named = json.loads(cli_ask([sys.executable, str(spy)], home=prepared)("hi"))
        self.assertEqual(named["HOME"], prepared)

    def test_shell_wrappers_are_refused_as_the_model_command(self):
        for argv in (["bash", "-c", "x"], ["sh", "-c", "x"], ["cmd", "/c", "x"], ["/usr/bin/env", "x"]):
            with self.assertRaises(ValueError, msg=str(argv)):
                cli_ask(argv)

    def test_pilot_over_the_open_set_with_a_fake_model(self):
        p = H.run_pilot(self.tasks, SUBJECT, 7, 1, ask=cli_ask(self.cmd["honest"]))
        self.assertEqual(len(p["records"]), len(self.tasks))
        self.assertTrue(all(r["valid"] for r in p["records"]))
        self.assertFalse(p["report"]["beats_best_constant"])

    def test_late_evidence_is_not_in_the_subject_view(self):
        t8 = next(t for t in self.tasks if t["category"] == "T08")
        view = json.dumps(to_public(variant(t8, 7, 0, None)), ensure_ascii=False)
        self.assertNotIn("отозвано", view)


if __name__ == "__main__":
    unittest.main()
