# Designing LLM steps for the local model

Read this before designing or changing any prompt, schema, or LLM step in
`ai-service`. When a model is tried in a role, add what was learned to
**Model notes** below, with evidence (a probe score, a live timing, a
commit).

## The floor

Every LLM step must work on **`qwen3.5:9b` (Q4_K_M, ~6.6 GB) on the RTX 3060
12 GB**, sharing the card with `nomic-embed-text` for the worker's
embeddings. A bigger model is a bonus, never a requirement: the harness
carries the burden, not the model.

Don't design a step by writing the prompt that would work on a frontier
model. What Claude does in one call, a 9B model often needs two calls and
code around them for.

## Principles

- **Code does the logic, the model picks or writes.** Filtering, matching,
  date math, counting, choosing ids and checking required fields belong in
  code. A small model should choose from options code has already narrowed,
  never compute or recall them.
- **One concern per call.** Each call answers one question. "Which tool,
  and fill every one of its fields, and write a summary" is three
  questions; split it when it starts failing, and prefer splitting before
  adding prompt rules.
- **Every internal decision is constrained decoding.** Use
  `ollama.complete_structured()` (`format` = JSON Schema, `think: false`,
  `temperature: 0`). At default temperature the same prompt gave different
  routes call to call; at 0 it's reproducible. Never parse free text for a
  decision, and don't use Ollama's native `tools` param (open Qwen3 bug).
- **Put the reasoning field before the answer field.** With thinking off,
  an enum-only schema commits on the first token and ignores the prompt. A
  short `reason` property listed before the answer property lets the model
  reason first (supervisor: 4/11 → 11/11).
- **Enums carry definitions, and come from the source of truth.** A bare
  list of values gets misread (an oil change labelled `inspection`, an
  invented `maintenance`). Give each value a one-line definition, derive the
  values from the schema the API validates against, and assert at import
  that the descriptions cover exactly those values.
- **Narrow the schema per request when the options are per request.** If
  the valid answers are this household's entities, this conversation's
  candidates, or this tool's fields, build them into the schema for that
  call (an `enum` of ids, a tool-specific object) rather than describing
  them in prose and hoping the model copies them back.
- **"No" is a named option, not a null.** A nullable `tool` let questions
  ("should I change the oil soon?") through as proposals. An explicit
  `'none'` value with its own definition ("anything else, including any
  question") stopped both probe cases.
- **Extract the user's words; let code interpret them.** Asked to choose a
  pattern and fill nullable fields, the model answered "first Saturday of
  every month" with a day number. Asked only to quote the phrase (`when:
  "first Saturday"`), it's reliable, and a regex turns the phrase into
  fields. The same goes for years: the model gives month and day, and code
  picks the next occurrence ("December 24" had come back as last year).
- **Context invites computation.** With a calendar in the prompt, the
  monthly step worked out a date instead of quoting the phrase. Send the
  calendar only to steps that answer with a date.
- **Validate before the user sees it.** Anything the model proposes is
  checked in code (known type, real entity, required fields present) before
  it becomes a confirmation card. A proposal that would 422 after the user
  confirms is a bug in this layer.
- **Decisions degrade, never fail the turn.** An LLM decision that errors or
  can't be used falls back to a safe default
  (`complete_structured_or_default`: route `general`, ground anyway, propose
  nothing). AI steps are additive; the core app works without them.
- **Think only where it pays.** Reasoning is for the user-facing answer.
  Routing, gates, tool choice, and replies whose outcome code already
  decided (confirm/cancel acknowledgements: 24.6 s → 0.8 s) run with
  `think: false`.
- **Give each call only what it needs.** Quality drifts well before the
  context fills. Send the facts this step uses: matched entities, not all
  entities; top-k chunks under a distance cutoff, not every hit.
- **The context window is set, and budgeted in code.** Every call sends
  `num_ctx` (`AI_SERVICE_OLLAMA_NUM_CTX`, 16384); Ollama's default is
  smaller and it truncates an overrun silently from the front, which drops
  the system prompt first. `ollama.fit_to_context()` keeps each prompt
  under `num_ctx` minus a reply reserve (4096 for answers, since thinking
  counts; 512 for decisions) by dropping the oldest turns, never the system
  prompt or the last message. Answers are also capped at that reserve
  (`num_predict`), so a long reasoning pass can't run past the window:
  thinking plus answer measured 1.3k–3.8k tokens on everyday questions. Its token estimate is deliberately
  pessimistic (3 chars per token). Raise `num_ctx` only after checking on
  the GPU that the model still loads entirely into VRAM (`/api/ps`:
  `size_vram == size`).
- **What repeats goes first, the question last.** System prompt and fixed
  instructions first, then retrieved context, then the user's message.
- **Every prompt is versioned.** `app/prompt_versions.py` names each prompt
  and schema the model sees and gives it a version;
  `tests/test_prompt_versions.py` pins each version to a hash of its
  content, so changing a prompt without bumping its version fails the
  suite. The content includes the user-message templates around the
  user's words (calendar, entity lists, research scratchpad lines),
  rendered from placeholders, not only system prompts and schemas.
  Probe output, commits, and **Model notes** cite the version.
- **Measure, don't guess.** A prompt or schema change to a decision step
  comes with a `scripts/llm-probe.ps1` run against the real model, reported
  as a score with prompt versions (`action v2: 9/10 → 10/10`) in the commit
  and in **Model notes**. Unit tests prove the plumbing; only the real
  model proves the prompt. Probe cases live in
  `services/ai-service/probes/cases.py`, synthetic only; when a live miss
  turns up, add it as a case before fixing it. Each run is saved under
  `probes/results/` (gitignored); `-Compare last` shows what flipped since
  each suite's newest saved run. Before trusting a
  score that moved by a case or two, rerun with `-Repeat 5`: a case that
  passes only some runs is flaky, and the prompt isn't proven either way.
  Step suites score one call; the `chain` suite runs a message through the
  compiled graph to the card (route, tool, summary), so a change to
  routing or matching shows up where users see it. Date-relative cases set
  their own `today`; add one from a boundary (end of month, a Sunday, Dec
  31) when date math changes. The `answer` suite scores answers with code
  checks (expected mentions, no date absent from the context); give an
  answer-prompt change its score.

## Model notes

One row per model and role. Speeds are on the RTX 3060.

| Model | Role | Strengths | Weaknesses | Evidence |
|---|---|---|---|---|
| `qwen3.5:9b` | Supervisor routing (`reason` + `agent` enum) | 11/11 on the routing probe with `reason` first; reproducible at temperature 0 | Enum-only schema with thinking off sent record/schedule requests to `vehicle`/`maintenance` (4/11) | `b2e187e` |
| `qwen3.5:9b` | Action proposal as one call (`tool`, `args`, `summary`; replaced by `decide_action`'s split calls, not yet probed) | 10/10 on the probe set once log types had definitions | Labelled an oil change `inspection`, invented a `maintenance` type from a bare list; emitted numeric metrics where strings are required | `aadb559`, `b2e187e` |
| `qwen3.5:9b` | Routing, live probe (supervisor v1) | 14/14, including a follow-up routed by history; 0.9 s per case | — | `scripts/llm-probe.ps1`, 2026-11-25 |
| `qwen3.5:9b` | Action proposal, split calls (`decide_action`) | 15/15 with action_target v2, log_fields v1, schedule_kind v1, schedule_detail v3; 2.6 s per case (2 to 3 calls). 18/18 once code parsed the model's "first of every month" and "fifteenth" (its quoted phrases were right; the parser only knew weekdays and digits). 21/21 (3 runs each) with schedule_detail v4 (monthly also quotes when it was last done) and last_done_date v1, the parser refusing phrases it can't hold ("2nd and 4th Tuesday", "fifth Friday") | v1: 10/15. Proposed cards for both questions with a nullable `tool`; gave "December 24" last year's date; wrote a day number for "first Saturday" when given a pattern enum, nullable fields, and a calendar | Same probe runs |
| `qwen3.5:9b` | Fuzzy entity match (`entity_id` enum) | 4/4, 0.5 s per case | — | Same probe runs |
| `qwen3.5:9b` | General grounding gate (`needs_grounding` bool, no `reason`) | 10/10 with grounding_gate v1, 0.5 s per case | — | `scripts/llm-probe.ps1 -Suite gate`, 2026-11-25 |
| `qwen3.5:9b` | Research tool choice (one schema: tool, query, location, days) | 11/12 with research_tool v1, 0.7 s per case; never kept an invented location past `_validated_location` | After a web search, picked `search_web` again instead of stopping (scaling-debt #36) | `scripts/llm-probe.ps1 -Suite research`, 2026-11-25 |
| `qwen3.5:9b` | Action proposal from boundary dates (`decide_action`, same versions) | 28/28 over 3 runs, adding yesterday on Jan 1, "next Saturday" from a Sunday, "tomorrow" on Nov 30, "January 2" on Dec 31, a last-done date in the previous year | — | `scripts/llm-probe.ps1 -Suite action -Repeat 3`, 2026-11-25 |
| `qwen3.5:9b` | Whole action turn (routing, entity match, `decide_action`, card summary) | 11/11 over 3 runs, 2.9 s per case | — | `scripts/llm-probe.ps1 -Suite chain -Repeat 3`, 2026-11-25 |
| `qwen3.5:9b` | Chat answer (personas v1, answer_context v1), code checks | 5/5 over 3 runs, 26.7 s per case: names the record's date or mileage, says it has no records instead of inventing a date | Added an unsupported claim (mileage "at this time" of an old log); not caught by code checks. In a later run, 1 of 5 answers to the oil-change question thought for all 4096 tokens and came back empty (scaling-debt #42) | `scripts/llm-probe.ps1 -Suite answer -Repeat 3`, 2026-11-25 |
| `qwen3.5:9b` | Run-to-run stability of every decision step | 0 of 58 probe cases flaky over 5 runs each at temperature 0; the one miss (research #36) failed all 5 | — | `scripts/llm-probe.ps1 -Repeat 5`, 2026-11-25 |
| `qwen3.5:9b` | Load at `num_ctx` 8192 | 5.8 GB, entirely in VRAM (`size_vram == size`); 8.2 of 12 GB used on the card with the desktop running | — | `/api/ps`, 2026-11-25 |
| `qwen3.5:9b` | Load at `num_ctx` 16384 | 6.1 GB, entirely in VRAM; 7.9 of 12.3 GB used on the card. 12288 was 5.9 GB. Decision speed unchanged on the probe | — | `/api/ps`, `nvidia-smi`, 2026-11-25 |
| `qwen3.5:9b` | Chat answer length with thinking on | 1,256–3,829 generated tokens (reasoning plus answer) on five everyday questions; 3 of 5 went past the old 2048 reserve | — | `eval_count`, 2026-11-25 |
| `qwen3.5:9b` | Confirm/cancel acknowledgement | Accurate done/cancelled/failed replies with `think: false`, 0.8 s | 24.6 s with reasoning on, for no gain | `63033ac` |
| `qwen3.5:9b` / `qwen3:14b` | Any `format` decision at default temperature | — | Different answer to the identical prompt across calls | `docs/scaling-debt-plans/sd15-model-swap-structured-routing.md` |
| `qwen3:14b` | Everything (retired for `qwen3.5:9b`) | — | 9.3 GB, left little VRAM headroom beside embeddings | sd15 |

## Local-model steps today

| Step | Shape | Status against the principles |
|---|---|---|
| Supervisor | `{reason, agent}`, `agent` enum from `VALID_AGENTS`; falls back to `general` | Conforms. |
| General grounding gate | `{needs_grounding: bool}`, biased to `true`; falls back to grounding | Partly: no `reason` field (scaling-debt #40). 10/10 on the probe. |
| Research tool choice | One schema for tool + query/location/days, up to `agent_max_tool_calls` rounds; the answer model reasons over raw forecast days | Partly. Picking a dry stretch from a forecast is logic code could do; no `reason` field (scaling-debt #40). 11/12 on the probe: repeats a web search instead of stopping (scaling-debt #36). |
| Action proposal (`decide_action`) | No matched entity: no call. Then one concern per call: `{reason, tool, entity_id}`, `tool` one of the two tools or `'none'`, `entity_id` an enum of this turn's matches; for a log, the fields with `type` narrowed to the entity's domain and a calendar from code (a week back to two weeks ahead); for a schedule, `{reason, interval_type, title}`, then a detail schema per interval type with plain answers (every 3 months; month and day; the user's phrase for a monthly day, and for when it was last done) that code turns into days, dates and indexes, refusing a monthly phrase it can't map to exactly one day; the calendar goes only to the date-answering types, so a monthly last-done date gets its own call with the calendar (month and day, code picks the past year). Code writes the summary and validates the args with `LogCreate`/`ScheduleCreate` | Conforms. 28/28 on the probe, including cases from month and year boundaries and a Sunday; 11/11 on the chain probe. Calls per turn including routing: 3 for a log, 4 for a schedule, 5 for a monthly schedule with a last-done date. |
| Fuzzy entity match | `{entity_id}`, an enum of the candidate ids; no call when there are no candidates | Conforms. 4/4 on the probe. |
| Chat answer | `chat_stream`, thinking on, default temperature; `think: false` on resume acknowledgements; prompt fitted to `num_ctx` | Conforms. |
