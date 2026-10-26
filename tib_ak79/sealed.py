"""Sealed-set primitives. No gold answer or salt ever enters a public manifest; only commitments and a Merkle root do."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

from ._compat import file_lock

DOMAIN_LEAF = b"TIB-AK79/leaf/v1\x00"
DOMAIN_NODE = b"TIB-AK79/node/v1\x00"
DOMAIN_COMMIT = b"TIB-AK79/commit/v1\x00"

# A subject may see a sealed task only if the task cannot leave the machine; a cloud provider "burns" it.
LOCAL_SUBJECTS = frozenset({"fake", "local_model"})
CLOUD_SUBJECTS = frozenset({"claude_code_cli", "codex_cli", "openai_api", "anthropic_api", "other_cloud"})


def canonical(obj) -> bytes:
    """Canonical JSON: sorted keys, no spaces, UTF-8. Field boundaries are explicit, so concatenation is unambiguous."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def commit(case_id: str, task, gold, salt: bytes) -> str:
    if len(salt) < 16:
        raise ValueError("salt must be at least 16 bytes")
    body = canonical({"id": case_id, "task": task, "gold": gold, "salt": salt.hex()})
    return hashlib.sha256(DOMAIN_COMMIT + body).hexdigest()


def merkle_root(commitments: list[str]) -> str:
    """Leaves sorted by value; an odd node is promoted unchanged (no duplication, so no second-preimage by repetition)."""
    if not commitments:
        raise ValueError("empty set")
    level = [hashlib.sha256(DOMAIN_LEAF + bytes.fromhex(c)).digest() for c in sorted(commitments)]
    while len(level) > 1:
        nxt = [hashlib.sha256(DOMAIN_NODE + level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


def public_manifest(cases: dict[str, tuple[object, object, bytes]]) -> dict:
    """cases: id -> (task, gold, salt). Returns the part that may be published."""
    commits = {cid: commit(cid, t, g, s) for cid, (t, g, s) in cases.items()}
    return {"schema": "tib_ak79.sealed_manifest.v1", "count": len(commits), "commitments": commits,
            "merkle_root": merkle_root(list(commits.values()))}


def _safe_merkle_ok(pub: dict, manifest: dict) -> bool:
    try:
        return hmac.compare_digest(merkle_root(list(pub.values())).encode(), str(manifest.get("merkle_root", "")).encode("utf-8"))
    except (ValueError, TypeError):
        return False


def verify(manifest: dict, cases: dict[str, tuple[object, object, bytes]]) -> list[str]:
    """Custodian-side check. Returns a list of problems (empty = intact): swapped gold, edited task, added/removed case."""
    problems = []
    pub = manifest.get("commitments", {})
    for cid in sorted(set(pub) | set(cases)):
        if cid not in cases:
            problems.append(f"missing_case:{cid}")
        elif cid not in pub:
            problems.append(f"unlisted_case:{cid}")
        else:
            t, g, s = cases[cid]
            if not isinstance(pub[cid], str) or not hmac.compare_digest(commit(cid, t, g, s).encode(), pub[cid].encode("utf-8")):
                problems.append(f"commitment_mismatch:{cid}")
    if not problems and not _safe_merkle_ok(pub, manifest):
        problems.append("merkle_root_mismatch")
    return problems


def hash_code_files(root: Path, rel_files) -> str:
    """Digest of the code under test: file CONTENTS only, sorted by content digest (a copy under another name changes
    nothing). Every path must resolve inside root; symlinks and missing files are refused."""
    root = Path(root).resolve()
    contents = []
    for name in {str(f).replace("\\", "/") for f in rel_files}:
        path = root / name
        if path.is_symlink():
            raise ValueError(f"code file is a symlink: {name}")
        resolved = path.resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError(f"code file outside code_root: {name}")
        if not resolved.is_file():
            raise ValueError(f"code file missing: {name}")
        contents.append(hashlib.sha256(resolved.read_bytes()).digest())
    if not contents:
        raise ValueError("no code files")
    h = hashlib.sha256(DOMAIN_COMMIT + b"code\x00")
    for d in sorted(set(contents)):
        h.update(d)
    return h.hexdigest()


def system_key(code_sha256: str | None, model_digest: str | None) -> str:
    """Budget key from digests the custodian computed itself; a label or a caller-supplied string never enters."""
    digests = {k: v for k, v in (("code_sha256", code_sha256), ("model_digest", model_digest)) if v}
    if not digests:
        raise ValueError("system identity needs a code digest or a model digest")
    return hashlib.sha256(DOMAIN_COMMIT + b"system\x00" + canonical(digests)).hexdigest()


class SealedBudget:
    """Caps scoring rounds per system identity on the sealed set (anti adaptive overfitting) and tracks burned cases.

    The identity is computed here from the custodian's own configuration: `code_files` (fixed once, at construction, i.e.
    in the system-package manifest - a caller cannot pick a different subset) and the model digest the custodian resolves
    (`model_digest_of`, e.g. a digest from a model registry) for a name that must be in `allowed_models`.  Callers supply neither
    files nor digests.  State lives in the signed
    ledger (injected), verified before each read; check-and-write runs under an exclusive file lock, so concurrent
    callers cannot both take the last round.
    """

    def __init__(self, ledger, set_id: str, case_ids, max_rounds_per_system: int = 1, signer: str = "verifier",
                 code_root: Path | str = ".", code_files=(), model_digest_of=None, allowed_models=()):
        self.ledger, self.task_id, self.signer = ledger, f"tib-sealed:{set_id}", signer
        self.max_rounds = max_rounds_per_system
        self.ids = set(case_ids)
        self.code_root = Path(code_root)
        self.code_files = tuple(code_files)
        self.allowed_models = frozenset(allowed_models)
        self.model_digest_of = model_digest_of
        self.lock_path = Path(ledger.root) / "tib_sealed_budget.lock"

    def _state(self):
        report = self.ledger.verify()
        if report["status"] != "PASS":
            raise PermissionError(f"ledger failed verification: {report['problems']}")
        rounds: dict[str, int] = {}
        burned: set[str] = set()
        for ev in self.ledger.events(self.task_id):
            if ev["type"] == "tib_sealed_round":
                rounds[ev["payload"]["system"]] = rounds.get(ev["payload"]["system"], 0) + 1
            elif ev["type"] == "tib_sealed_burn":
                burned |= set(ev["payload"]["cases"])
        return rounds, burned

    @property
    def rounds(self) -> dict[str, int]:
        return self._state()[0]

    @property
    def burned(self) -> set[str]:
        return self._state()[1]

    def exposure_allowed(self, subject_kind: str) -> bool:
        if subject_kind in LOCAL_SUBJECTS:
            return True
        if subject_kind in CLOUD_SUBJECTS:
            return False  # sealed text must not reach a third party
        raise ValueError(f"unknown subject kind: {subject_kind}")

    def identity_key(self, model_name: str | None = None) -> str:
        code = hash_code_files(self.code_root, self.code_files) if self.code_files else None
        model = None
        if model_name:
            if model_name not in self.allowed_models:
                raise ValueError(f"model not allowed by the custodian: {model_name}")
            if self.model_digest_of is None:
                raise ValueError("model_name given but no model_digest_of resolver configured")
            model = self.model_digest_of(model_name)
            if not isinstance(model, str) or not re.fullmatch(r"[0-9a-f]{64}", model):
                raise ValueError("resolver returned no sha256 digest")
        return system_key(code, model)

    def start_round(self, subject_kind: str, model_name: str | None = None, allow_burn: bool = False) -> str:
        key = self.identity_key(model_name)
        with file_lock(self.lock_path):
            rounds, _ = self._state()
            used = rounds.get(key, 0)
            if used >= self.max_rounds:
                raise PermissionError(f"sealed budget exhausted for {key[:12]}: {used}/{self.max_rounds}")
            if not self.exposure_allowed(subject_kind):
                if not allow_burn:
                    raise PermissionError("cloud subject on sealed set: refused (pass allow_burn=True to burn the cases)")
                self.ledger.append("tib_sealed_burn", self.task_id, self.signer,
                                   {"system": key, "kind": subject_kind, "cases": sorted(self.ids)})
            self.ledger.append("tib_sealed_round", self.task_id, self.signer, {"system": key, "kind": subject_kind})
        return key


def leaks(text: str, canaries: list[str]) -> list[str]:
    """Canary strings found in text (repo files, logs, exports)."""
    return [c for c in canaries if c and c in text]


_TOKEN = re.compile(r"\w+", re.UNICODE)


def ngram_overlap(a: str, b: str, n: int = 8) -> bool:
    """True if a and b share any n-word window; a text shorter than n words matches when its whole word sequence occurs
    in the other (used to exclude reference-exam cases already seen elsewhere)."""
    wa = [w.lower() for w in _TOKEN.findall(a)]
    wb = [w.lower() for w in _TOKEN.findall(b)]
    if not wa or not wb:
        return False
    k = min(n, len(wa), len(wb))
    grams = {tuple(wa[i:i + k]) for i in range(len(wa) - k + 1)}
    return any(tuple(wb[i:i + k]) in grams for i in range(len(wb) - k + 1))
