"""Run the reference subjects over the open set and write a result file with SHA-256 identities.
python scripts/run_baselines.py OUT.json [seed ...]   (default seeds 7 11 13, 3 epochs; stdlib only, offline)"""
import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tib_ak79 import harness as H  # noqa: E402

MODES = ("unknown", "supported", "unsupported", "liar", "honest_unknown")
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def main():
    out = Path(sys.argv[1])
    seeds = [int(s) for s in sys.argv[2:]] or [7, 11, 13]
    tasks = json.loads(H.OPEN_SET_PATH.read_text(encoding="utf-8"))
    code = "".join(sha(p.read_bytes()) for p in sorted((ROOT / "tib_ak79").glob("*.py")))
    res = {"schema": "tib_ak79.baselines.v1", "python": platform.python_version(), "epochs": 3, "seeds": seeds,
           "task_set_sha256": sha(H.OPEN_SET_PATH.read_bytes()), "code_sha256": sha(code.encode()), "runs": []}
    for mode in MODES:
        for seed in seeds:
            p = H.run_pilot(tasks, [sys.executable, str(ROOT / "tib_ak79" / "baseline_subject.py"), mode], seed, epochs=3)
            a = p["aggregate"]
            res["runs"].append({"mode": mode, "seed": seed, "aggregate": {k: v for k, v in a.items() if k != "critical"},
                                "report": p["report"], "records": [H.public_record(r) for r in p["records"]]})
    out.write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(out, sha(out.read_bytes()))


main()
