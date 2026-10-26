"""How the subject process is started.  The harness (run_subject) only needs a handle with stdin/stdout/poll/kill/wait/close.

LocalLauncher   - a plain child process in a scratch directory (development, fakes, the open set).
AgentLauncher   - asks an AGENT that already runs as the low-privilege account (tib-subject) to start the subject.  On Windows the
                  evaluator cannot start a process as another user without that user's password, which the operator types only
                  once, into the agent's own console; the agent then needs nothing else: it creates the run directory, applies the Job
                  Object limits and relays the subject's stdio.  The agent starts with:
                      python -I tib_ak79/launcher.py agent --address <pipe or socket> --work-root <dir> --python <exe> --authkey-file <f> --require-limits
                  and the evaluator and agent share an authkey read from a file (never an environment variable). The key protects the agent from other
users; the subject runs as the same account and could read it from the agent's memory, which gains it nothing the account does not
already have, because the evaluator trusts nothing the agent says.
Wire format (multiprocessing.connection bytes frames, never pickle): evaluator -> agent  b"S"+json start request, b"I"+stdin bytes,
b"C" close stdin, b"K" kill;  agent -> evaluator  b"R"+json {"limits_applied": bool}, b"O"+stdout bytes, b"X"+json {"returncode": n},
b"E"+reason.  The agent is the same identity as the subject, so it can be tampered with by the subject; it only starts and relays,
the verdict is the evaluator's MockEnv, so a lying agent cannot create a state it did not cause (it can only fail the run)."""
from __future__ import annotations

import json
import os
import queue
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener, answer_challenge, deliver_challenge
from pathlib import Path

DEFAULT_LIMITS = {"mem_mb": 512, "max_processes": 1, "cpu_percent": 50}
MAX_FRAME = 1 << 20          # no frame larger than 1 MiB is read, on either side
HANDSHAKE_S = 10.0           # connect, authenticate and first reply must each finish within this
AGENT_HANDSHAKE_S = 2.0      # the agent gives a new connection this long to answer the challenge
GRACE_S = 2.0                # how long past the run timeout an agent may take to say the subject has ended


def _clean_env() -> dict:
    keep = ("SYSTEMROOT",) if sys.platform == "win32" else ()
    return {k: os.environ[k] for k in keep if k in os.environ}


class LauncherError(RuntimeError):
    pass


class LauncherTimeout(LauncherError):
    """The agent did not answer in time: the run counts as a timeout, not as a crash of the evaluator."""


def _on_rm_error(func, path, *_exc) -> None:
    """rmtree error hook (onerror passes exc_info, onexc the exception): make the entry usable and try again; whatever goes wrong
    here (OSError, or a TypeError from the safe-fd path calling os.open without flags) must never escape and mask the run result."""
    import stat
    try:
        os.chmod(path, stat.S_IRWXU)
        func(path)
    except Exception:
        pass


def _rmtree_retry(path: str, attempts: int = 8) -> bool:
    """Remove a scratch directory; True if it is gone.  On Windows a just-killed child, an indexer or a virus scanner can hold it for
    a moment (WinError 32/5): retry with a growing pause.  A directory that stays is REPORTED by the caller (scratch_left), never
    ignored: the next subject runs as the same account and could read what is left."""
    import shutil
    kw = {"onexc": _on_rm_error} if sys.version_info >= (3, 12) else {"onerror": _on_rm_error}
    for i in range(attempts):
        try:
            shutil.rmtree(path, **kw)
        except Exception:
            pass
        if not os.path.exists(path):
            return True
        time.sleep(0.05 * (2 ** i))
    return False


def scratch_root() -> str:
    """Per-account private root for run directories (mode 0700), not the shared temp directory."""
    root = os.path.join(os.path.expanduser("~"), ".tib_ak79", "scratch")
    os.makedirs(root, mode=0o700, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


class LocalHandle:
    timed_out = False
    limits_applied = False

    scratch_left = None

    def __init__(self, argv: list[str], root: str | None = None):
        self._scratch = tempfile.mkdtemp(dir=root or scratch_root())      # mkdtemp: mode 0700
        try:
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          cwd=self._scratch, env=_clean_env())
        except BaseException:
            _rmtree_retry(self._scratch)
            raise
        self.stdin, self.stdout = self._proc.stdin, self._proc.stdout

    def poll(self):
        return self._proc.poll()

    def kill(self):
        self._proc.kill()

    def wait(self):
        return self._proc.wait()

    def close(self):
        for stream in (self._proc.stdin, self._proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()                  # the child must be gone before its working directory is removed
        if not _rmtree_retry(self._scratch):
            self.scratch_left = os.path.basename(self._scratch)


class LocalLauncher:
    limits_applied = False

    def __init__(self, root: str | None = None):
        self.root = root

    def start(self, argv: list[str], timeout: float = 30.0) -> LocalHandle:
        return LocalHandle(argv, self.root)


# ---------------------------------------------------------------- evaluator side
class _AgentStdin:
    def __init__(self, handle):
        self._h = handle

    def write(self, data: bytes) -> int:
        self._h._send(b"I" + bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        try:
            self._h._send(b"C")
        except (OSError, EOFError, ValueError):
            pass


class _AgentStdout:
    def __init__(self, handle):
        self._h, self._buf = handle, bytearray()

    def readline(self, limit: int = -1) -> bytes:
        while b"\n" not in self._buf and (limit < 0 or len(self._buf) < limit) and not self._h._eof:
            self._h._pump_one()
        cut = self._buf.find(b"\n") + 1 or len(self._buf)
        if limit >= 0:
            cut = min(cut, limit)
        out = bytes(self._buf[:cut]); del self._buf[:cut]
        return out

    def close(self):
        pass


_BROKEN = object()


def _loads_dict(raw: bytes):
    """JSON object or None: nothing the agent sends may raise out of the evaluator."""
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


class AgentHandle:
    def __init__(self, conn, request: dict, require_limits: bool, timeout: float):
        self._conn, self._lock, self._eof, self._rc = conn, threading.Lock(), False, None
        self._deadline = time.monotonic() + timeout + GRACE_S
        self.timed_out, self.protocol_error, self.limits_applied, self.scratch_left = False, False, False, None
        self.stdin, self.stdout = _AgentStdin(self), _AgentStdout(self)
        conn.send_bytes(b"S" + json.dumps(request).encode("utf-8"))
        first = self._recv(min(HANDSHAKE_S, timeout + GRACE_S))
        if first is _BROKEN:
            self.close(); raise LauncherError("agent closed the channel")
        if first is None:
            self.close(); raise LauncherTimeout("agent did not answer the start request")
        if first[:1] == b"E":
            self.close(); raise LauncherError("agent refused: " + first[1:200].decode("utf-8", "replace"))
        reply = _loads_dict(first[1:]) if first[:1] == b"R" else None
        if reply is None or not isinstance(reply.get("limits_applied"), bool):
            self.close(); raise LauncherError("agent protocol error")
        self.limits_applied = reply["limits_applied"]
        if require_limits and not self.limits_applied:
            self.kill(); self.close()
            raise LauncherError("agent could not apply the resource limits")

    def _recv(self, wait: float):
        """One frame; None if nothing arrived in time; _BROKEN if the channel failed or the frame was oversized."""
        try:
            if not self._conn.poll(max(0.0, wait)):
                return None
            return self._conn.recv_bytes(MAX_FRAME)
        except (EOFError, OSError, ValueError):
            return _BROKEN

    def _send(self, frame: bytes) -> None:
        with self._lock:
            self._conn.send_bytes(frame)

    def _end(self, rc: int, timed_out: bool = False, protocol_error: bool = False) -> None:
        self._eof, self._rc = True, rc
        self.timed_out = self.timed_out or timed_out
        self.protocol_error = self.protocol_error or protocol_error
        self.close()

    def _pump_one(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self._end(-1, timed_out=True); return
        frame = self._recv(min(remaining, 0.5))
        if frame is _BROKEN:
            self._end(-1, protocol_error=True); return
        if frame is None:
            if time.monotonic() >= self._deadline:
                self._end(-1, timed_out=True)
            return
        tag, body = frame[:1], frame[1:]
        if tag == b"O":
            self.stdout._buf += body
        elif tag == b"X":
            msg = _loads_dict(body)
            rc = msg.get("returncode") if msg else None
            if isinstance(rc, int) and not isinstance(rc, bool):
                if msg.get("scratch_left") is True:
                    self.scratch_left = "agent"
                self._end(rc)
            else:
                self._end(-1, protocol_error=True)
        else:                                   # E, or a tag the protocol does not have
            self._end(-1, protocol_error=True)

    def poll(self):
        return self._rc if self._eof else None

    def kill(self):
        try:
            self._send(b"K")
        except (OSError, EOFError, ValueError):
            pass

    def wait(self):
        while not self._eof:
            self._pump_one()
        return self._rc

    def close(self):
        try:
            self._conn.close()
        except OSError:
            pass


class AgentLauncher:
    def __init__(self, address, authkey: bytes, limits: dict | None = None, require_limits: bool = True, family: str | None = None):
        self.address, self.authkey, self.family = address, authkey, family
        self.limits, self.require_limits = dict(limits or DEFAULT_LIMITS), require_limits

    def _connect(self, wait: float):
        """Connect + authenticate in a worker so that a silent agent cannot hang the evaluator."""
        box: dict = {}

        def work():
            try:
                box["conn"] = Client(self.address, family=self.family, authkey=self.authkey)
            except (OSError, EOFError, AuthenticationError) as e:
                box["err"] = e
        t = threading.Thread(target=work, daemon=True); t.start(); t.join(wait)
        if t.is_alive():
            raise LauncherTimeout("agent handshake timed out")
        if "err" in box:
            raise LauncherError(f"agent unreachable: {type(box['err']).__name__}") from box["err"]
        return box["conn"]

    def start(self, argv: list[str], timeout: float = 30.0) -> AgentHandle:
        conn = self._connect(min(HANDSHAKE_S, timeout + GRACE_S))
        return AgentHandle(conn, {"argv": list(argv), "limits": self.limits}, self.require_limits, timeout)


# ---------------------------------------------------------------- agent side (runs as the low-privilege user)
def _check_request(req: dict, work_root: Path, pythons: set) -> str | None:
    argv = req.get("argv")
    if not isinstance(argv, list) or len(argv) < 3 or not all(isinstance(a, str) for a in argv):
        return "bad_argv"
    if os.path.normcase(os.path.realpath(argv[0])) not in pythons:
        return "interpreter_not_allowed"
    if argv[1] != "-I":
        return "isolated_mode_required"
    script = Path(os.path.realpath(argv[2]))
    root = Path(os.path.realpath(work_root))
    if root not in script.parents:
        return "script_outside_work_root"
    return None


def _spawn(argv: list, scratch: str, limits: dict):
    """Start the subject.  On Windows the process is created SUSPENDED, put into the Job Object, and only then resumed, so that no
    uncontained instruction of the subject ever runs.  Returns (proc, limits_applied, job)."""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000004   # CREATE_SUSPENDED
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=scratch,
                            env=_clean_env(), **kw)
    if sys.platform != "win32":
        return proc, False, None
    try:
        from tib_ak79 import winjob
        job = winjob.create_job(int(limits["mem_mb"]), int(limits["max_processes"]), int(limits["cpu_percent"]))
        winjob.assign(job, int(proc._handle))
        winjob.resume_process(int(proc._handle))
        return proc, True, job
    except Exception:
        proc.kill()
        return proc, False, None


def _close_pipes(proc) -> None:
    for stream in (proc.stdin, proc.stdout):
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _handle(conn, work_root: Path, allowed: set, require_limits: bool) -> None:
    try:
        if not conn.poll(HANDSHAKE_S):
            return
        first = conn.recv_bytes(MAX_FRAME)
        req = _loads_dict(first[1:]) if first[:1] == b"S" else None
        bad = "bad_request" if req is None else _check_request(req, work_root, allowed)
        limits = req.get("limits") if req else None
        if not bad and not (isinstance(limits, dict) and all(isinstance(limits.get(k), int) for k in DEFAULT_LIMITS)):
            bad = "bad_limits"
        if bad:
            conn.send_bytes(b"E" + bad.encode()); return
        runs = work_root / "runs"; runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        scratch = tempfile.mkdtemp(dir=runs)
        left = False
        try:
            proc, applied, _job = _spawn(req["argv"], scratch, limits)
            if require_limits and not applied:
                proc.kill(); proc.wait(); _close_pipes(proc)
                conn.send_bytes(b"E" + b"limits_not_applied"); return
            conn.send_bytes(b"R" + json.dumps({"limits_applied": applied}).encode())

            def out_pump():
                try:
                    while True:
                        chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                        if not chunk:
                            break
                        conn.send_bytes(b"O" + chunk)
                except (OSError, ValueError, EOFError):
                    pass
            t = threading.Thread(target=out_pump, daemon=True); t.start()

            def in_pump():
                try:
                    while True:
                        frame = conn.recv_bytes(MAX_FRAME)
                        tag, body = frame[:1], frame[1:]
                        if tag == b"I":
                            proc.stdin.write(body); proc.stdin.flush()
                        elif tag == b"C":
                            proc.stdin.close()
                        elif tag == b"K":
                            proc.kill()
                except (OSError, ValueError, EOFError):
                    proc.kill()
            threading.Thread(target=in_pump, daemon=True).start()
            rc = proc.wait(); t.join(5)
            _close_pipes(proc)
            left = not _rmtree_retry(scratch)          # remove first, so that the report below is truthful
            try:
                conn.send_bytes(b"X" + json.dumps({"returncode": rc, "scratch_left": left}).encode())
            except (OSError, ValueError):
                pass
        finally:
            if not left:
                _rmtree_retry(scratch)
    except (OSError, EOFError, ValueError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _authenticate(conn, authkey: bytes) -> bool:
    """Server side of the multiprocessing HMAC handshake, with a deadline.  On POSIX the socket gets a receive timeout (the descriptor
    is shared, so a duplicate carries the option), and a watchdog closes the connection after the deadline on every platform."""
    timer = threading.Timer(AGENT_HANDSHAKE_S, lambda: conn.close())
    timer.daemon = True; timer.start()
    dup = None
    try:
        if sys.platform != "win32":
            dup = socket.socket(fileno=os.dup(conn.fileno()))
            dup.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", int(AGENT_HANDSHAKE_S), 0))
        deliver_challenge(conn, authkey)
        answer_challenge(conn, authkey)
        return not timer.finished.is_set()
    except (AuthenticationError, OSError, EOFError, ValueError):
        return False
    finally:
        timer.cancel()
        if dup is not None:
            try:                      # the option lives on the shared socket: lift it, or an idle subject would be killed by the pump
                dup.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", 0, 0))
            except OSError:
                pass
            dup.close()


def serve(listener: Listener, work_root: Path, pythons: list, require_limits: bool = True, runs: int | None = None,
          authkey: bytes = b"", max_handshakes: int = 32) -> None:
    """Serve runs one at a time.  `listener` must be built WITHOUT an authkey: accepting is then instant and the challenge is
    answered here, per connection, in its own thread with a 2 s deadline.  A wrong key, a dropped connection or a client that only
    connects therefore cannot stop the agent or delay the next real client; at most `max_handshakes` handshakes run at once
    (more are closed at once), and each is cut off after the deadline."""
    if len(authkey) < 16:
        raise ValueError("authkey too short")
    work_root = Path(work_root)
    allowed = {os.path.normcase(os.path.realpath(p)) for p in pythons}
    queue_: queue.Queue = queue.Queue()
    slots = threading.BoundedSemaphore(max_handshakes)
    stop = threading.Event()

    def handshake(conn):
        try:
            if _authenticate(conn, authkey):
                queue_.put(conn); return
            conn.close()
        except Exception:
            try:
                conn.close()
            except OSError:
                pass
        finally:
            slots.release()

    def acceptor():
        while not stop.is_set():
            try:
                conn = listener.accept()
            except (OSError, EOFError, AuthenticationError):
                time.sleep(0.05)              # a closed listener or a failed accept: keep going, do not spin
                continue
            if not slots.acquire(blocking=False):
                conn.close(); continue
            threading.Thread(target=handshake, args=(conn,), daemon=True).start()
    threading.Thread(target=acceptor, daemon=True).start()
    done = 0
    try:
        while runs is None or done < runs:
            _handle(queue_.get(), work_root, allowed, require_limits)
            done += 1
    finally:
        stop.set()


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["agent"])
    ap.add_argument("--address", required=True)
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--python", action="append", required=True)
    ap.add_argument("--authkey-file", required=True, help="file with the shared key, readable by the agent account and the evaluator only")
    ap.add_argument("--require-limits", action="store_true")
    a = ap.parse_args(argv)
    key = Path(a.authkey_file).read_bytes().strip()
    if len(key) < 16:
        print("authkey too short (>= 16 bytes)", file=sys.stderr); return 2
    with Listener(a.address) as lst:          # no authkey here: serve() authenticates each connection with a deadline
        serve(lst, Path(a.work_root), a.python, a.require_limits, authkey=key)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
