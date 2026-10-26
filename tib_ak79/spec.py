"""TIB-AK79 pilot specification: 160 unique tasks, 12 categories, public distribution and a validator for case descriptors.

Descriptors carry public metadata only (no prompt, no gold): id, category, split (open|sealed), difficulty (1-3),
multistep (bool), kind (positive|negative|ambiguous|adversarial).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

CATEGORIES = {
    "T01": "Factual Accuracy", "T02": "Evidence Grounding", "T03": "Epistemic Humility", "T04": "Contradiction Integrity",
    "T05": "Temporal Accuracy", "T06": "Formal Proof Integrity", "T07": "Action Reality", "T08": "Correction & Retraction",
    "T09": "Provenance Independence", "T10": "Adversarial Evidence", "T11": "Durable State Integrity",
    "T12": "Authority & Budget Integrity",
}
KINDS = ("positive", "negative", "ambiguous", "adversarial")
SPLITS = ("open", "sealed")
EPOCHS = 3  # controlled runs per task: <= 480 executions of 160 unique tasks, all kept, none selected

# per category: (open, sealed, multistep)  -> totals 40 / 120 / 47 over 160
PLAN = {
    "T01": (4, 10, 3), "T02": (4, 10, 3), "T03": (4, 10, 3), "T04": (4, 10, 4),
    "T05": (3, 10, 3), "T06": (3, 10, 3), "T07": (3, 10, 5), "T08": (3, 10, 5),
    "T09": (3, 10, 4), "T10": (3, 10, 5), "T11": (3, 10, 5), "T12": (3, 10, 4),
}
TOTAL, OPEN, SEALED, MIN_MULTISTEP = 160, 40, 120, 40


def planned_distribution() -> dict:
    return {"schema": "tib_ak79.distribution.v1", "total": TOTAL, "open": OPEN, "sealed": SEALED, "epochs": EPOCHS,
            "max_executions": TOTAL * EPOCHS, "min_multistep": MIN_MULTISTEP,
            "categories": {c: {"name": CATEGORIES[c], "open": o, "sealed": s, "multistep": m, "difficulty_levels": [1, 2, 3]}
                           for c, (o, s, m) in PLAN.items()}}


ALLOWED_KEYS = frozenset({"id", "category", "split", "difficulty", "multistep", "kind"})
ID_RE = re.compile(r"^T\d{2}-[A-Za-z0-9_-]{1,32}$")


def validate_descriptors(cases: list) -> list[str]:
    """Problems of a full 160-case descriptor list against PLAN (empty = conforms). Public metadata only: exactly the
    keys in ALLOWED_KEYS are accepted, so no content field (answer, solution, ...) can ride along."""
    if not isinstance(cases, list):
        return ["bad_descriptor_list"]
    problems, seen, valid = [], set(), []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            problems.append(f"bad_descriptor:{i}")
            continue
        valid.append(c)
        cid = c.get("id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or cid in seen:
            problems.append(f"bad_or_duplicate_id:{cid}")
        elif cid[:3] != c.get("category"):
            problems.append(f"id_category_mismatch:{cid}")
        seen.add(cid)
        for key in sorted(set(c) - ALLOWED_KEYS, key=str):
            problems.append(f"descriptor_extra_key:{cid}:{key}")
        if c.get("category") not in CATEGORIES:
            problems.append(f"bad_category:{cid}")
        if c.get("split") not in SPLITS:
            problems.append(f"bad_split:{cid}")
        if c.get("difficulty") not in (1, 2, 3) or isinstance(c.get("difficulty"), bool):
            problems.append(f"bad_difficulty:{cid}")
        if c.get("kind") not in KINDS:
            problems.append(f"bad_kind:{cid}")
        if not isinstance(c.get("multistep"), bool):
            problems.append(f"bad_multistep:{cid}")
    cases = valid
    if len(cases) != TOTAL:
        problems.append(f"total:{len(cases)}!={TOTAL}")
    n_multi = sum(1 for c in cases if c.get("multistep") is True)
    if n_multi < MIN_MULTISTEP:
        problems.append(f"multistep:{n_multi}<{MIN_MULTISTEP}")
    by = Counter((c.get("category"), c.get("split")) for c in cases)
    multi = Counter(c.get("category") for c in cases if c.get("multistep") is True)
    for cat, (o, s, m) in PLAN.items():
        if by[(cat, "open")] != o or by[(cat, "sealed")] != s:
            problems.append(f"split_counts:{cat}:{by[(cat, 'open')]}/{by[(cat, 'sealed')]}!={o}/{s}")
        if multi[cat] < m:
            problems.append(f"multistep:{cat}:{multi[cat]}<{m}")
        kinds = {c.get("kind") for c in cases if c.get("category") == cat}
        if not {"negative", "adversarial"} <= kinds:
            problems.append(f"kinds_missing:{cat}")
        levels = {c.get("difficulty") for c in cases if c.get("category") == cat}
        if levels != {1, 2, 3}:
            problems.append(f"difficulty_levels:{cat}")
    return problems


if __name__ == "__main__":
    json.dump(planned_distribution(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    print()
