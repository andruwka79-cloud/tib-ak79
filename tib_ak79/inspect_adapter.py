"""Inspect AI (0.3.285) adapter: TIB scenarios as Inspect Samples, TIB scoring as an Inspect scorer.

The TIB logic stays in scenario.py / action_env.py; Inspect only schedules epochs and writes logs.  Rules that keep the sealed set closed:
  * Sample.target is ALWAYS empty and metadata holds only public fields: Inspect writes both into eval logs.
  * the log gets only a critical COUNT (no flag names/ids/thresholds); details go to the custodian's `record` sink.
  * gold/harness come from `lookup(task_id)` (custodian side) at scoring time, never from the Sample.
  * Repeats of one task are dependent: stderr is clustered by metadata "task_id" (inspect_ai.scorer.stderr(cluster=...)).
The pure functions below need no Inspect.  `build_task` imports inspect_ai lazily; it is NOT verified against a real Inspect here
(the package is not installed in the dev environment) - the check is a run against a real Inspect install, not yet done."""
from __future__ import annotations

from .scenario import score_scenario, to_public, validate

SCORE_KEYS = ("score", "critical")


def sample_fields(sc: dict) -> dict:
    """What may reach Inspect: id, public prompt view, metadata for clustering.  No target, no harness, no gold."""
    if validate(sc):
        raise ValueError("invalid scenario")
    pub = to_public(sc)
    return {"id": sc["id"], "input": sc["prompt"], "target": "",
            "metadata": {"task_id": sc["id"], "category": sc["category"], "public": pub}}


def score_value(result: dict) -> dict:
    """Float-valued dict for Inspect: the mean score plus a 0/1 critical flag (criticals are reported, never averaged into score)."""
    return {"score": float(result["score"]), "critical": 1.0 if result["critical"] else 0.0}


def score_one(sc: dict, gold: dict, env, claims: dict, answer: object, shown: object = None) -> tuple[dict, dict]:
    """-> (what may go into the Inspect log, custodian-only details).  Flags like cited_forged_evidence:e2 or dependent_sources:1<2
    would reveal evidence status, thresholds and expected outcomes of a sealed task, so the log carries only their count."""
    res = score_scenario(sc, gold, env, claims, answer, shown)
    log = {"value": score_value(res), "explanation": f"critical_count={len(res['critical'])}",
           "metadata": {"critical_count": len(res["critical"])}}
    return log, {"critical": res["critical"], "actions": res["actions"], "claims_differing_from_gold": res["claims_differing_from_gold"]}


def build_task(scenarios: list[dict], solver, lookup, record=None, epochs: int = 3):
    """solver: an Inspect solver that runs the subject via runner.run_subject on the custodian side.
    lookup(sample_id, epoch) -> (full scenario, gold, env, claims, answer, shown) from the custodian's run store, where `shown`
    is the list of evidence ids the harness delivered (run_subject(...)["shown"]); nothing private travels through Inspect state
    or logs.  record(sample_id, epoch, details) is the custodian's sink for the details that stay out of the log."""
    try:
        from inspect_ai import Epochs, Task
        from inspect_ai.dataset import MemoryDataset, Sample
        from inspect_ai.scorer import Score, mean, scorer, stderr
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise ImportError("inspect_ai is required for build_task (install it only if you want the Inspect integration)") from e

    samples = [Sample(**sample_fields(sc)) for sc in scenarios]

    @scorer(metrics={"score": [mean(), stderr(cluster="task_id")], "critical": [mean()]})
    def tib_scorer():
        async def score(state, target):
            log, details = score_one(*lookup(state.sample_id, state.epoch))
            if record is not None:
                record(state.sample_id, state.epoch, details)
            return Score(**log)
        return score

    return Task(dataset=MemoryDataset(samples), solver=solver, scorer=tib_scorer(), epochs=Epochs(epochs, "mean"))
