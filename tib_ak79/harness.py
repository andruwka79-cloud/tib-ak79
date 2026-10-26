"""Offline run assembly: variant -> env + evidence feed -> subject process -> real-state scoring.  No network anywhere in this path.

A record has two faces: `public_record` (task id, epoch, score, critical COUNT) is what may leave the custodian or enter an Inspect
log; the full record (flag names, per-action verdicts, shown ids) stays with the custodian.

Authorization fails closed: a task list is run without custody only if every task is, byte for byte, an entry of the checked-in open
set.  Anything else (a sealed set, an edited task, a foreign list) needs a `Custody`: a manifest signed by the control plane that covers
exactly these tasks, plus a fresh attestor-signed sandbox receipt.  The caller never declares a set "sealed" or "open"."""
from __future__ import annotations

import json
from pathlib import Path

from .action_env import aggregate
from .launcher import AgentLauncher
from .manifest import Custody, canonical, manifest_sha256
from .runner import run_subject
from .scenario import EvidenceFeed, build_env, score_scenario, to_public, validate
from .variants import rng, run_order, variant

OPEN_SET_PATH = Path(__file__).resolve().parent / "open_calibration_v1.json"


def _open_set() -> dict:
    return {t["id"]: canonical(t) for t in json.loads(OPEN_SET_PATH.read_text(encoding="utf-8"))}


def _authorize(tasks: list, custody: Custody | None, launcher=None) -> str | None:
    """Raises PermissionError unless the run is allowed; returns the manifest digest to stamp on records (None for the open set)."""
    if custody is not None:
        if not (isinstance(launcher, AgentLauncher) and launcher.require_limits is True):
            raise PermissionError("run refused: a closed set runs only through an AgentLauncher that requires the resource limits")
        problem = custody.problem(tasks)
        if problem:
            raise PermissionError(f"run refused: {problem}")
        return manifest_sha256(custody.manifest)
    try:
        open_set = _open_set()
        ok = all(isinstance(t, dict) and open_set.get(t.get("id")) == canonical(t) for t in tasks)
    except (OSError, ValueError, TypeError):
        ok = False
    if not ok:
        raise PermissionError("run refused: not the open set, custody (signed manifest + sandbox receipt) required")
    return None


def _run_task(task: dict, argv: list[str], seed: int, epoch: int, prompts, timeout: float, stamp, launcher=None, ask=None) -> dict:
    if validate(task):
        raise ValueError("invalid scenario")
    sc = variant(task, seed, epoch, prompts)
    env = build_env(sc)
    feed = EvidenceFeed(sc, env)
    res = run_subject(argv, env, timeout=timeout, feed=feed, public=to_public(sc), launcher=launcher, ask=ask)
    scored = score_scenario(sc, sc["gold"], env, res["claims"], res["answer"], res["shown"])
    critical = sorted(set(scored["critical"]) | set(res["critical"]))
    return {"task_id": task["id"], "epoch": epoch, "score": 0.0 if res["protocol_violation"] else scored["score"],
            "critical": critical, "protocol_violation": res["protocol_violation"], "steps": res["steps"], "shown": res["shown"],
            "actions": scored["actions"], "answer_correct": scored["answer_correct"], "manifest_sha256": stamp,
            "limits_applied": res["limits_applied"], "valid": res["valid"]}


def run_task(task: dict, argv: list[str], seed: int, epoch: int, prompts: list | None = None, timeout: float = 30.0,
             custody: Custody | None = None, task_set: list | None = None, launcher=None, ask=None) -> dict:
    """task_set: the full list the manifest covers (needed when custody is used)."""
    tasks = task_set if task_set is not None else [task]
    stamp = _authorize(tasks, custody, launcher)
    if not any(isinstance(t, dict) and canonical(t) == canonical(task) for t in tasks):
        raise PermissionError("run refused: the task is not an entry of the authorized task set")   # authorization covers the task itself
    return _run_task(task, argv, seed, epoch, prompts, timeout, stamp, launcher, ask)


def public_record(rec: dict) -> dict:
    return {"task_id": rec["task_id"], "epoch": rec["epoch"], "score": rec["score"], "critical_count": len(rec["critical"]),
            "manifest_sha256": rec.get("manifest_sha256"), "limits_applied": rec.get("limits_applied"), "valid": rec.get("valid")}


def run_epoch(tasks: list[dict], argv: list[str], seed: int, epoch: int, prompts_by_id: dict | None = None,
              timeout: float = 30.0, custody: Custody | None = None, launcher=None, ask=None) -> list[dict]:
    stamp = _authorize(tasks, custody, launcher)
    by_id = {t["id"]: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate task ids")
    return [_run_task(by_id[i], argv, seed, epoch, (prompts_by_id or {}).get(i), timeout, stamp, launcher, ask) for i in run_order(list(by_id), seed, epoch)]


def run_pilot(tasks: list[dict], argv: list[str], seed: int, epochs: int = 3, prompts_by_id: dict | None = None,
              timeout: float = 30.0, custody: Custody | None = None, launcher=None, ask=None) -> dict:
    """All epochs, nothing selected or dropped; repeats are clustered by task in `aggregate`."""
    _authorize(tasks, custody, launcher)
    records = []
    for epoch in range(epochs):
        records += run_epoch(tasks, argv, seed, epoch, prompts_by_id, timeout, custody, launcher, ask)
    return {"records": records, "aggregate": aggregate(records), "report": report(tasks, records, seed)}


CONSTANTS = ("supported", "unsupported", "unknown")


def report(tasks: list[dict], records: list[dict], seed: int = 0, n_boot: int = 2000) -> dict:
    """Answer accuracy against the best constant answer, with a task-clustered bootstrap.

    `beats_best_constant` needs the 2.5th percentile of (accuracy - best constant baseline) over resampled TASKS to be above 0:
    repeats of one task are dependent, so tasks, not runs, are resampled, and baseline and accuracy move together in each resample."""
    gold = {t["id"]: t["gold"].get("answer_status") for t in tasks}
    per_task: dict[str, list[float]] = {}
    for r in records:
        if gold.get(r.get("task_id")) is not None:
            per_task.setdefault(r["task_id"], []).append(1.0 if r.get("answer_correct") is True else 0.0)
    ids = sorted(per_task)
    if not ids:
        return {"answer_scored_tasks": 0, "answer_accuracy": None, "constant_baselines": {c: None for c in CONSTANTS},
                "best_constant_baseline": None, "margin_lower_bound": None, "beats_best_constant": False}
    acc_t = {t: sum(v) / len(v) for t, v in per_task.items()}

    def stat(sample):
        acc = sum(acc_t[t] for t in sample) / len(sample)
        best = max(sum(1 for t in sample if gold[t] == c) / len(sample) for c in CONSTANTS)
        return acc, best

    acc, best = stat(ids)
    base = {c: sum(1 for t in ids if gold[t] == c) / len(ids) for c in CONSTANTS}
    r = rng(seed, "report-bootstrap")
    diffs = sorted((lambda a, b: a - b)(*stat([ids[r.randrange(len(ids))] for _ in ids])) for _ in range(n_boot))
    lower = diffs[int(0.025 * n_boot)]
    return {"answer_scored_tasks": len(ids), "answer_accuracy": acc, "constant_baselines": base, "best_constant_baseline": best,
            "margin_lower_bound": lower, "beats_best_constant": lower > 0}


RUN_RECEIPT_EVENT = "tib_run_receipt"


def run_receipt(custody: Custody, pilot: dict, seed: int, epochs: int) -> dict:
    """The receipt of a run on a closed set: only what may leave the custodian.  Per-run public records (critical COUNT, no flag names),
    the aggregate without its flag list, and the report; bound to the manifest and the task set it was run under."""
    agg = {k: v for k, v in pilot["aggregate"].items() if k != "critical"}
    agg["critical_count"] = len(pilot["aggregate"]["critical"])
    return {"schema": "tib_ak79.run_receipt.v1", "manifest_sha256": manifest_sha256(custody.manifest),
            "task_set_sha256": custody.manifest.get("task_set_sha256"), "seed": seed, "epochs": epochs,
            "sandbox_receipt": ({"seq": custody.receipt["seq"], "hash": custody.receipt["hash"]} if custody.receipt else None),
            "limits_applied_all": bool(pilot["records"]) and all(r.get("limits_applied") is True for r in pilot["records"]),
            "all_runs_valid": bool(pilot["records"]) and all(r.get("valid") is True for r in pilot["records"]),
            "records": [public_record(r) for r in pilot["records"]], "aggregate": agg, "report": pilot["report"]}


def sign_run_receipt(ledger, receipt: dict, task_id: str = "tib_ak79") -> dict:
    return ledger.append(RUN_RECEIPT_EVENT, task_id, "evaluator", receipt)
