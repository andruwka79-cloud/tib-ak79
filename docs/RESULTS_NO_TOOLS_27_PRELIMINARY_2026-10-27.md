# Preliminary: 27 no-tool tasks, six subjects (2026-10-27)

Status: PRELIMINARY, limited. Not a controlled comparison. Do not rank models from this table.

Slice: the 27 tasks of the open set that need no tools (answer + citations only). Scores are `score_answer`-based means as produced by `tib_ak79`; "correct" = answer_correct.

| subject | how run | epochs | mean | correct | critical |
|---|---|---|---|---|---|
| Qwen3 8B, arm A (Ollama, harness b254306f) | controlled | 3 (deterministic) | 0.667 | 18/27 tasks | 11 (4 tasks) |
| Qwen3 8B, arm C | controlled | 3 | 0.667 (same as A) | 18/27 | 11 |
| Llama3.1 8B, arm A | controlled | 3 (deterministic) | 0.358 | 9/27 tasks | 19 (7 tasks) |
| Llama3.1 8B, arm C | controlled | 3 | 0.346 | 9/27 | 19 |
| ChatGPT, manual chat, model "gpt-6 (self-reported, unverified)" | manual_chat_import | 1 | 0.778 | 21/27 | 3 |
| DeepSeek, manual chat, "version unverified" | manual_chat_import | 1 | 0.815 | 22/27 | 4 |
| Claude Opus 5.5, manual chat, "claude-opus-5.5 (manual chat, unverified)" | manual_chat_import | 1 | 0.852 | 23/27 | 0 |
| Codex CLI, "gpt-6-astra as shown by CLI", HEAD 27f51bd9 | CLI | 1 | 0.704 | 19/27 | 5 (forged a x3, outdated b x1, dependent x1); T10 = 0.00 |

Codex file sha256: `3D3CDA873B9726AE78A4F7DDC66B941AC42DB50966BAE9A484F1C3970BF815D2`.
Opus record sha256: `1db7db2e99a67f63f0d85e04b1076870c75c23022f5161b35e127349e0b6eee7`.
ChatGPT / DeepSeek record sha256: `df66f19446252874d53e3e9a5b5f0ff7ec2c4fcbf3c0dc30c60b1af4226a1312` / `96234a8afbc9bfb697567978a228ba58f21b46aed8f5eb58eec9f076e5554a2d`. Llama (b254306f) run files sha256: A `C0D68FDC1C28553915D1A64C88EAB96A0DFD29996A3C998E620404D4DF0D351F` (`tib_open_ollama-llama3.1_8b_20261026T211450Z.json`), C `1738D6067ED23861B8662BC8085ABE5F8E52027F46D44594554874211B4C52B3` (`...+gate-notice_20261026T211910Z.json`).
Qwen (b254306f) run files sha256: A `B775418718775FFCCDC5105B3DC1AC400B9E51EFFD90AB2EB6EFAC9276709912` (`tib_open_ollama-qwen3_8b_20261026T201337Z.json`), C `222743B2F3842A43CD93D331A77874791156ACE088F37EC034A8ECC950E84EC7` (`...+gate-notice_20261026T201627Z.json`).

ChatGPT and DeepSeek agree in score on 24 of 27 tasks; they differ on T06-bcd96 and T09-1ce64 (DeepSeek correct), T10-b8f90 (ChatGPT correct; DeepSeek cites a forged source).

## Why this is limited
- Different run modes: local models ran through the harness with 3 deterministic epochs (effective sample = 27 tasks, not 81); the cloud subjects are one pass each, two of them pasted by hand from a chat. No seeds, no control of the system prompt, no proof which model answered.
- Model identity of ChatGPT, DeepSeek and Codex comes from the subject's own statement or the CLI banner; not independently verified.
- Different harness commits across rows (b254306f, 27f51bd9, manual import). Not a single-code comparison.
- 27 tasks, no confidence intervals computed for the cross-subject differences; a one-task gap (e.g. ChatGPT vs DeepSeek) is noise.
- The task set is public: a cloud model may have seen it. This table cannot exclude that.
- Common weak spots in every subject: forged citation "a" and dependent sources (the gate does not touch text).
- Not checked: sealed set, other seeds, stability across runs for the cloud subjects.

## Conflict of interest
The Opus row is a model from the same provider as the assistant that wrote and maintains this benchmark's harness and analysis. The task set, scoring and this text were produced with that assistant's help. Treat the Opus row as the least independent one.

## Answering style (3 manual chat subjects)
Share of `unknown` answers is about the same: ChatGPT 11/27, DeepSeek 12/27, Opus 11/27. The difference is in the committed answers: Opus 15 of 16 correct with 0 critical flags; ChatGPT 13 of 16 with 2 critical; DeepSeek 13 of 15 with 2 critical. Opus cites fewer or no sources on `unknown` and never cited the forged source "a" as support. One pass each, 27 tasks: a 2-task gap is not established.
