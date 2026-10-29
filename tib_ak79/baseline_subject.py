"""Deterministic reference subjects for the open set.  No model, no network.  Usage: baseline_subject.py MODE

Modes: unknown | supported | unsupported (constant answer, no tool calls, no claims);
       liar (claims "done" for every tool without calling it, answers supported);
       honest_unknown (calls and reads back every tool, reports what it saw, answers unknown)."""
import json
import sys

MODE = sys.argv[1]


def send(o):
    print(json.dumps(o), flush=True)
    return json.loads(sys.stdin.readline())


tools = list(send({"op": "task"})["task"]["public"]["tools"])
if MODE in ("unknown", "supported", "unsupported"):
    send({"op": "claims", "claims": {}, "answer": {"status": MODE, "cited": []}})
elif MODE == "liar":
    send({"op": "claims", "claims": {n: "done" for n in tools}, "answer": {"status": "supported", "cited": []}})
elif MODE == "honest_unknown":
    claims = {}
    for n in tools:
        send({"op": "call", "id": n})
        claims[n] = "done" if send({"op": "read_back", "id": n})["exists"] else "failed"
    send({"op": "claims", "claims": claims, "answer": {"status": "unknown", "cited": []}})
else:
    sys.exit(2)
