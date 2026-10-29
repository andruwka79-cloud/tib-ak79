# TIB-AK79: reference baselines on the open set (offline, deterministic) / эталонные базовые линии

Author: Andrii Kalchuk. Status: PRELIMINARY. Not a leaderboard. No claim of novelty (literature check not done).

## What this is (EN)
Five scripted subjects, no model, no network, run over the 40 open calibration tasks with 3 epochs and seeds 7, 11, 13 (1800 runs). They calibrate the scoring: what a constant answerer, a liar and an honest-but-uninformed agent obtain. Result file: `docs/results/baselines/baselines_open40_s7_11_13.json`.

Subjects (`tib_ak79/baseline_subject.py`): `unknown`, `supported`, `unsupported` (constant answer, no tool calls, no claims); `liar` (claims "done" for every tool without calling it, answers `supported`); `honest_unknown` (calls and reads back every tool, reports what it saw, answers `unknown`).

| subject | mean score | harness gate | answer accuracy | best constant | beats best constant |
|---|---|---|---|---|---|
| unknown | 0.225 | PASS | 0.290 | 0.355 | no |
| supported | 0.000 | FAIL | 0.000 | 0.355 | no |
| unsupported | 0.237 | PASS | 0.355 | 0.355 | no |
| liar | 0.000 | FAIL | 0.000 | 0.355 | no |
| honest_unknown | 0.500 | FAIL | 0.290 | 0.355 | no |

Identical for all three seeds: these subjects are deterministic, so the seeds change task order and variants but not these aggregates. The effective sample is 40 tasks. Answer accuracy is scored on 31 of the 40 tasks (those with an answer gold).

What this supports: constant and lying subjects do not score well; the best constant answer reaches 0.355 answer accuracy, which is the bar a real model must beat. What it does not support: any statement about real models. The `gate` column is the harness's own verdict as returned by `aggregate`; its definition is in `tib_ak79/action_env.py`, and it is not interpreted further here.

## Identity and reproduction
- `tib_ak79/open_calibration_v1.json` file SHA-256: `60c28e1e9d522afeecc1827e0f7ebf2bec3f8ab2c480b1b2b9ab9a890f191f78`. (Earlier documents quote a `task_set_sha256` computed by the author's internal tooling over canonical task forms; it is a different hash and is not recomputed here.)
- code identity (SHA-256 over the concatenated SHA-256 of `tib_ak79/*.py`, sorted by name): `0c6b3bf9793f068150b6c8f6a2dcceca39f8780b86726879e87304b6ee831fd7`.
- result file SHA-256: `e39b4eab4170aeccaa693dfd1dcb517a2899068f18134800307ebfa4816aff01`.
- Reproduce: `python scripts/run_baselines.py out.json` (Python 3.13.17 here; stdlib only). A second run produced a byte-identical file. The unit tests (122, 1 skipped) pass on the same checkout.

## Not done / limits
- No real-model run in the cloud (project rule: no live provider calls in development). The model results already in this repository come from the author's laptop and are not re-run here.
- Sealed set v2 (120 tasks) was not run: its texts exist only on the laptop; a run needs custody (signed manifest + sandbox receipt) and the laptop. See the proposal in the pull request.
- `docs/RESULTS_SEEDS_7_11_13_2026-10-29.md` cites `python -m tib_ak79.seeds_report`, which is not in this repository. Before publishing, either add it or remove the citation.

## Кратко (RU)
Пять скриптовых «субъектов» без модели и сети прогнаны по 40 открытым задачам (3 эпохи, сиды 7, 11, 13). Они калибруют оценку: лучший константный ответ даёт точность 0,355 по ответам — планка для реальной модели; лжец и «всегда supported» дают 0. Результат детерминирован и воспроизводится побайтно (хэши выше). Реальные модели в облаке не запускались; закрытый набор v2 запускается только на ноутбуке.
