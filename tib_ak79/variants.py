"""Deterministic reformulation and run-order generator (fixed seeds, no clock, no global RNG).

Epoch 0 is the canonical form of every task.  Later epochs reorder what the author declared order-free (evidence list, tool list)
and may pick one of the author's `prompts` paraphrases; ids, harness and gold never change, so the verdict of a task is the same
object in every epoch and repeats stay comparable (and dependent: aggregate clusters them by task)."""
from __future__ import annotations

import copy
import hashlib
import random

from .scenario import validate


def rng(seed: int, *parts: object) -> random.Random:
    """Independent stream per (seed, parts): sha256 of a canonical string, so no stream depends on call order."""
    digest = hashlib.sha256(("|".join([str(int(seed))] + [str(p) for p in parts])).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def run_order(task_ids: list, seed: int, epoch: int) -> list:
    """Order of tasks for one epoch.  Epoch 0 keeps the given (sorted) order; later epochs are seeded permutations."""
    ids = sorted(task_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids")
    if epoch > 0:
        rng(seed, "order", epoch).shuffle(ids)
    return ids


def variant(sc: dict, seed: int, epoch: int, prompts: list | None = None) -> dict:
    """Epoch-`epoch` form of a validated scenario.  `prompts`: author-written equivalent phrasings (epoch 0 is sc['prompt'])."""
    if validate(sc):
        raise ValueError("invalid scenario")
    out = copy.deepcopy(sc)
    if epoch <= 0:
        return out
    r = rng(seed, "variant", sc["id"], epoch)
    pool = [sc["prompt"]] + [p for p in (prompts or []) if isinstance(p, str) and p and p != sc["prompt"]]
    out["prompt"] = pool[r.randrange(len(pool))]
    ev = out["public"].get("evidence", [])
    r.shuffle(ev)                               # revisions keep their after_step: only the initial list is order-free
    tools = list(out["public"].get("tools", {}).items())
    r.shuffle(tools)
    out["public"]["tools"] = dict(tools)
    htools = out["harness"].get("tools", {})
    out["harness"]["tools"] = {k: htools[k] for k in out["public"]["tools"]}
    if validate(out):
        raise ValueError("variant became invalid")
    return out
