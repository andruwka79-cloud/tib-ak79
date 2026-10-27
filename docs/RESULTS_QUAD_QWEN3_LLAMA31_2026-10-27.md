# TIB-AK79: arms A/B/C/D, Qwen 3 8B and Llama 3.1 8B (preliminary, limited)

Status: DRAFT, not for release before the author's approval. Numbers come from the laptop runs (harness commit b254306f, open set, 27 tasks, 3 deterministic Ollama epochs: effective sample = 27 tasks). Cells marked n/a were not supplied yet; nothing is estimated.

Arms: A no control; B `--gate`; C `--gate-notice` (one notice before the final claims; claims are not rewritten); D = C + one independent reviewer of a different model (Qwen reviews Llama: qwen3; the reviewer for Qwen is not stated here).

## Qwen 3 8B
| arm | mean | critical | task_done | harm | with_tools | accuracy |
|---|---|---|---|---|---|---|
| A | 0.500 | 41 | 21/27 | 3 | 0.154 | 0.581 |
| B | 0.508 | 34 | 24/27 | 0 | 0.154 | 0.591 |
| C | 0.500 | 35 | 24/27 | 0 | 0.154 | 0.581 |
| D | 0.546 | 31 | 25/27 | 0 | 0.295 | 0.645 |

A→D: score +0.046 (95% CI −0.058..0.154); with_tools +0.141 (CI 0.000..0.308); critical per task −0.083 (CI −0.217..0.017). Reviewer: 120 calls, 0 errors.
All intervals include zero or touch it: a tendency, not an established effect.

## Llama 3.1 8B
| arm | mean | critical | task_done | harm | with_tools |
|---|---|---|---|---|---|
| A | 0.267 | n/a | n/a | n/a | n/a |
| B | n/a | n/a | n/a | n/a | n/a |
| C | 0.471 | 19 | n/a | n/a | n/a |
| D | 0.479 | 18 | 18/27 | 0 | 0.731 |

A→D: score +0.212 (95% CI 0.092..0.346); critical per task −0.258; critical on tool tasks 0.769 → 0. D accuracy 0.4086, reported by the laptop as not beating the constant answer (not rechecked here). Reviewer qwen3: 120 calls, 0 errors; 186 model calls, 0 errors.
C→D: 0.471 → 0.479, critical 19 → 18: within noise.

## What this shows and does not show
- Llama's whole gain is produced by the notice (A→C 0.267→0.471, through claims, not through actions); the reviewer adds 0.008 on top of C. Do not read it as "the reviewer improved Llama".
- Qwen: D is better than C (0.546 vs 0.500) but every interval includes zero or touches it.
- The gate does not catch false claims in text or forged citations; the notice changes what the model writes, it is not verification.
- Harm and task_done are computed from the environment state, not from what the model says.
- One seed, one small model per family, public task set (a model may have seen it), no sealed set, no stability across seeds. The Qwen C figure before the notice fix (#593) was 0.483 and is superseded.
- `limited`: the reviewer is a separate identity key (model_config differs between arms C and D).

## Files (sha256)
Qwen A `B775418718775FFCCDC5105B3DC1AC400B9E51EFFD90AB2EB6EFAC9276709912`, Qwen B `6ECC2275…9258`, Qwen C `222743B2F3842A43CD93D331A77874791156ACE088F37EC034A8ECC950E84EC7`, Qwen D `C261C71B…B608` (B and D: full hashes to be added).
Llama A `C0D68FDC1C28553915D1A64C88EAB96A0DFD29996A3C998E620404D4DF0D351F`, Llama C `1738D6067ED23861B8662BC8085ABE5F8E52027F46D44594554874211B4C52B3`, Llama D `2ADB5B7E4058668A6BBA1FCC082EDE1BCA619141FD1E58E35D3981F86A461C6B`.
