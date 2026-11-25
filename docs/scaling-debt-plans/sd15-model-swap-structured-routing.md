# sd15: model swap, structured-output routing, live turn preview

Closes `scaling-debt.md` item [#15](../scaling-debt.md#15--supervisortool-choice-routing-is-free-text-llm-parsing-brittle-by-construction).
Design doc: `docs/plans/typed-swimming-cookie.md`.

## What shipped

### 1. Model swap: `qwen3:14b` → `qwen3.5:9b`

Hardware check first (`nvidia-smi`, not the buggy ~4GB WMI report):
RTX 3060 **12GB VRAM**, i7-12700K (12C/20T), 32GB RAM. `qwen3.5:9b`
(Ollama library, Apache-2.0, Q4_K_M, 6.6GB) replaces `qwen3:14b` (9.3GB) —
same vendor lineage (no adapter risk), reported to match `qwen2.5:14b`-class
quality at a third less footprint, frees ~2.5-3GB of VRAM headroom on the
card shared with `worker`'s embedding calls (item #14), and — combined with
`think: false` on internal routing calls, below — measurably lower
per-turn latency than the old setup. `OLLAMA_MODEL` default updated in
`.env`/`.env.example`/`.env.prod`/`.env.prod.example`/all three
`docker-compose*.yml` files and both services' `config.py` defaults.
`AI_SERVICE_MODEL_ADAPTER` stays `qwen` — `QwenAdapter` is still the right
adapter for this model family (verified live, see below).

**Required bumping the Ollama server itself**: `qwen3.5` needs Ollama
≥0.17.4 (Gated DeltaNet architecture); the running `aria-llm` container was
on 0.9.2 (never auto-updates on its own despite `docker-compose.llm.yml`'s
unpinned `image: ollama/ollama`). `docker pull ollama/ollama:latest` +
`docker compose -p aria-llm -f docker-compose.llm.yml up -d --force-recreate`
brought it to 0.33.0. This turned out to matter far beyond just unlocking
the new model — see the "found during implementation" section below.

### 2. Fix #15 for real: `format` (structured outputs), not per-model text parsing

The item's root cause: every internal orchestration decision (supervisor
classification, research tool-choice, write-path log/schedule decision,
general-node grounding gate) asked the model to emit prose or loosely-fenced
JSON and then regex/word-boundary-parsed the reply
(`ModelAdapter.parse_choice()`/`parse_tool_decision()`,
`_SUPERVISOR_CHOICE_WORDS`'s "action" collision workaround) — needed only
because Ollama's native `tools` param has a documented, still-open bug
against Qwen3 (ollama/ollama#14601).

Ollama's `format` field is a different, model-agnostic mechanism —
grammar-constrained decoding against a JSON Schema, unaffected by that bug.
New `app/ollama.py::complete_structured(messages, schema) -> dict` sets
`format: schema`, `think: false`, and (see below) `temperature: 0`, then
parses the guaranteed-valid JSON reply directly. All four call sites in
`app/agents/nodes.py` migrated to it, each with its own schema
(`_SUPERVISOR_SCHEMA`, `_GROUNDING_GATE_SCHEMA`, `_RESEARCH_TOOL_SCHEMA`,
`_ACTION_DECISION_SCHEMA`) built from the same source-of-truth constants the
old prose already used (`VALID_AGENTS`, `_RESEARCH_TOOL_HANDLERS`'s keys).
The supervisor's `_SUPERVISOR_CHOICE_WORDS`/`_AGENT_BY_CHOICE_WORD`
indirection is gone entirely — grammar-constrained decoding makes the
"action" collision it existed to dodge structurally impossible, so the
classifier now just answers with the real agent name directly.

`ModelAdapter.parse_choice()`/`parse_tool_decision()` (and `QwenAdapter`'s
`_CODE_FENCE` markdown-fence stripping) are deleted — dead code once every
call site moved to `complete_structured()`. `ModelAdapter` now only owns
`normalize_response()`/`create_stream_filter()`, both still genuinely
model-specific (Qwen's `<think>` tag) for the one thing that's still real
freeform prose: the user-facing streamed answer.

The write-path decision (`propose_action_node`) schema is deliberately
scoped light: it constrains the top-level shape (`tool` enum incl. null,
`args` as a loosely-typed object, `summary`) rather than a fully exhaustive
`oneOf`-per-`interval_type` schema mirroring every field
`_ACTION_DECISION_SYSTEM_PROMPT`'s prose describes — that's a meaningfully
heavier lift than this item's severity warrants, matching this repo's
existing "scope light, document the heavier option" convention (see
scaling-debt.md #7). Semantic validation (`entity_id` really matches a
household entity, all fields for a given `interval_type` are present)
still happens in Python and for real at core-api's `LogCreate`/
`ScheduleCreate`, unchanged.

### 3. Live turn preview: real progress instead of a static "Thinking…"

Root cause of the "long silent wait": `routers/chat.py::chat()` used to run
the **entire** LangGraph graph to completion — supervisor classification,
context gathering, and (Research-routed) a multi-iteration tool-choice
loop, each a blocking Ollama call — before `StreamingResponse` was even
constructed. Even the `agent` SSE frame waited for the whole graph, not
just the supervisor's decision.

`_route_and_gather`/`_resume_action` became `_stream_route_and_gather`/
`_stream_resume_action` — async generators yielding `(event, payload)`
tuples as soon as each is known, ending with a sentinel
(`_ROUTING_RESULT_SENTINEL`) carrying the final routing result. `chat()`
was folded into one `_event_stream()` generator that runs the graph itself,
via `graph.astream(..., stream_mode=["values", "custom"])` (multi-mode
return shape — `(mode, chunk)` tuples — verified directly against the
installed `langgraph` 1.2.9 with a throwaway test graph before writing any
of this, matching this repo's existing "verify against the installed
version" practice):

- **`"values"`** — the full state snapshot after each node. The `agent`
  frame now fires the instant `selected_agent` appears (right after
  `supervisor_node`), not after the whole graph reaches `END`.
- **`"custom"`** — pushed via a new `get_stream_writer()`-based
  `_stream_status()` helper in `app/agents/nodes.py`, called once in
  `general_node` (before its gate call) and once per iteration in
  `research_node`'s tool loop (reusing the existing `ToolResult.
  scratchpad_entry` text, not a second copy) — surfaced as a new `status`
  SSE event.

**One deliberate carve-out, protecting an existing invariant**: an
action-routed turn might still pause at `execute_action_node`'s interrupt,
whose whole contract (asserted by `test_chat_emits_action_proposed_and_
stops_with_no_ollama_call`) is that the response contains *exactly one*
frame (`action_proposed`) — no early `agent`/`status` leak before the
household member has confirmed anything. Both streaming generators track
`suppress_live_frames`, set the moment `selected_agent == "action"` is
seen, which blocks every early `agent`/`status` frame for that turn
(verified this doesn't regress a non-interrupted action turn either — see
Verification below: it still gets its `agent` frame, just at the same
later point it always occupied, right before `citations`).

Frontend: new `status` SSE event (`onStatus` in
`services/frontend/src/api/chat.ts`), and a single live-updating
`statusLine` in `ChatPage.tsx` (the user's explicit choice over an
expandable running log) replacing the old static `'Thinking…'`/`'X is
looking into this…'` placeholder text — updated by both `onAgent`
(`"Routing to ${label}…"`) and `onStatus`, cleared the same places
`thinkingPreview` already was. The now-fully-redundant `pendingAgent`
state (its only read site was this placeholder text) was removed rather
than left dangling.

## Found during implementation, not in the original plan

**Ollama's newer server (0.33.0) splits a thinking model's reasoning into
a separate `message.thinking` field, not an inline `<think>...</think>`
tag in `content`.** Discovered live: streaming a plain question through
`ollama.chat_stream()` against the upgraded server showed every delta as
`{"content": "", "thinking": "<reasoning text>"}` until the real answer
started, at which point `content` carried it and `thinking` went empty —
never once did `<think>` appear literally inside `content`. `_event_stream`
only ever read `chunk["message"]["content"]`, so all of that reasoning text
was being silently discarded — the exact opposite of what item #15's live
preview goal needed. Fixed by reading `message.get("thinking")` directly
and emitting it as a `thinking` SSE frame, *alongside* (not instead of)
still running `content` through `QwenAdapter`'s existing `StreamFilter` —
additive, so a model/server combination that still inlines the tag (the
`StreamFilter`'s original, still-tested contract) keeps working unchanged.

**Internal routing/tool-choice decisions were non-deterministic at
Ollama's default sampling temperature** — confirmed live: the identical
prompt/schema/query against the identical model gave a *different* answer
from one call to the next (checked against both `qwen3.5:9b` and
`qwen3:14b`, so not model-specific). Added `"options": {"temperature": 0}`
to `complete_structured()` — confirmed this makes every decision fully
reproducible across repeated calls. The user-facing answer
(`chat_stream()`) is unaffected, still using Ollama's default temperature,
since sampling variety is exactly what's wanted there.

**Supervisor classification prompt sharpened** for the "action" category —
live testing surfaced an explicit "log that I changed the oil..." request
initially routing to "maintenance" instead of "action". Added concrete
example phrasings ("log that...", "I just...", "remind me to...") to the
category's description. This measurably improved but did not perfectly
solve classification precision for this category (a "remind me to..."
schedule request can still land on "maintenance"). Not chased further:
supervisor routing is explicitly additive/non-load-bearing by design (a
misroute degrades to a different specialist's persona answering
conversationally, never a broken response), and further prompt tuning here
is open-ended, unbounded scope beyond this item's actual bug (the
structural parsing brittleness, now provably gone) — left as a known
characteristic, not a defect, for future prompt iteration if it proves
worth it in practice.

## Verification

All verified live against the real running `aria-prod` stack (not just
unit tests), driving the compiled graph and `/chat` endpoint directly
(auth bypassed via monkeypatching `require_chat_session` inside the
container — no real household credentials touched):

- `docker exec aria-llm ollama --version` → `0.33.0` (was `0.9.2`);
  `ollama list` shows `qwen3.5:9b` (6.6GB) pulled successfully.
- `ollama.complete_structured()` against the live model: every reply valid
  JSON matching its schema, across all four decision shapes; confirmed
  fully deterministic across repeated identical calls once
  `temperature: 0` was added (was previously non-deterministic).
- `QwenAdapter`'s `<think>` handling: confirmed compatible with the new
  model/server via the `message.thinking`-field fix above — a real chat
  turn's reasoning streamed live as `thinking` events, then the real answer
  as `token` events, in the correct order.
- Full `/chat` SSE sequence for a general-routed turn: `agent` → `status`
  ("Checking household records…") → `citations` → `thinking` (many) →
  `token` (many) — the `agent` frame now arrives immediately, not after the
  whole graph.
- Research-routed turn (real graph, real Ollama, real weather provider
  call attempt): `status` frames streamed live from `research_node`'s tool
  loop mid-execution, confirmed via direct `graph.astream(...,
  stream_mode=["values", "custom"])` inspection.
- Action-routed, non-interrupted turn (no household entities available):
  exactly one `agent` frame in the whole response (not zero, not two) —
  confirms the late-emission fallback in `chat()`'s `_event_stream()` fires
  correctly once, without double-firing alongside the early-emission path.
- `uv run tsc -b` (frontend): clean.

Not independently re-verified live in this pass: the true interrupted
(pending-confirmation) path with a real matched household entity — covered
instead by the existing `test_chat_emits_action_proposed_and_stops_with_no_
ollama_call` test (still asserts exactly one frame, unmodified) and by the
suppression logic sharing the identical code path already verified live
for the non-interrupted action case above.

## Follow-up / deliberately not done

- The write-path's fully exhaustive per-`interval_type` JSON schema (see
  "scope light" note above) — pick up if `propose_action_node`'s
  loosely-typed `args` object proves to be a real source of malformed
  writes in practice.
- Further supervisor-prompt tuning for the "action vs. maintenance/vehicle"
  classification edge cases noted above.
- Item #14 (single-GPU contention/no concurrency limit) is untouched —
  the smaller model's VRAM headroom eases it somewhat but doesn't solve it.
