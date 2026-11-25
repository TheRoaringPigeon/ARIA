# Research: Jev-class decision models for ARIA's decision steps

Status: done, not adopted. Researched 2026-11-25 on branch
`research/jev-routing-model`.

## Question

Could a fast "System One" decision model take over the enum decisions
that `qwen3.5:9b` makes today, so turns go faster? Those are routing, the
action tool and entity, and the grounding gate.

## Conclusion

**On cloud calls this would be worth doing. On our local setup it isn't.**

- **With cloud calls,** every decision is a network round trip to a
  general model, typically 0.5 to 2 s plus per-token cost. A decision
  model answers in about 0.1 s (hosted Jev's reported median is 0.15 s),
  and there's no VRAM budget to fight over. Moving routing and tool
  choice to one would save real time and money on every turn.
- **On our local setup,** the one model accurate enough (NeoHorse-Jev-4B)
  saves about 0.8 s per decision. It needs 8.8 GiB of VRAM, though, and
  `qwen3.5:9b` needs 5.8 GB on a 12 GB card. qwen has to stay loaded for
  extraction and every answer, so the two can't share the card. Swapping
  models each turn costs more than the decision saves. It would also add
  a second inference runtime beside Ollama (Linux, Triton,
  flash-linear-attention). The answer stream, with thinking on, is still
  most of a turn's wall-clock time anyway.

Revisit this if ARIA moves its decisions to a hosted API, gets a card
with about 16 GB or more, or a quantized NeoHorse turns out to keep its
accuracy while fitting beside qwen.

## What these models are

"Jev" (TypeSafe AI) is a closed, hosted decision model. A request is a
`state` (the text) plus typed questions: `noul` (yes/no), `choice` (one of
N options, each with a one-line description) and `score` (an ordinal
level). One forward pass answers every question with probabilities. It
generates no text. Open-weight reproductions are either a small encoder
with a readout head, or a Qwen3.5 backbone with a pointer head over the
options. None of them runs on Ollama, GGUF or llama.cpp.

The interface fits `docs/local-models.md` well: code supplies the
options, each option has a definition, the choices are set per request,
and there's no free text to parse. It only covers enum decisions.
Extraction (dates, titles, the user's phrase) and the chat answer still
need the LLM.

| ARIA step | Jev primitive | Fit |
|---|---|---|
| Supervisor route | `choice` | Good |
| General grounding gate | `noul` | Good |
| `action_target` tool and entity | `choice` (two questions, one pass) | Good |
| `schedule_kind` interval type | `choice` | Good |
| Fuzzy entity match | `choice`, options per request | Plausible |
| Log fields, schedule detail, chat answer | none | Needs the LLM |

## Method

`spikes/decision_probe.py` ran the existing synthetic cases from
`services/ai-service/probes/cases.py` against each model: 14 routing
cases, and 15 action cases asking for tool and entity. Option definitions
were copied from `_SUPERVISOR_SYSTEM_PROMPT` and
`ACTION_TARGET_SYSTEM_PROMPT`. Each suite ran twice, in prompt order and
with the options reversed, because these models are reported to be
sensitive to option order. GPU runs went through
`spikes/run-decision-probe.ps1`, in a disposable
`pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime` container on the RTX
3060, with aria-llm's models unloaded first. The spike scripts aren't
merged; they stay on the research branch.

## Results

| Model | Routing (prompt order / reversed) | Action target | p50 per decision | Memory |
|---|---|---|---|---|
| `qwen3.5:9b` (today) | 14/14 | 15/15 (whole action path) | ~900 ms | 5.8 GB VRAM |
| laya 0.5.4, 421M encoder, CPU | 5/14 / 6/14 | 9/15 / 8/15 | 245 / 231 ms | CPU only |
| laya + vega-U, Qwen3.5-4B reader | 5/14 / 5/14 | 8/15 / 11/15 | 147 / 145 ms* | 8.1 GiB |
| NeoHorse-Jev-4B 1.0.0, bf16 | 12/14 / 12/14 | 15/15 / 15/15 | 98 / 94 ms | 8.8 GiB |

\* Run without flash-linear-attention, on transformers' slow reference
path. That affects speed, not answers.

**laya, both sizes.** It sent action requests and every research
question to `maintenance` or `general`, with confidence of 0.2 to 0.6. The
4B reader made the same routing mistakes as the 421M encoder, so model
size wasn't the problem. Option order decided past-tense logs: in one
order the 4B read 6 of 7 of them as `create_schedule`, and in the other it
got 6 of 7 right. In one order or the other, both questions came back as
proposals.

**NeoHorse-Jev-4B.** No answer changed when the options were reversed,
and confidence was 0.86 to 1.00 on most correct answers. Action target was
perfect, including both questions as `none`. Both routing misses broke a
priority rule. "I just changed the oil in the Ranger" went to
`maintenance` instead of `action`, at p = 0.39 to 0.43. "When is the
Ranger due for its next oil change?" went to `maintenance` instead of
`vehicle`, at p = 0.53 to 0.64. The tie-break rule was only in the
question text. Putting it into the route definitions, or sending
low-confidence answers back to qwen, would likely fix both. Untested.

## Practical notes for a future attempt

- **NeoHorse setup.** Its runtime is a pure-Python wheel inside the model
  bundle (`dist/neohorse_decision-1.0.0-py3-none-any.whl`). It pins torch
  2.8.0, transformers 5.17.0, flash-linear-attention 0.5.2 and triton
  3.7.1. Triton needs a C compiler in the container. The loader reads
  only local safetensors, with no remote code. Check the bundle against
  its `SHA256SUMS`. The first call takes about 0.8 s while Triton
  autotunes. `causal_conv1d` wasn't installed, so there's a little more
  speed available.
- **Load times.** Cold loads took 6 to 7.5 minutes for the 4B models.
  Most of that was the pip install and first-use downloads into the spike
  container's caches.
- **laya's checkpoints.** Several Hugging Face repos named `laya` are
  unaffiliated copies. The official one is `convaiinnovations/laya`,
  linked from `github.com/NandhaKishorM/laya`. `vegaml-learn` (for
  `laya.Learner`) is PolyForm Noncommercial. `laya` and `vegaml` are
  Apache-2.0.
- **Evidence quality.** Published benchmarks for this class of model are
  self-reported or small samples. Our probe set is small too (29 cases per
  order), so treat 12/14 against 14/14 as directional, not precise.

## Sources

- OpenZeka, "The First Open-Weight Alternatives to Jev":
  whitepapers.openzeka.com/papers/jev-open-weight-alternatives
- Benchmark Heaven, JevBench: benchmarkheaven.com/jev-models
- EveryInfra, "Jev ecosystem report (4)", dev.to
- Tom's Hardware, coverage of Jev-like decision models
- `huggingface.co/TokenRhythm/NeoHorse-Jev-4B` (model card and
  DEPLOYMENT.md)
- `huggingface.co/convaiinnovations/laya`, `github.com/NandhaKishorM/laya`,
  `github.com/NandhaKishorM/vegaml`
