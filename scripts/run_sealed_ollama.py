"""Run a CLOSED (sealed) task set through a local Ollama model under custody and write ONLY the public run receipt.

The private cases file never leaves the custodian's machine and is never copied, printed or committed.  Only `--out` is public:
per-run (task_id, epoch, score, critical_count, manifest_sha256, limits_applied, valid), the aggregate without flag names, the report.

  python scripts/run_sealed_ollama.py --cases PRIVATE_CASES.json --manifest sealed_manifest_v2.json --model qwen3:8b \
      --model-digest SHA256_OF_THE_MODEL --seed 11 --epochs 3 --out receipt_v2_qwen3_s11.json \
      --ledger-factory my_host_module:open_ledger --expected-user LOWPRIV_ACCOUNT

Host-supplied pieces (NOT shipped here): the signed hash-chained ledger (`--ledger-factory pkg.mod:func`, returns an object with
append/append_attested/verify/events/root), the low-privilege agent service (env TIB_AGENT_ADDRESS, TIB_AGENT_AUTHKEY) and the attestor
sandbox receipt for --expected-user.  `--check-only` verifies the commitments and prints counts, runs nothing.

Private cases layout accepted: a JSON object {id: {"task":..., "gold":..., "salt": "<hex>"}} or a list of {"id", "task", "gold", "salt"}.
If your file differs, adapt `load_cases` only."""
import argparse
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tib_ak79 import harness as H  # noqa: E402
from tib_ak79 import sealed  # noqa: E402
from tib_ak79.launcher import AgentLauncher  # noqa: E402
from tib_ak79.manifest import Custody, build_manifest, is_signed, sign_manifest  # noqa: E402
from tib_ak79.model_ask import cli_ask  # noqa: E402

CODE_FILES = sorted(f"tib_ak79/{p.name}" for p in (ROOT / "tib_ak79").glob("*.py"))


def load_cases(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.items() if isinstance(raw, dict) else ((c["id"], c) for c in raw)
    return {cid: (c["task"], c["gold"], bytes.fromhex(c["salt"])) for cid, c in items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--model-digest")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--ledger-factory")
    ap.add_argument("--expected-user")
    ap.add_argument("--sign-manifest", action="store_true", help="append the manifest to the ledger (the custodian's own act)")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    cases = load_cases(a.cases)
    pub = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    problems = sealed.verify(pub, cases)
    print(json.dumps({"cases": len(cases), "commitment_problems": problems, "merkle_root": pub.get("merkle_root")}))
    if problems or a.check_only:
        return 1 if problems else 0
    if not (a.ledger_factory and a.expected_user and a.model_digest):
        sys.exit("need --ledger-factory, --expected-user and --model-digest for a real run")

    tasks = [t if "gold" in t else {**t, "gold": g} for t, g, _ in (cases[c] for c in sorted(cases))]
    mod, fn = a.ledger_factory.split(":")
    ledger = getattr(importlib.import_module(mod), fn)()
    manifest = build_manifest(ROOT, CODE_FILES, model_digest=a.model_digest, tasks=tasks)
    if a.sign_manifest:
        sign_manifest(ledger, manifest)
    if not is_signed(ledger, manifest):
        sys.exit("manifest not signed in the ledger (use --sign-manifest if you are the custodian)")
    custody = Custody(ledger, manifest, a.expected_user)
    launcher = AgentLauncher(os.environ["TIB_AGENT_ADDRESS"], os.environ["TIB_AGENT_AUTHKEY"].encode(), require_limits=True)
    budget = sealed.SealedBudget(ledger, "v2", sorted(cases), code_root=ROOT, code_files=CODE_FILES,
                                 model_digest_of=lambda _n: a.model_digest, allowed_models=[a.model])
    budget.start_round("local_model", a.model)          # raises when the round for this system identity is spent
    ask = cli_ask(["ollama", "run", a.model], timeout=300.0,
                  extra_env={k: os.environ[k] for k in ("OLLAMA_HOST", "OLLAMA_MODELS") if k in os.environ})
    subject = [sys.executable, str(ROOT / "tib_ak79" / "model_subject.py")]
    pilot = H.run_pilot(tasks, subject, a.seed, epochs=a.epochs, custody=custody, launcher=launcher, ask=ask, timeout=600.0)
    receipt = H.run_receipt(custody, pilot, a.seed, a.epochs)
    receipt["model"] = {"name": a.model, "digest": a.model_digest}
    H.sign_run_receipt(ledger, receipt)
    Path(a.out).write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    print("wrote", a.out)
    return 0


sys.exit(main())
