"""Custodian manifest of the system under test: what exactly was run, signed into the ledger.

All digests are computed here from files on disk (never taken from the caller), so a label cannot stand in for a build."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from ._compat import SSH_SIG_PREFIX

from .sealed import canonical, hash_code_files, system_key

SCHEMA = "tib_ak79.manifest.v1"
EVENT = "tib_manifest"
MANIFEST_SIGNER = "evaluator"          # only the control plane vouches for what is being run
RECEIPT_EVENT = "tib_sandbox_receipt"
RECEIPT_SIGNER = "attestor"          # role name of the host ledger that attests the sandbox probe
PROBE_SCHEMA = "tib_ak79.sandbox_probe.v2"
PROBE_OK = "BLOCKED_AS_REQUIRED"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_set_sha256(tasks: list) -> str:
    return hashlib.sha256(canonical(tasks)).hexdigest()


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(canonical(manifest)).hexdigest()


def build_manifest(code_root: Path, code_files, data_files=(), model_digest: str | None = None, tasks: list | None = None) -> dict:
    """code_files: the harness/system code (hash_code_files rules: inside root, no symlinks).  data_files: task sets etc. by content."""
    root = Path(code_root).resolve()
    code_sha = hash_code_files(root, code_files)
    data = {}
    for name in sorted({str(f).replace("\\", "/") for f in data_files}):
        path = root / name
        if path.is_symlink() or not path.resolve().is_file() or root not in path.resolve().parents:
            raise ValueError(f"bad data file: {name}")
        data[name] = _sha(path.resolve())
    return {"schema": SCHEMA, "code_sha256": code_sha, "model_digest": model_digest, "data_sha256": data,
            "system_key": system_key(code_sha, model_digest), "python": list(sys.version_info[:3]),
            "task_set_sha256": task_set_sha256(tasks) if tasks is not None else None}


def sign_manifest(ledger, manifest: dict, task_id: str = "tib_ak79") -> dict:
    """Append the manifest to the signed, hash-chained ledger under the one role allowed to vouch for it."""
    return ledger.append(EVENT, task_id, MANIFEST_SIGNER, manifest)


def is_signed(ledger, manifest: dict, task_id: str = "tib_ak79") -> bool:
    """True only if the chain verifies and an event of this task carries exactly this manifest."""
    if ledger.verify()["status"] != "PASS":
        return False
    want = canonical(manifest)
    return any(ev["type"] == EVENT and ev["signer"] == MANIFEST_SIGNER and canonical(ev["payload"]) == want
               for ev in ledger.events(task_id))


def record_sandbox_receipt(ledger, probe_result: dict, private_key, task_id: str = "tib_ak79") -> dict:
    """The attestor's record of a whole probe result (not just its verdict), signed with the attestor's SSH key (there is no HMAC path)."""
    return ledger.append_attested(RECEIPT_EVENT, task_id, probe_result, private_key)


def sandbox_receipt_check(ledger, expected_user: str, max_age_s: float = 3600.0, expected_platform: str = "win32",
                          task_id: str = "tib_ak79", now: datetime | None = None) -> tuple[str | None, dict | None]:
    """(None, the accepted receipt event) if a fresh, attestor-signed, complete probe receipt for `expected_user` exists on a verified
    chain; else (reason, None)."""
    if ledger.verify()["status"] != "PASS":
        return "ledger_not_verified", None
    now = now or datetime.now(timezone.utc)
    receipts = [ev for ev in ledger.events(task_id) if ev["type"] == RECEIPT_EVENT and ev["signer"] == RECEIPT_SIGNER]
    if not receipts:
        return "no_sandbox_receipt", None
    ev = receipts[-1]   # the newest receipt decides: a later failing probe revokes an earlier good one
    if not str(ev.get("sig", "")).startswith(SSH_SIG_PREFIX):
        return "receipt_not_ssh_signed", None   # verify() above checked the signature; a legacy HMAC attestor event is refused outright
    p = ev["payload"]
    if not isinstance(p, dict):
        return "receipt_malformed", None
    try:
        age = (now - datetime.fromisoformat(ev["utc"])).total_seconds()
    except (ValueError, TypeError):
        return "receipt_time_malformed", None
    if age < -60 or age > max_age_s:
        return "receipt_stale", None
    if p.get("schema") != PROBE_SCHEMA or p.get("verdict") != PROBE_OK:
        return "receipt_not_blocked_as_required", None
    if p.get("incomplete") != [] or p.get("violations") != []:
        return "receipt_incomplete_or_violations", None
    if not expected_user or p.get("user") != expected_user:
        return "receipt_wrong_user", None
    if p.get("platform") != expected_platform:
        return "receipt_wrong_platform", None
    return None, ev


def sandbox_receipt_problem(*args, **kwargs) -> str | None:
    return sandbox_receipt_check(*args, **kwargs)[0]


class Custody:
    """Everything a run on a closed set must prove before the subject process starts."""

    def __init__(self, ledger, manifest: dict, expected_user: str, max_age_s: float = 3600.0, expected_platform: str = "win32",
                 now=None):
        self.ledger, self.manifest, self.expected_user = ledger, manifest, expected_user
        self.max_age_s, self.expected_platform, self.now = max_age_s, expected_platform, now
        self.receipt: dict | None = None     # the sandbox receipt event the last successful check relied on

    def problem(self, tasks: list) -> str | None:
        if not is_signed(self.ledger, self.manifest):
            return "manifest_not_signed"
        if self.manifest.get("task_set_sha256") != task_set_sha256(tasks):
            return "manifest_does_not_cover_these_tasks"
        reason, ev = sandbox_receipt_check(self.ledger, self.expected_user, self.max_age_s, self.expected_platform, now=self.now)
        self.receipt = ev
        return reason
