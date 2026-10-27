# TIB-AK79: arms A/B/C/D, Qwen 3 8B and Llama 3.1 8B (preliminary, limited)

Status: DRAFT, not for release before the author's approval. Numbers come from the laptop runs on harness commit b254306f.

Sample: the open set has 40 tasks; 3 deterministic Ollama epochs give 120 runs per arm, so the effective sample is 40 tasks, not 120. 13 of the 40 tasks have tools. `task_done X/27` counts runs whose goal is an action (9 tasks with gold claims x 3 epochs = 27 runs); it is scored from the final environment state only, and the other runs have no action goal. `harm` is out of all 120 runs. (The separate "27 no-tool tasks" slice in the other document is a different 27: tasks without tools.)

Arms A and B for both models repeat the pair document ([RESULTS_PAIR_QWEN3_LLAMA31_2026-10-26.md](RESULTS_PAIR_QWEN3_LLAMA31_2026-10-26.md)) on a different commit with different file hashes: it is a deterministic rerun on b254306f and it gave the same values (Qwen A 0.500 / B 0.508, Llama A 0.267 / B 0.258).

Arms: A no control; B `--gate`; C `--gate-notice` (one notice before the final claims; claims are not rewritten); D = C + one independent reviewer of a different model (reviewer for Qwen D: llama3.1:8b; reviewer for Llama D: qwen3:8b).

## Qwen 3 8B
| arm | mean | critical | task_done | harm | with_tools | accuracy |
|---|---|---|---|---|---|---|
| A | 0.500 | 41 | 21/27 | 3 | 0.154 | 0.581 |
| B | 0.508 | 34 | 24/27 | 0 | 0.154 | 0.591 |
| C | 0.500 | 35 | 24/27 | 0 | 0.154 | 0.581 |
| D | 0.546 | 31 | 25/27 | 0 | 0.295 | 0.645 |

A→D: score +0.046 (95% CI −0.058..0.154); with_tools +0.141 (CI 0.000..0.308); critical per task −0.083 (CI −0.217..0.017). Reviewer llama3.1:8b: 120 calls, 0 errors.
All intervals include zero or touch it: a tendency, not an established effect.

## Llama 3.1 8B
| arm | mean | critical | task_done | harm (of 120) | with_tools | accuracy |
|---|---|---|---|---|---|---|
| A | 0.267 | 49 | 18/27 | 0 | 0.077 | 0.376 |
| B | 0.258 | 49 | 18/27 | 0 | 0.077 | 0.366 |
| C | 0.471 | 19 | 18/27 | 0 | 0.731 | 0.398 |
| D | 0.479 | 18 | 18/27 | 0 | 0.731 | 0.4086 |

A→B: score −0.008 (95% CI −0.025..0.000), critical per task 0.408 → 0.408.
A→C: score +0.204 (CI 0.087..0.338), critical per task −0.250 (CI −0.400..−0.125); on tool tasks score +0.654 (CI 0.423..0.846), critical 0.769 → 0 (CI −1.000..−0.538); comparable.
A→D: score +0.212 (CI 0.092..0.346), critical per task −0.258.
C vs D: `analyze` does not compare these two arms (marked incomparable), so there is no interval; the difference 0.471 → 0.479, critical 19 → 18, is reported as a raw figure only and is not shown to be real.
D accuracy 0.4086: the laptop reports it does not beat the constant answer (not rechecked here). Reviewer qwen3:8b: 120 calls, 0 errors; 186 model calls, 0 errors.
task_done is 18/27 in every arm: the gain does not come from actions; harm is 0 in every arm for Llama.

## What this shows and does not show
- Llama's gain is already visible on arm C, before any reviewer (A→C 0.267→0.471, task_done unchanged at 18/27); the reviewer adds a raw 0.008 on top of C, not tested. One seed, so causation is not established: it is consistent with the notice driving the gain, but not proven. Do not read it as "the reviewer improved Llama".
- Qwen: D is better than C (0.546 vs 0.500) but every interval includes zero or touches it.
- The gate does not catch false claims in text or forged citations; the notice changes what the model writes, it is not verification.
- Harm and task_done are computed from the environment state, not from what the model says.
- One seed, one small model per family, public task set (a model may have seen it), no sealed set, no stability across seeds. The Qwen C figure before the notice fix (#593) was 0.483 and is superseded.
- `limited`: the reviewer is a separate identity key (model_config differs between arms C and D).

## Files (sha256)
Qwen A `B775418718775FFCCDC5105B3DC1AC400B9E51EFFD90AB2EB6EFAC9276709912`, Qwen B `6ECC2275136ED7E258B5DDEC784DCB08048BA02C0B4E6C03174AE9AB54449258`, Qwen C `222743B2F3842A43CD93D331A77874791156ACE088F37EC034A8ECC950E84EC7`, Qwen D `C261C71BA116201E211A4D3227AFA9507FED1D666243D85B62E68633F7FDB608`.
Llama A `C0D68FDC1C28553915D1A64C88EAB96A0DFD29996A3C998E620404D4DF0D351F`, Llama B `80E5617F595EA6D6CD07970F24F6820C86713048D438819F63250F586BB85D4A`, Llama C `1738D6067ED23861B8662BC8085ABE5F8E52027F46D44594554874211B4C52B3`, Llama D `2ADB5B7E4058668A6BBA1FCC082EDE1BCA619141FD1E58E35D3981F86A461C6B`.
