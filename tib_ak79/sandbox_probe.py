"""Probe run AS the unprivileged subject account (tib-subject): it reports what that account can actually do.

Honest by construction: "blocked" is claimed only for evidence that proves it (timeout or an access error on a target that
is live / a path that exists); "refused" and "no such file" are INCONCLUSIVE, never green.  A configuration that does not
test the minimum (external + live loopback + read + write + both positive controls + identity) is INCOMPLETE.
Verdicts: BLOCKED_AS_REQUIRED (exit 0), FAIL (exit 1), INCOMPLETE (exit 2).

Run AS tib-subject:  python -I -m tib_ak79.sandbox_probe --config probe.json
The evaluator side (another account) starts the live loopback listener with `serve_live_listener()` and puts its port in
the config as {"loopback_live": [["127.0.0.1", port]]}.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

DEFAULTS = {
    "expected_user": None,       # e.g. "tib-subject"; required for a complete run
    "require_windows": True,
    "external": [],              # [[host, port], ...] reachable from the internet (1.1.1.1:443 ...)
    "external_control_ok": [],    # "host:port" the EVALUATOR (another account, with network) itself connected to: positive control
    "loopback_live_control_ok": [],  # same for loopback_live; fill both with controlled_config()
    "loopback_live": [],         # [[host, port]] listeners that ARE up, owned by another account (harness-started)
    "loopback_info": [],         # known local services (model servers, UIs, helper bridges...): connected = violation, anything else = info only
    "must_not_read": [],         # paths that exist and must be denied (your sealed-set directory ...)
    "must_not_write": [],        # paths that exist and must be denied
    "must_read": [],             # positive control: must be readable
    "must_write": [],            # positive control: must be writable (the scratch directory)
    "mem_limit_mb": 0,           # >0: the Job limit; the probe tries 2x and EXPECTS failure
    "cpu_cap_percent": 0,        # >0: the Job hard cap; the probe burns CPU and EXPECTS a share <= cap (+tolerance)
    "cpu_probe_s": 1.0,
    "timeout_s": 2.0,
}
CPU_TOLERANCE = 0.15
_BLOCKING_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EACCES, errno.EPERM}
for _name in ("WSAEACCES", "WSAENETUNREACH", "WSAEHOSTUNREACH"):
    if hasattr(errno, _name):
        _BLOCKING_ERRNOS.add(getattr(errno, _name))


def connect_outcome(host: str, port: int, timeout: float) -> str:
    """connected | blocked (timeout / access / unreachable) | refused (inconclusive) | error (inconclusive)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "connected"
    except ConnectionRefusedError:
        return "refused"          # the host answered: nothing was listening or a reject rule - does NOT prove blocking
    except (TimeoutError, socket.timeout):
        return "blocked"
    except PermissionError:
        return "blocked"
    except OSError as e:
        if getattr(e, "winerror", None) == 10061:      # WSAECONNREFUSED: the host answered, which is not proof of blocking
            return "refused"
        return "blocked" if e.errno in _BLOCKING_ERRNOS else "error"


def path_outcome(path: str, write: bool) -> str:
    """allowed | denied (PermissionError on an existing path) | missing (inconclusive) | error (inconclusive)."""
    p = Path(path)
    try:
        if not p.exists() and not p.is_symlink():
            return "missing"
        if write:
            target = (p if p.is_dir() else p.parent) / ".tib_probe_write_test"
            target.write_text("x")
            target.unlink()
        elif p.is_dir():
            next(iter(p.iterdir()), None)
        else:
            with p.open("rb") as f:
                f.read(1)
        return "allowed"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"


def current_user() -> str:
    """Account of the process TOKEN, not the environment (USER/LOGNAME/USERNAME can be set by anybody)."""
    if sys.platform == "win32":
        import ctypes
        size = ctypes.c_uint32(257)
        buf = ctypes.create_unicode_buffer(257)
        if not ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)):
            raise OSError("GetUserNameW failed")
        return buf.value
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def controlled_config(cfg: dict) -> dict:
    """Evaluator side: connect to every external / live loopback target from the evaluator's own account and record which
    ones are reachable there.  Only those can later prove 'blocked' for the subject (a target that is down for everyone
    looks blocked too)."""
    t = float(cfg["timeout_s"])
    out = dict(cfg)
    out["external_control_ok"] = [f"{h}:{p}" for h, p in cfg["external"] if connect_outcome(h, p, t) == "connected"]
    out["loopback_live_control_ok"] = [f"{h}:{p}" for h, p in cfg["loopback_live"] if connect_outcome(h, p, t) == "connected"]
    return out


def is_admin() -> bool:
    try:
        if sys.platform == "win32":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return True  # cannot tell: treat as privileged


def alloc_succeeds(mb: int) -> bool:
    try:
        buf = bytearray(mb * 1024 * 1024)
        buf[::4096] = b"\x01" * len(buf[::4096])
        return True
    except (MemoryError, OSError):
        return False


def cpu_share(seconds: float) -> float:
    w0, c0 = time.perf_counter(), time.process_time()
    while time.perf_counter() - w0 < seconds:
        pass
    wall = time.perf_counter() - w0
    return round((time.process_time() - c0) / wall, 3) if wall else 0.0


def serve_live_listener(host: str = "127.0.0.1"):
    """Evaluator side: a live listener owned by ANOTHER account. Returns (port, stop()). Accepts and closes connections."""
    srv = socket.socket()
    srv.bind((host, 0))
    srv.listen(8)
    srv.settimeout(0.2)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.close()
            except (socket.timeout, OSError):
                pass
        srv.close()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return srv.getsockname()[1], lambda: (stop.set(), t.join(1))


def probe(cfg: dict) -> dict:
    t = float(cfg["timeout_s"])
    violations, incomplete = [], []
    user = current_user()
    out = {"schema": "tib_ak79.sandbox_probe.v2", "platform": sys.platform, "user": user, "scratch": tempfile.gettempdir()}

    # identity
    if not cfg["expected_user"]:
        incomplete.append("expected_user_not_configured")
    elif user.lower().split("\\")[-1] != str(cfg["expected_user"]).lower().split("\\")[-1]:
        violations.append(f"wrong_user:{user}")
    if cfg["require_windows"] and sys.platform != "win32":
        violations.append(f"not_windows:{sys.platform}")
    if is_admin():
        violations.append("running_as_admin")

    # network
    out["external"] = {f"{h}:{p}": connect_outcome(h, p, t) for h, p in cfg["external"]}
    out["loopback_live"] = {f"{h}:{p}": connect_outcome(h, p, t) for h, p in cfg["loopback_live"]}
    out["loopback_info"] = {f"{h}:{p}": connect_outcome(h, p, t) for h, p in cfg["loopback_info"]}
    if not cfg["external"]:
        incomplete.append("no_external_target")
    if not cfg["loopback_live"]:
        incomplete.append("no_live_loopback_target")
    controls = {"external": set(cfg["external_control_ok"]), "loopback_live": set(cfg["loopback_live_control_ok"])}
    for group in ("external", "loopback_live"):
        for k, v in out[group].items():
            if v == "connected":
                violations.append(f"{group}:{k}")
            elif v != "blocked":
                incomplete.append(f"inconclusive:{group}:{k}:{v}")
            elif k not in controls[group]:  # blocked, but nobody showed the target is up: could be down for everyone
                incomplete.append(f"no_positive_control:{group}:{k}")
    violations += [f"loopback_info:{k}" for k, v in out["loopback_info"].items() if v == "connected"]

    # filesystem: denied must be PermissionError on an existing path; controls must work
    out["must_not_read"] = {p: path_outcome(p, False) for p in cfg["must_not_read"]}
    out["must_not_write"] = {p: path_outcome(p, True) for p in cfg["must_not_write"]}
    out["must_read"] = {p: path_outcome(p, False) for p in cfg["must_read"]}
    out["must_write"] = {p: path_outcome(p, True) for p in cfg["must_write"]}
    for name, wanted in (("must_not_read", "denied"), ("must_not_write", "denied")):
        if not cfg[name]:
            incomplete.append(f"no_{name}")
        for p, r in out[name].items():
            if r == "allowed":
                violations.append(f"{name}:{p}")
            elif r != wanted:
                incomplete.append(f"inconclusive:{name}:{p}:{r}")
    for name in ("must_read", "must_write"):
        if not cfg[name]:
            incomplete.append(f"no_{name}_control")
        violations += [f"control_failed:{name}:{p}:{r}" for p, r in out[name].items() if r != "allowed"]

    # limits: expected values are part of the check
    if cfg["cpu_cap_percent"] > 0:
        share = out["cpu_share"] = cpu_share(float(cfg["cpu_probe_s"]))
        if share > cfg["cpu_cap_percent"] / 100 + CPU_TOLERANCE:
            violations.append(f"cpu_cap_not_enforced:{share}>{cfg['cpu_cap_percent']}%")
    else:
        incomplete.append("no_cpu_cap_configured")
    if cfg["mem_limit_mb"] > 0:
        ok = out["mem_alloc_ok"] = alloc_succeeds(cfg["mem_limit_mb"] * 2)  # a hard limit may also kill us: no report = FAIL
        if ok:
            violations.append(f"mem_limit_not_enforced:{cfg['mem_limit_mb']}MB")
    else:
        incomplete.append("no_mem_limit_configured")

    out["violations"], out["incomplete"] = sorted(violations), sorted(set(incomplete))
    out["verdict"] = "FAIL" if violations else ("INCOMPLETE" if incomplete else "BLOCKED_AS_REQUIRED")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="JSON file overriding DEFAULTS")
    args = ap.parse_args(argv)
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
    result = probe(cfg)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"BLOCKED_AS_REQUIRED": 0, "FAIL": 1, "INCOMPLETE": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
