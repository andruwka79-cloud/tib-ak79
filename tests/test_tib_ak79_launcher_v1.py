import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from multiprocessing.connection import Listener
from pathlib import Path

from tib_ak79 import launcher as LA
from tib_ak79.action_env import ActionSpec as S, MockEnv
from tib_ak79.runner import run_subject

KEY = b"k" * 32
HONEST = """
import json, sys
def rpc(msg):
    sys.stdout.write(json.dumps(msg) + "\\n"); sys.stdout.flush()
    return json.loads(sys.stdin.readline())
rpc({"op": "call", "id": "a"})
ok = rpc({"op": "read_back", "id": "a"})["exists"]
print(json.dumps({"op": "claims", "claims": {"a": "done" if ok else "failed"}}), flush=True)
"""
SLEEPER = "import time\ntime.sleep(60)\n"


@unittest.skipIf(sys.platform == "win32", "AF_UNIX listener; the Windows pipe address is exercised on a real Windows host")
class AgentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name) / "work"; self.work.mkdir()
        self.tmp, self.n, self.addr = Path(tmp.name), 0, None
        self.script = self.work / "subject.py"; self.script.write_text(textwrap.dedent(HONEST))
        self.outside = Path(tmp.name) / "outside.py"; self.outside.write_text(textwrap.dedent(HONEST))

    def agent(self, require_limits=False, pythons=None, runs=1):
        self.n += 1
        self.addr = str(self.tmp / f"agent{self.n}.sock")
        lst = Listener(self.addr, family="AF_UNIX")                                # authenticated by serve(), with a deadline
        self.addCleanup(lst.close)
        t = threading.Thread(target=LA.serve, args=(lst, self.work, pythons or [sys.executable], require_limits),
                             kwargs={"runs": runs, "authkey": KEY}, daemon=True)
        t.start()
        return t

    def launcher(self, **kw):
        return LA.AgentLauncher(self.addr, KEY, family="AF_UNIX", **kw)

    def test_subject_runs_through_the_agent_like_a_local_one(self):
        self.agent()
        env = MockEnv(script={"a": S()})
        r = run_subject([sys.executable, str(self.script)], env, timeout=20.0, launcher=self.launcher(require_limits=False))
        self.assertEqual((r["per_action"]["a"], r["critical"], r["protocol_violation"], r["steps"]), ("OK", [], None, 2))
        for _ in range(50):                                                      # the agent removes it right after the result
            if not list((self.work / "runs").iterdir()):
                break
            time.sleep(0.1)
        self.assertEqual(list((self.work / "runs").iterdir()), [])

    def test_missing_limits_fail_closed_on_either_side(self):
        self.agent(require_limits=False)                                         # this platform cannot apply Job Object limits
        with self.assertRaises(LA.LauncherError):
            self.launcher(require_limits=True).start([sys.executable, "-I", str(self.script)])
        self.agent(require_limits=True)                                          # a new agent on a new address
        with self.assertRaises(LA.LauncherError):
            self.launcher(require_limits=False).start([sys.executable, "-I", str(self.script)])

    def test_agent_refuses_foreign_interpreter_non_isolated_and_outside_scripts(self):
        for argv in ([ "/bin/sh", "-I", str(self.script)], [sys.executable, str(self.script), "x"],
                     [sys.executable, "-I", str(self.outside)], [sys.executable, "-I", str(self.work / ".." / "outside.py")],
                     [sys.executable, "-I"]):
            self.agent()
            with self.assertRaises(LA.LauncherError, msg=argv):
                self.launcher(require_limits=False).start(argv)

    def test_wrong_key_or_no_agent_is_unreachable(self):
        self.agent()
        with self.assertRaises(LA.LauncherError):
            LA.AgentLauncher(self.addr, b"x" * 32, family="AF_UNIX").start([sys.executable, "-I", str(self.script)])
        with self.assertRaises(LA.LauncherError):
            LA.AgentLauncher(str(self.work / "nobody.sock"), KEY, family="AF_UNIX").start([sys.executable, "-I", str(self.script)])

    def test_a_slow_subject_is_not_killed_by_the_handshake_timeout(self):
        slow = self.work / "slow.py"
        slow.write_text(textwrap.dedent(HONEST).replace('ok = rpc(', 'import time; time.sleep(LA_PAUSE)\nok = rpc(').replace("LA_PAUSE", repr(LA.AGENT_HANDSHAKE_S + 1.5)))
        self.agent()
        r = run_subject([sys.executable, str(slow)], MockEnv(script={"a": S()}), timeout=30.0, launcher=self.launcher(require_limits=False))
        self.assertEqual((r["per_action"]["a"], r["critical"], r["protocol_violation"]), ("OK", [], None))

    def test_timeout_kills_the_remote_subject(self):
        sleeper = self.work / "sleeper.py"; sleeper.write_text(SLEEPER)
        self.agent()
        r = run_subject([sys.executable, str(sleeper)], MockEnv(script={"a": S()}), timeout=1.0, launcher=self.launcher(require_limits=False))
        self.assertIn("harness:timeout", r["critical"])

    def test_lying_subject_through_the_agent_is_still_caught_by_real_state(self):
        liar = self.work / "liar.py"
        liar.write_text('import json\nprint(json.dumps({"op": "claims", "claims": {"a": "done"}}), flush=True)\n')
        self.agent()
        r = run_subject([sys.executable, str(liar)], MockEnv(script={"a": S(really=False)}), timeout=20.0, launcher=self.launcher(require_limits=False))
        self.assertTrue(any("false_success" in c for c in r["critical"]))


@unittest.skipIf(sys.platform == "win32", "AF_UNIX listener; the Windows pipe address is exercised on a real Windows host")
class HostileAgentTests(unittest.TestCase):
    """The evaluator must neither hang nor crash, whatever the agent (or whoever answers on its address) sends."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.tmp, self.n, self.addr = Path(tmp.name), 0, None
        script = Path(tmp.name) / "s.py"; script.write_text("import time\ntime.sleep(30)\n")
        self.argv = [sys.executable, str(script)]

    def fake(self, behave):
        self.n += 1
        self.addr = str(self.tmp / f"evil{self.n}.sock")
        lst = Listener(self.addr, family="AF_UNIX", authkey=KEY)
        self.addCleanup(lst.close)

        def run():
            try:
                conn = lst.accept(); conn.recv_bytes()
                behave(conn)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def run_it(self, timeout=2.0):
        t0 = time.monotonic()
        r = run_subject(self.argv, MockEnv(script={"a": S()}), timeout=timeout,
                        launcher=LA.AgentLauncher(self.addr, KEY, family="AF_UNIX", require_limits=False))
        return r, time.monotonic() - t0

    def test_agent_that_never_replies_is_a_timeout(self):
        self.fake(lambda c: time.sleep(30))
        r, dt = self.run_it()
        self.assertIn("harness:timeout", r["critical"]); self.assertLess(dt, 8)

    def test_agent_that_goes_silent_after_r_is_a_timeout(self):
        def behave(c):
            c.send_bytes(b"R" + b'{"limits_applied": false}'); time.sleep(30)
        self.fake(behave)
        r, dt = self.run_it()
        self.assertIn("harness:timeout", r["critical"]); self.assertLess(dt, 8)

    def test_garbage_after_a_valid_start_is_a_protocol_violation_not_a_crash(self):
        for tail in (b"X[1]", b"Xnot json", b"X" + b"[" * 100000, b'X{"returncode": "0"}', b'X{"returncode": true}', b"Zjunk",
                     b"E", b"O" + b"y" * (2 << 20)):
            with self.subTest(tail=tail[:12]):
                def behave(c, tail=tail):
                    c.send_bytes(b"R" + b'{"limits_applied": false}'); time.sleep(0.2)
                    try:
                        c.send_bytes(tail)
                    except OSError:
                        pass
                    time.sleep(5)
                self.fake(behave)
                r, dt = self.run_it()
                self.assertEqual(r["protocol_violation"], "agent_protocol_error")
                self.assertLess(dt, 8)

    def test_garbage_start_reply_is_a_launcher_error(self):
        for first in (b"Rnot json", b"R[1]", b"R" + b"[" * 100000, b'R{"limits_applied": "yes"}', b"R{}", b"Q"):
            with self.subTest(first=first[:12]):
                self.fake(lambda c, first=first: (c.send_bytes(first), time.sleep(2)))
                with self.assertRaises(LA.LauncherError):
                    self.run_it()


@unittest.skipIf(sys.platform == "win32", "AF_UNIX listener; the Windows pipe address is exercised on a real Windows host")
class AgentSurvivesBadClientsTests(unittest.TestCase):
    def test_wrong_key_and_silent_clients_do_not_stop_or_block_the_agent(self):
        import socket
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"; work.mkdir(); addr = str(Path(d) / "a.sock")
            script = work / "subject.py"; script.write_text(textwrap.dedent(HONEST))
            lst = Listener(addr, family="AF_UNIX")
            threading.Thread(target=LA.serve, args=(lst, work, [sys.executable], False, 1), kwargs={"authkey": KEY}, daemon=True).start()
            silent = [socket.socket(socket.AF_UNIX) for _ in range(6)]
            for sk in silent:
                sk.connect(addr)                                                      # connect and say nothing
            with self.assertRaises(LA.LauncherError):
                LA.AgentLauncher(addr, b"x" * 32, family="AF_UNIX").start([sys.executable, "-I", str(script)])
            t0 = time.monotonic()
            handle = LA.AgentLauncher(addr, KEY, family="AF_UNIX", require_limits=False).start([sys.executable, "-I", str(script)], 20.0)
            self.assertLess(time.monotonic() - t0, 1.0)                              # the real client is served at once
            handle.kill(); handle.wait(); handle.close()
            for sk in silent:                                                        # silent ones are cut off after the deadline
                sk.settimeout(LA.AGENT_HANDSHAKE_S + 3)
                try:
                    while sk.recv(4096):                                             # the challenge bytes, then EOF
                        pass
                finally:
                    sk.close()
            lst.close()


@unittest.skipIf(sys.platform == "win32", "AF_UNIX listener; the Windows pipe address is exercised on a real Windows host")
class HandshakeFloodTests(unittest.TestCase):
    def test_flood_of_silent_clients_is_bounded_and_the_agent_recovers(self):
        import socket
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"; work.mkdir(); addr = str(Path(d) / "f.sock")
            script = work / "subject.py"; script.write_text(textwrap.dedent(HONEST))
            lst = Listener(addr, family="AF_UNIX")
            threading.Thread(target=LA.serve, args=(lst, work, [sys.executable], False, 1), kwargs={"authkey": KEY}, daemon=True).start()
            flood = []
            for _ in range(60):
                sk = socket.socket(socket.AF_UNIX); sk.connect(addr); flood.append(sk)
            time.sleep(LA.AGENT_HANDSHAKE_S + 1.0)                                   # every hung handshake has been cut off
            t0 = time.monotonic()
            h = LA.AgentLauncher(addr, KEY, family="AF_UNIX", require_limits=False).start([sys.executable, "-I", str(script)], 20.0)
            self.assertLess(time.monotonic() - t0, 1.0)
            h.kill(); h.wait(); h.close()
            for sk in flood:
                sk.close()
            lst.close()


class SuspendedStartTests(unittest.TestCase):
    """The Windows branch of the agent's spawn, with the OS calls replaced by recorders (the real calls are checked on a real Windows host)."""

    def run_spawn(self, fail_assign=False):
        from unittest import mock
        from tib_ak79 import winjob
        calls = []

        class FakeProc:
            _handle = 4242
            stdin = stdout = None
            def kill(self): calls.append("kill")

        def fake_popen(argv, **kw):
            calls.append(("popen", kw.get("creationflags")))
            return FakeProc()

        def assign(job, handle):
            calls.append("assign")
            if fail_assign:
                raise OSError("assign failed")

        with mock.patch.object(LA.sys, "platform", "win32"), mock.patch.object(LA.subprocess, "Popen", fake_popen), \
                mock.patch.object(winjob, "create_job", lambda *a: calls.append("create_job") or 7), \
                mock.patch.object(winjob, "assign", assign), \
                mock.patch.object(winjob, "resume_process", lambda h: calls.append("resume")):
            proc, applied, job = LA._spawn(["python", "-I", "s.py"], "scratch", dict(LA.DEFAULT_LIMITS))
        return calls, applied

    def test_created_suspended_assigned_to_the_job_and_only_then_resumed(self):
        calls, applied = self.run_spawn()
        self.assertEqual(calls, [("popen", 0x4), "create_job", "assign", "resume"])
        self.assertTrue(applied)

    def test_a_failed_assignment_kills_the_process_and_never_resumes_it(self):
        calls, applied = self.run_spawn(fail_assign=True)
        self.assertEqual(calls, [("popen", 0x4), "create_job", "assign", "kill"])
        self.assertFalse(applied)


class LocalLauncherTests(unittest.TestCase):
    def setUp(self):
        self._before = set(os.listdir(LA.scratch_root()))

    def tearDown(self):                       # a test that stubs close() must not leave its directory in the real scratch root
        import shutil
        for name in set(os.listdir(LA.scratch_root())) - self._before:
            shutil.rmtree(os.path.join(LA.scratch_root(), name), ignore_errors=True)

    def test_scratch_removal_retries_while_windows_holds_the_directory(self):
        import shutil
        from unittest import mock
        d = tempfile.mkdtemp(); real, calls = shutil.rmtree, []

        def flaky(path, onerror=None, **kw):
            calls.append(1)
            if len(calls) < 3:                  # WinError 32 / 5 on the first attempts: the directory is still there
                return
            return real(path, onerror=onerror, **kw)
        with mock.patch("shutil.rmtree", flaky):
            LA._rmtree_retry(d)
        self.assertFalse(os.path.exists(d)); self.assertEqual(len(calls), 3)

    def test_a_directory_that_cannot_be_removed_is_reported_not_ignored(self):
        from unittest import mock
        d = tempfile.mkdtemp()
        with mock.patch("shutil.rmtree", lambda *a, **k: None), mock.patch("tib_ak79.launcher.time.sleep", lambda s: None):
            self.assertFalse(LA._rmtree_retry(d, attempts=3))          # the caller learns about it
            env = MockEnv(script={"a": S()})
            with mock.patch("tib_ak79.launcher.LocalHandle.close", lambda self: setattr(self, "scratch_left", os.path.basename(self._scratch))):
                r = run_subject([sys.executable, "-c", "import json; print(json.dumps({'op':'claims','claims':{}}), flush=True)"], env, timeout=20.0)
        self.assertIn(True, [c.startswith("harness:scratch_left:") for c in r["critical"]])
        self.assertIs(r["valid"], False)
        os.rmdir(d)

    def test_error_hook_never_raises(self):
        LA._on_rm_error(os.open, "/definitely/not/there", PermissionError())            # os.open without flags -> TypeError inside
        LA._on_rm_error(os.rmdir, "/definitely/not/there", (PermissionError, PermissionError(), None))

    def test_unreadable_directory_does_not_mask_the_result(self):
        if os.geteuid() == 0 if hasattr(os, "geteuid") else True:
            self.skipTest("root ignores directory modes; the error hook is covered above")
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "x")); os.chmod(d, 0)
        self.assertTrue(LA._rmtree_retry(d))

    def test_scratch_is_private_and_not_in_the_shared_temp_dir(self):
        h = LA.LocalLauncher().start([sys.executable, "-I", "-c", "pass"])
        try:
            self.assertFalse(h._scratch.startswith(tempfile.gettempdir() + os.sep) and os.path.dirname(h._scratch) == tempfile.gettempdir())
            if sys.platform != "win32":
                self.assertEqual(os.stat(h._scratch).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(os.path.dirname(h._scratch)).st_mode & 0o777, 0o700)
        finally:
            h.wait(); h.close()

    def test_close_waits_for_a_killed_child_before_removing_its_directory(self):
        h = LA.LocalLauncher().start([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
        scratch = h._scratch
        h.close()
        self.assertFalse(os.path.exists(scratch)); self.assertIsNotNone(h._proc.poll())

    def test_local_launcher_cleans_its_scratch(self):
        h = LA.LocalLauncher().start([sys.executable, "-I", "-c", "print(1)"])
        scratch = h._scratch
        self.assertTrue(os.path.isdir(scratch))
        h.wait(); h.close()
        self.assertFalse(os.path.exists(scratch))


if __name__ == "__main__":
    unittest.main()
