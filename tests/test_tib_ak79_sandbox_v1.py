import ctypes
import errno
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tib_ak79 import sandbox_probe, winjob


def _closed_port():
    c = socket.socket(); c.bind(("127.0.0.1", 0)); port = c.getsockname()[1]; c.close()
    return port


class ProbeTests(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(sandbox_probe.DEFAULTS, require_windows=False, expected_user=sandbox_probe.current_user(), timeout_s=0.5)
        base.update(kw)
        return base

    def complete(self, d, live_port, **kw):
        existing = Path(d) / "sealed"; existing.mkdir(exist_ok=True)
        work = Path(d) / "work"; work.mkdir(exist_ok=True)
        (Path(d) / "readable.txt").write_text("x")
        cfg = self.cfg(external=[["127.0.0.1", live_port]], loopback_live=[["127.0.0.1", live_port]],
                       must_not_read=[str(existing)], must_not_write=[str(existing)],
                       must_read=[str(Path(d) / "readable.txt")], must_write=[str(work)],
                       external_control_ok=[f"127.0.0.1:{live_port}"], loopback_live_control_ok=[f"127.0.0.1:{live_port}"],
                       cpu_cap_percent=100, cpu_probe_s=0.1, mem_limit_mb=0)
        cfg.update(kw)
        return cfg

    # --- review (round 3): identity from the token, positive control for blocked targets
    def test_identity_ignores_environment(self):
        real = sandbox_probe.current_user()
        with mock.patch.dict(os.environ, {"USER": "tib-subject", "LOGNAME": "tib-subject", "USERNAME": "tib-subject"}):
            self.assertEqual(sandbox_probe.current_user(), real)
            out = sandbox_probe.probe(self.cfg(expected_user="tib-subject"))
        if real != "tib-subject":
            self.assertIn(f"wrong_user:{real}", out["violations"])

    def test_blocked_without_positive_control_is_incomplete(self):
        port, stop = sandbox_probe.serve_live_listener()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = self.complete(d, port, external_control_ok=[], loopback_live_control_ok=[])
                with mock.patch("tib_ak79.sandbox_probe.connect_outcome", return_value="blocked"), \
                        mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=False), \
                        mock.patch("tib_ak79.sandbox_probe.alloc_succeeds", return_value=False), \
                        mock.patch("tib_ak79.sandbox_probe.path_outcome", side_effect=lambda p, w: "denied" if "sealed" in p else "allowed"):
                    out = sandbox_probe.probe(dict(cfg, mem_limit_mb=4))
        finally:
            stop()
        self.assertEqual(out["verdict"], "INCOMPLETE", out["violations"])
        self.assertTrue(any(i.startswith("no_positive_control:external") for i in out["incomplete"]))
        self.assertTrue(any(i.startswith("no_positive_control:loopback_live") for i in out["incomplete"]))

    def test_no_internet_for_everyone_never_looks_like_a_block(self):
        dead = _closed_port()   # nobody listens: the evaluator's own control fails, so it is not recorded as ok
        cfg = sandbox_probe.controlled_config(self.cfg(external=[["127.0.0.1", dead]], loopback_live=[["127.0.0.1", dead]]))
        self.assertEqual((cfg["external_control_ok"], cfg["loopback_live_control_ok"]), ([], []))

    def test_controlled_config_records_reachable_targets(self):
        port, stop = sandbox_probe.serve_live_listener()
        try:
            cfg = sandbox_probe.controlled_config(self.cfg(external=[["127.0.0.1", port]], loopback_live=[["127.0.0.1", port]]))
        finally:
            stop()
        self.assertEqual(cfg["external_control_ok"], [f"127.0.0.1:{port}"])
        self.assertEqual(cfg["loopback_live_control_ok"], [f"127.0.0.1:{port}"])

    # --- review 1: refused vs blocked, live listener required
    def test_refused_is_not_blocked(self):
        # Windows answers a closed loopback port only after its SYN retransmissions (~2 s): a short timeout there is a timeout, and a
        # timeout counts as "blocked" by design, so the closed-port check needs a window longer than that.
        t = 6.0 if sys.platform == "win32" else 0.5
        self.assertEqual(sandbox_probe.connect_outcome("127.0.0.1", _closed_port(), t), "refused")
        out = sandbox_probe.probe(self.cfg(loopback_live=[["127.0.0.1", _closed_port()]], external=[["127.0.0.1", _closed_port()]], timeout_s=t))
        self.assertEqual(out["verdict"], "INCOMPLETE" if not out["violations"] else "FAIL")
        self.assertTrue(any(i.startswith("inconclusive:loopback_live") for i in out["incomplete"]))

    def test_winsock_connection_refused_is_refused_not_blocked(self):
        err = OSError(10061, "refused"); err.winerror = 10061
        with mock.patch("tib_ak79.sandbox_probe.socket.create_connection", side_effect=err):
            self.assertEqual(sandbox_probe.connect_outcome("127.0.0.1", 1, 0.1), "refused")

    def test_live_listener_reachable_is_a_violation(self):
        port, stop = sandbox_probe.serve_live_listener()
        try:
            self.assertEqual(sandbox_probe.connect_outcome("127.0.0.1", port, 0.5), "connected")
            out = sandbox_probe.probe(self.cfg(loopback_live=[["127.0.0.1", port]]))
        finally:
            stop()
        self.assertEqual(out["verdict"], "FAIL")
        self.assertIn(f"loopback_live:127.0.0.1:{port}", out["violations"])

    def test_timeout_and_access_errors_count_as_blocked(self):
        with mock.patch("tib_ak79.sandbox_probe.socket.create_connection", side_effect=socket.timeout()):
            self.assertEqual(sandbox_probe.connect_outcome("10.255.255.1", 80, 0.1), "blocked")
        with mock.patch("tib_ak79.sandbox_probe.socket.create_connection", side_effect=PermissionError()):
            self.assertEqual(sandbox_probe.connect_outcome("1.1.1.1", 443, 0.1), "blocked")
        with mock.patch("tib_ak79.sandbox_probe.socket.create_connection", side_effect=OSError(errno.ENETUNREACH, "x")):
            self.assertEqual(sandbox_probe.connect_outcome("1.1.1.1", 443, 0.1), "blocked")
        with mock.patch("tib_ak79.sandbox_probe.socket.create_connection", side_effect=OSError(errno.EINVAL, "x")):
            self.assertEqual(sandbox_probe.connect_outcome("1.1.1.1", 443, 0.1), "error")

    # --- review 2: only PermissionError on an existing path is "denied"
    def test_missing_path_is_inconclusive_not_denied(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sandbox_probe.path_outcome(d + "/typo", False), "missing")
            self.assertEqual(sandbox_probe.path_outcome(d + "/typo", True), "missing")
            self.assertEqual(sandbox_probe.path_outcome(d, False), "allowed")
            self.assertEqual(sandbox_probe.path_outcome(d, True), "allowed")
            with mock.patch("pathlib.Path.iterdir", side_effect=PermissionError()):
                self.assertEqual(sandbox_probe.path_outcome(d, False), "denied")
            with mock.patch("pathlib.Path.write_text", side_effect=PermissionError()):
                self.assertEqual(sandbox_probe.path_outcome(d, True), "denied")

    def test_typo_in_denied_path_never_gives_green(self):
        port, stop = sandbox_probe.serve_live_listener()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = self.complete(d, port, must_not_read=[d + "/typo"])
                with mock.patch("tib_ak79.sandbox_probe.connect_outcome", return_value="blocked"), \
                        mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=False):
                    out = sandbox_probe.probe(cfg)
        finally:
            stop()
        self.assertNotEqual(out["verdict"], "BLOCKED_AS_REQUIRED")
        self.assertTrue(any("missing" in i for i in out["incomplete"]))

    # --- review 3: empty config is INCOMPLETE
    def test_empty_config_is_incomplete_and_exit_2(self):
        with mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=False):
            out = sandbox_probe.probe(dict(sandbox_probe.DEFAULTS, require_windows=False))
        self.assertEqual(out["verdict"], "INCOMPLETE")
        for need in ("expected_user_not_configured", "no_external_target", "no_live_loopback_target", "no_must_not_read",
                     "no_must_not_write", "no_must_read_control", "no_must_write_control", "no_cpu_cap_configured", "no_mem_limit_configured"):
            self.assertIn(need, out["incomplete"])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "c.json").write_text('{"require_windows": false}')
            with mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=False):
                self.assertEqual(sandbox_probe.main(["--config", str(Path(d) / "c.json")]), 2)

    # --- review 4: limits are part of the verdict
    def test_cpu_and_mem_limits_enter_violations(self):
        out = sandbox_probe.probe(self.cfg(cpu_cap_percent=10, cpu_probe_s=0.3, mem_limit_mb=4))
        self.assertTrue(any(v.startswith("cpu_cap_not_enforced") for v in out["violations"]))
        self.assertIn("mem_limit_not_enforced:4MB", out["violations"])
        self.assertEqual(out["verdict"], "FAIL")
        with mock.patch("tib_ak79.sandbox_probe.alloc_succeeds", return_value=False), \
                mock.patch("tib_ak79.sandbox_probe.cpu_share", return_value=0.09):
            out = sandbox_probe.probe(self.cfg(cpu_cap_percent=10, mem_limit_mb=4))
        self.assertFalse(any(v.startswith(("cpu_cap", "mem_limit")) for v in out["violations"]))

    # --- review 5: identity and positive controls
    def test_identity_admin_platform_and_controls(self):
        with tempfile.TemporaryDirectory() as d:
            port, stop = sandbox_probe.serve_live_listener()
            try:
                bad = sandbox_probe.probe(self.complete(d, port, expected_user="someone-else", must_write=[d + "/typo"]))
                win = sandbox_probe.probe(self.cfg(require_windows=True))
            finally:
                stop()
        self.assertTrue(any(v.startswith("wrong_user") for v in bad["violations"]))
        self.assertTrue(any(v.startswith("control_failed:must_write") for v in bad["violations"]))
        if sys.platform == "win32":     # require_windows is satisfied there
            self.assertFalse(any(v.startswith("not_windows") for v in win["violations"]))
        else:
            self.assertTrue(any(v.startswith("not_windows") for v in win["violations"]))
        with mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=True):
            self.assertIn("running_as_admin", sandbox_probe.probe(self.cfg())["violations"])

    def test_green_only_when_everything_proves_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            port, stop = sandbox_probe.serve_live_listener()
            try:
                cfg = self.complete(d, port, cpu_cap_percent=100)
                with mock.patch("tib_ak79.sandbox_probe.connect_outcome", return_value="blocked"), \
                        mock.patch("tib_ak79.sandbox_probe.is_admin", return_value=False), \
                        mock.patch("tib_ak79.sandbox_probe.path_outcome", side_effect=lambda p, w: "denied" if "sealed" in p else "allowed"), \
                        mock.patch("tib_ak79.sandbox_probe.alloc_succeeds", return_value=False):
                    out = sandbox_probe.probe(dict(cfg, mem_limit_mb=4))
            finally:
                stop()
        self.assertEqual((out["violations"], out["incomplete"], out["verdict"]), ([], [], "BLOCKED_AS_REQUIRED"))


class JobStructTests(unittest.TestCase):
    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "x64 layout")
    def test_struct_sizes_match_win32_x64(self):
        self.assertEqual(ctypes.sizeof(winjob.BasicLimit), 64)
        self.assertEqual(ctypes.sizeof(winjob.IoCounters), 48)
        self.assertEqual(ctypes.sizeof(winjob.ExtendedLimit), 144)
        self.assertEqual(ctypes.sizeof(winjob.CpuRate), 8)

    def test_limits_values_and_validation(self):
        lim = winjob.extended_limit(512, 4)
        self.assertEqual(lim.ProcessMemoryLimit, 512 * 1024 * 1024)
        self.assertEqual(lim.Basic.ActiveProcessLimit, 4)
        self.assertTrue(lim.Basic.LimitFlags & winjob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertEqual(winjob.cpu_rate(25).CpuRate, 2500)
        for bad in ((0, 1), (1, 0), (-1, 1)):
            with self.assertRaises(ValueError):
                winjob.extended_limit(*bad)
        for bad in (0, 101, -5):
            with self.assertRaises(ValueError):
                winjob.cpu_rate(bad)

    @unittest.skipIf(sys.platform == "win32", "non-Windows guard")
    def test_not_windows_refuses(self):
        with self.assertRaises(OSError):
            winjob.create_job(256, 2, 50)


if __name__ == "__main__":
    unittest.main()
