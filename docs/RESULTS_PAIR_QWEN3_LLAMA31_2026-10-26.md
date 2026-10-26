# First A/B pair on the open calibration set: Qwen 3 8B and Llama 3.1 8B (2026-10-26)

Small-scale engineering observation, not a proven effect: one seed, three epochs, two 8B local models, the open calibration set only (40 tasks).
The sealed set (120 tasks) is not written and was not used.

## Setup
- Harness code: AK79-OS commit `6ac22d9f9c7ba614d6513fd9ab863938ab72ff78`, identical for both arms and both models. Open set 40 tasks, 3 epochs, seed 7.
- Arm A: neutral interface. Arm B: a pre-action gate (A.K/79 HandsGate, fake backend, throw-away test keys). The gate limits actions; it never rewrites the model's answer or claims.
- Models via a local Ollama: `qwen3:8b` (500a1f067a9f), `llama3.1:8b` (46e0c10c039e). Identity is the declared `ollama list` string, not independently verified.
- Intervals: 95% bootstrap, 2000 resamples, clustered by task (resampling per-task B-A differences), seed 0. Both pairs were `comparable` and every run exited 0.

## Qwen 3 8B
| | A | B | B-A |
|---|---|---|---|
| mean score | 0.500 | 0.508 | +0.008 (95% CI 0 .. 0.025) |
| critical violations | 41 | 34 | -7 |
| answer accuracy | 0.581 | 0.591 | |

Critical violations by flag, A -> B: `repeated_effect:charge` 3 -> 0, `spend_over_cap` 3 -> 0, `cited_forged_evidence` 8 -> 7, `false_success:refresh` 12 -> 12.
The gate removed the two classes of action violation it can act on (repeated charge, spend over cap): -6 of the -7. These are small counts (3 and 3 over 40 tasks x 3 epochs), so read them as an observation, not a measured rate.
In the 13 tasks that give the model tools (the only place the gate can act) the mean score did not change.

## Llama 3.1 8B
| | A | B |
|---|---|---|
| mean score | 0.267 | 0.258 (-0.008, CI -0.025 .. 0) |
| critical violations | 49 | 49, flags identical |

No effect of the gate on this model.

## What this does not show
- Both models stay far from good: best constant answer reaches 0.355 answer accuracy; for Qwen the lower bound of "beats the constant answer" is negative, so it is not established that either model beats it.
- The gate does not catch false success claims (`false_success:refresh`) or forged citations: those are in the model's text, and the gate does not touch text.
- Not checked: stability across seeds, other models, the sealed set, independent model identity. Do not read this as "the gate reduces model errors"; it removed two classes of action violation on one small model.

## Files
SHA-256 of the run files: Qwen A `157FB2D6302C7E046E51B96059A680BF9514AF05782A63B2FB2AFF08FC3B888F`, Qwen B `82CACD0C0E7568110831981FA2672653359C79749517F9E4CC2BEFDEFBD8991F`; Llama A `E9D133B5D834D8A021D4BC8781B0B44A1B2C7A9681B285ED3C95F973879EBCC4`, Llama B `E116F13392B4419B8D5D80FE39551172554B5A7DAC02697EFB58861C7076F8F3`.
Reproduce: run `python -m tib_ak79.run_open` per arm (`--gate` for B) with the same model, seed and epochs, then `python -m tib_ak79.analyze A.json B.json --out DIR`.
