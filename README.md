# TIB-AK79 — Truth Integrity Benchmark harness

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23685588.svg)](https://doi.org/10.5281/zenodo.23685588)

AI agents are usually graded by what they report ("done"). TIB-AK79 grades what actually happened: a task is run in a mock environment, the agent acts through a narrow tool protocol, and the score is computed from the **resulting state** plus a check of the agent's claims against it. Baselines that cannot be right for the right reasons (always "unknown", always "supported", a liar) are scored side by side.

**Status: v0.1, early research code.** Unit tests run on fakes on Linux (stdlib only). NOT verified: the Windows subject sandbox (not included in this release) and the Inspect AI integration against a real Inspect install. Preliminary results on small local and chat models (one seed, public task set, limited comparability) are in [`docs/`](docs/): [pair](docs/RESULTS_PAIR_QWEN3_LLAMA31_2026-10-26.md), [arms A/B/C/D](docs/RESULTS_QUAD_QWEN3_LLAMA31_2026-10-27.md), [27 no-tool tasks, six subjects](docs/RESULTS_NO_TOOLS_27_PRELIMINARY_2026-10-27.md). They are not a leaderboard.

## What is inside
- `tib_ak79/action_env.py`, `scenario.py`, `variants.py`, `spec.py` — task model, mock environment, scoring, epochs/variants.
- `runner.py`, `launcher.py`, `sandbox_probe.py`, `winjob.py` — subject-process protocol and launchers (a local child process for development).
- `harness.py` — runs the open set; refuses anything else without a signed custody manifest.
- `manifest.py`, `sealed.py` — commitment scheme and exposure/evaluation budget for a *sealed* hidden task set. They talk to a hash-chained, signed ledger through a small duck-typed interface (`append`, `append_attested`, `verify`, `events`); **no ledger implementation is shipped**, so custody features are not runnable out of the box. The signer-role strings (`evaluator`, `attestor`, `verifier`) are the role names the host ledger must accept.
- `inspect_adapter.py` — optional adapter that exposes tasks as Inspect AI samples (lazy import).
- `open_calibration_v1.json`, `distribution_v1.json`, `open_calibration_names_v1.json` — the 40 open calibration tasks and the category plan. The sealed tasks are **not** part of this repository and never will be.

## Running a real model: limits of `model_ask` / `model_subject`

`model_ask.cli_ask(argv)` pipes a prompt to a command you choose and returns its stdout; `model_subject.py` relays the model's JSON
replies to the harness. This is a convenience wrapper, **not a sandbox**. What it does:

- the command gets an allowlisted environment (`PATH` and a few locale/system variables) plus whatever you pass in `extra_env`;
- `HOME`/`USERPROFILE` point at an empty temporary directory (or a directory you prepare), and the working directory is a fresh empty one;
- a command whose name is a known shell or wrapper (`sh`, `bash`, `cmd`, `powershell`, `env`, ...) is refused;
- output is capped (64 KiB) and the number of model turns is limited.

What it does NOT do: there is no filesystem, network or process isolation. A command such as `python -c ...` or `node -e ...`, or any
program that starts other programs, passes the name check and can read files the current user can read and use the network. The
refusal of shell names is a guard against mistakes, not a security boundary. Run untrusted model CLIs under a dedicated OS account,
container or VM. The model-call path has been tested only with local fake commands; it is not verified against any real model CLI.

## Run the tests
```
python -m unittest discover -s tests -p "test_*.py"
```
Python 3.10+, no third-party packages required.

## Licence
Code: Apache-2.0 (`LICENSE`). Open calibration tasks and documentation: CC-BY-4.0 (`LICENSE-DATA`).

## Security
See `SECURITY.md`. Please report suspected leakage of sealed-set content privately.
