# SD-15: Model swap + structured orchestration + live turn preview

## Context

`docs/scaling-debt.md` item #15 ("Supervisor/tool-choice routing is
free-text LLM parsing, brittle by construction") flags that every internal
orchestration decision in `ai-service` — the supervisor's specialist
classification, the Research Assistant's tool choice, the write-path's
log/schedule decision, the general-node grounding gate — works by asking
the model to emit prose or loosely-fenced JSON and then regex/word-boundary
parsing the reply (`app/adapters/qwen.py::parse_choice`/
`parse_tool_decision`, `app/agents/nodes.py::_SUPERVISOR_CHOICE_WORDS`).
This exists only because Ollama's native `tools` parameter has a
documented, still-open bug against the Qwen3 family
(ollama/ollama#14601 — tool defs serialize as malformed Go-struct text, and
prior tool calls get stripped from resent history). The item's own note
says this is "worth keeping in mind if the AWS move also means
reconsidering the LLM backend."

Separately, the user has observed that the chat UI shows a static
"Thinking…" (or "X is looking into this…") placeholder for a long time
before any real content streams in. Root cause, confirmed by reading
`routers/chat.py`: `_route_and_gather()` runs the **entire** LangGraph
graph to completion — supervisor classification, entity/document
gathering, and (for Research-routed turns) a multi-iteration tool-choice
loop, each a separate blocking Ollama call — and only *then* does
`chat()` construct the `StreamingResponse` / SSE generator. Nothing is
streamed to the browser until orchestration is 100% finished; even the
`agent` frame (which flips the placeholder from generic "Thinking…" to
"Research Assistant is looking into this…") waits for the whole graph,
not just the supervisor's decision.

This plan bundles three changes the user asked for, in the order they
asked for them:

1. **Swap the local model** for something that fits this machine better
   and is a straight upgrade, chosen from current hardware constraints.
2. **Fix item #15 for real** — not with model-specific prose parsing, but
   with Ollama's `format` (JSON-schema-constrained decoding) feature, which
   sidesteps the broken `tools` param entirely and works the same
   regardless of which model is loaded.
3. **Stream real orchestration progress into the chat window** instead of
   a static placeholder, by moving graph execution inside the SSE
   generator and surfacing a live-updating status line.

Machine: RTX 3060 **12GB VRAM** (confirmed via `nvidia-smi`, not the
buggy 4GB WMI report), i7-12700K (12C/20T), 32GB system RAM. Currently
running Ollama 0.9.2 (confirmed via `docker exec aria-llm ollama
--version`) with `qwen3:14b` (9.3GB on disk) — old enough that it predates
several of the model options below.

---

## 1. Model swap: `qwen3:14b` → `qwen3.5:9b`

**Recommendation: `qwen3.5:9b`** (Ollama library, Apache-2.0, Q4_K_M,
6.6GB). Reasoning:

- Same vendor lineage as today (Qwen), so `QwenAdapter`'s `<think>...</think>`
  stripping and stream-classification logic keeps working unchanged — this
  is the lowest-risk axis of the swap. (Verify empirically once pulled —
  see Verification — but Qwen's hybrid-thinking tag format has been stable
  across the 3.x line.)
- Reported to match `qwen2.5:14b`-class benchmark quality at roughly a
  third less disk/VRAM footprint (6.6GB vs. qwen3:14b's 9.3GB) — a real
  generational efficiency gain, not just a smaller/weaker model.
- Frees ~2.5-3GB of VRAM headroom on the shared 12GB card, which directly
  eases (without fully solving — that's item #14, out of scope here) the
  GPU-contention problem between `ai-service` chat calls and `worker`'s
  concurrent embedding calls during OCR.
- Smaller model + fewer/no "thinking" tokens spent on internal routing
  calls (see part 2) should also mean *lower* per-turn latency than today,
  not just comparable — helping the exact "long silent wait" complaint
  from a different angle than part 3's streaming fix.
- Apache-2.0, free, runs entirely local — no API cost, no data leaving the
  machine, consistent with this app's fully self-hosted design.

**Requires Ollama ≥ 0.17.4** (Qwen3.5's Gated DeltaNet architecture). The
running `aria-llm` container is on 0.9.2 — `docker-compose.llm.yml`'s
`image: ollama/ollama` is unpinned (`:latest`), but Docker never
auto-updates a running container, so a `docker compose pull` + recreate is
needed before pulling the new model (see Verification).

**Changes** (mechanical — `docker/ollama/entry.sh` already auto-pulls
whatever `OLLAMA_MODEL` names on container start):
- `.env`, `.env.example`, `.env.prod`, `.env.prod.example`:
  `OLLAMA_MODEL=qwen3.5:9b`
- `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.llm.yml`:
  fallback default `${OLLAMA_MODEL:-qwen3.5:9b}`
- `services/ai-service/app/config.py` / `services/worker/app/config.py`:
  `ollama_model: str = "qwen3.5:9b"` (worker's copy of this setting is
  currently dead — grepped, nothing reads `settings.ollama_model` in
  `services/worker/app/` outside `config.py` itself — keep it consistent
  anyway rather than let the two configs' defaults silently diverge)
- `AI_SERVICE_MODEL_ADAPTER` stays `qwen` — no adapter registry change.
- `QwenAdapter`'s docstring mention of "Qwen3" gets a one-line update to
  note it also covers 3.5's same think-tag behavior, once verified.

---

## 2. Fix #15: `format` (structured outputs), not model-specific parsing

Ollama's `format` field on `/api/chat` is a *different* mechanism from the
broken `tools` param — it grammar-constrains raw decoding to match a given
JSON Schema, model-agnostic, unaffected by ollama/ollama#14601. Combined
with the `think` request field (also independent of `format`), every
internal orchestration decision can ask for exactly the shape it needs and
get back **guaranteed**-valid JSON — no code-fence stripping, no
word-boundary collision risk, no "silently becomes `{"tool": None}`" on a
merely-oddly-formatted reply.

### `app/ollama.py`: new `complete_structured()`

Add alongside the existing `complete()`:

```python
async def complete_structured(messages: list[dict], schema: dict) -> dict:
    """Like complete(), but constrains decoding to `schema` (Ollama's
    grammar-based `format` field) and disables thinking — internal
    routing/tool-choice decisions don't need visible chain-of-thought, and
    skipping it cuts real per-turn latency. Returns the already-parsed
    dict; still wrapped in try/except by every caller since a schema
    violation, timeout, or unreachable Ollama is still possible.
    """
    client = get_client()
    resp = await client.post(
        "/api/chat",
        json={
            "model": settings.ollama_model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "think": False,
        },
    )
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])
```

This is genuinely model-agnostic — no adapter involved. It supersedes
`ModelAdapter.parse_choice()` / `parse_tool_decision()` for every call site
that switches to it (below), since the model literally cannot produce a
reply outside the schema. **Delete both methods from `ModelAdapter`
(`app/adapters/base.py`) and `QwenAdapter`** once every call site is
migrated, plus their tests in `test_adapters.py` — this repo's own
convention is "don't keep unused abstractions," and the adapter's reason
for existing (per-model prose-quirk workarounds) no longer applies to
these two methods. `normalize_response()`/`create_stream_filter()` stay —
still needed for the user-facing streamed answer, which is genuinely
freeform prose, not schema-constrained.

### Call-site migrations, `app/agents/nodes.py`

- **`supervisor_node`**: replace the "respond with one word" prompt +
  `_SUPERVISOR_CHOICE_WORDS`/`_AGENT_BY_CHOICE_WORD`/the "action" collision
  workaround entirely with:
  `{"type": "object", "properties": {"agent": {"type": "string", "enum": list(VALID_AGENTS)}}, "required": ["agent"]}`
  (`VALID_AGENTS` imported from `agents/state.py`, already the single
  source of truth). Grammar-constrained decoding means the "action" vs.
  ordinary-prose collision this workaround existed for is structurally
  impossible now — delete `_SUPERVISOR_CHOICE_WORDS`, `_AGENT_BY_CHOICE_WORD`,
  and their import-time assert along with it. Response is
  `{"agent": "..."}`; any exception (bad JSON, missing key, value not
  actually in `VALID_AGENTS` — defensive, shouldn't happen given the
  schema but keep the same degrade-don't-fail contract) still falls back
  to `"general"`.
- **`general_node`**'s grounding gate: schema
  `{"type": "object", "properties": {"needs_grounding": {"type": "boolean"}}, "required": ["needs_grounding"]}`
  replaces the yes/no word-parsing. Same yes-biased-on-failure contract.
- **`research_node`**'s tool-choice loop: schema covering the flat
  (non-nested) tool shape —
  `{"type": "object", "properties": {"tool": {"type": ["string","null"], "enum": [...tool names..., null]}, "query": {"type": "string"}, "location": {"type": "string"}, "days": {"type": "integer"}}, "required": ["tool"]}`.
  Deletes the "respond with ONLY a JSON object, no other text" prompt
  language and `_CODE_FENCE` handling (moves with `parse_tool_decision`'s
  deletion — `_CODE_FENCE` was only ever needed because free-text replies
  sometimes wrapped JSON in a markdown fence; grammar-constrained output
  never does).
- **`propose_action_node`**'s write-path decision: same idea, but
  `create_log`/`create_schedule` have genuinely different `args` shapes
  per `interval_type` (see `_ACTION_DECISION_SYSTEM_PROMPT`'s existing
  prose). A fully exhaustive `oneOf`-per-interval_type schema is a
  meaningfully heavier lift than this item's severity warrants — **scope
  light**, matching this repo's existing convention (see scaling-debt.md
  #7's `scaling-decisions.md` deferral): constrain the top-level shape
  (`tool` enum incl. null, `args` as a loosely-typed object, `summary`
  required alongside a non-null `tool`) so malformed *top-level* replies
  (wrong tool name, missing summary, non-JSON) become structurally
  impossible, while the per-`interval_type` field prose stays exactly as
  it is today, still validated (for real) by core-api's own
  `ScheduleCreate`/`LogCreate` at write time — same as now. Note the
  fully-exhaustive schema as a documented future enhancement in the sd15
  writeup rather than building it here.

### Net effect on `app/adapters/`

`ModelAdapter` shrinks to two methods (`normalize_response`,
`create_stream_filter`) — both still genuinely model-specific (Qwen's
`<think>` tag). Update `base.py`'s docstring: it no longer needs to
mention "avoiding native structured tool-calling because of a documented
bug" as the reason this seam exists, since the seam's remaining two
methods were never about that bug in the first place.

---

## 3. Live orchestration preview (replace the static "Thinking…")

### Backend: run the graph *inside* the SSE generator

Today, `chat()` calls `await _route_and_gather(...)` to completion, *then*
builds `StreamingResponse(_event_stream())`. Restructure so
`_event_stream()` itself drives the graph via
`graph.astream(..., stream_mode=["values", "custom"])` (verify this exact
multi-mode return shape — `(mode, chunk)` tuples — against the installed
`langgraph` version first, same practice `graph.py`'s own comments already
follow for `AsyncRedisSaver`) and yields SSE frames as soon as each piece
of information is available, instead of only after the whole graph
reaches `END`:

- The moment a `"values"` update includes `selected_agent`, yield the
  `agent` frame immediately — today this waits for the entire graph
  including `research_node`'s tool loop to finish first, which is most of
  the real dead time for Research-routed turns.
- Use LangGraph's `get_stream_writer()` inside `research_node`'s loop
  (and one call each in `general_node`'s gate and
  `propose_action_node`, before their single decision call) to push a
  short human-readable line at each real step — reusing the tool
  handlers' existing `ToolResult.scratchpad_entry` text (e.g. "Already
  searched the web for 'X' and found 3 result(s)") rather than
  maintaining a second copy of the same phrasing. These surface as
  `"custom"`-mode chunks in the same `astream()` iteration.
- New SSE event: `status` — `{"content": "<line>"}`. Purely additive/
  informational, same contract as every other node output in this
  codebase: if nothing is emitted, the frontend's existing fallback text
  still applies.
- The existing `_route_and_gather`/`_resume_action` degrade-to-blanket-M4/M5
  try/except logic moves inside the generator too — headers are committed
  once the `StreamingResponse` starts, so a mid-graph failure after some
  `status`/`agent` frames were already sent must still degrade to the
  blanket-gather fallback and continue into a normal (ungrounded-or-
  degraded) answer, never abort the stream. This is the same principle
  the existing Ollama-call `except httpx.HTTPError`/`except
  (json.JSONDecodeError, ...)` blocks already apply one level down — just
  extended to cover the routing phase now that it lives in the same
  generator.
- `_InterruptedResult`'s single-frame short-circuit stays basically as-is
  (no loop to narrate there).

### Frontend: one live-updating status line

Per the user's choice, this replaces the static "Thinking…"/"X is looking
into this…" text with a single line that updates in place as each event
arrives — no new "expandable log" component.

- `services/frontend/src/api/chat.ts`: add `onStatus?: (line: string) =>
  void` to `StreamChatHandlers`; add `case 'status':` to `dispatchFrame`.
- `services/frontend/src/pages/ChatPage.tsx`: add a `statusLine` piece of
  state (parallel to today's `thinkingPreview`/`pendingAgent`), updated by
  `onStatus` and by `onAgent` (e.g. "Routing to Research Assistant…"
  instead of today's "Research Assistant is looking into this…" — both
  fine, pick whichever reads more like an in-progress action). Placeholder
  bubble's text becomes, in priority order: `thinkingPreview` (once the
  final answer's `<think>` block starts arriving) → `statusLine` (once any
  status/agent frame has arrived) → the existing static `'Thinking…'`
  fallback (first instant of the request, before anything at all has come
  back). Cleared on new send / resume, same lifecycle the existing
  `thinkingPreview`/`pendingAgent` resets already follow.

---

## Critical files

- `services/ai-service/app/ollama.py` — new `complete_structured()`
- `services/ai-service/app/adapters/base.py`, `qwen.py` — remove
  `parse_choice`/`parse_tool_decision`
- `services/ai-service/app/agents/nodes.py` — all four decision call sites
  + delete `_SUPERVISOR_CHOICE_WORDS`/`_AGENT_BY_CHOICE_WORD`/`_CODE_FENCE`-
  adjacent prompt language + `get_stream_writer()` calls
- `services/ai-service/app/routers/chat.py` — move graph execution inside
  `_event_stream()`, new `status` SSE event, degrade-on-failure moves
  in-generator
- `services/frontend/src/api/chat.ts`, `src/pages/ChatPage.tsx` — `onStatus`
  handler + status line
- `services/ai-service/tests/test_adapters.py`, `test_ollama.py`,
  `test_nodes.py` (if present), `test_chat.py` — remove obsolete
  parse_choice/parse_tool_decision tests, add `complete_structured` tests,
  update chat SSE tests for early `agent` frame + new `status` events
- `.env`, `.env.example`, `.env.prod`, `.env.prod.example`,
  `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.llm.yml`,
  `services/ai-service/app/config.py`, `services/worker/app/config.py` —
  model swap
- `docs/scaling-debt.md` (flip #15 to 🟢 with a summary note, matching
  every other item's convention) and a new
  `docs/scaling-debt-plans/sd15-model-swap-structured-routing.md`
  (written after implementation + live verification, same
  retrospective-with-verification-evidence format every existing
  `sdN-*.md` in that directory already uses)

## Verification

1. `docker compose -f docker-compose.llm.yml pull ollama` then recreate
   the container; confirm `docker exec aria-llm ollama --version` ≥
   0.17.4.
2. `docker exec aria-llm ollama pull qwen3.5:9b`; confirm `ollama list`
   shows it.
3. Update env vars, rebuild/restart `ai-service` (and `worker`, for
   config consistency) against whichever compose stack is running
   (currently `aria-prod-*`, per `docker ps`).
4. Before touching parsing logic: send a real chat message through the
   browser against `qwen3.5:9b` on the *unmodified* free-text parsing path
   first, to isolate whether the model swap alone changes behavior (does
   `<think>` still render/strip correctly? does classification still
   route sensibly?) before also changing how routing is parsed.
5. After the `format`/`complete_structured()` migration: exercise each of
   the four decision points live — a maintenance question, a vehicle
   question, a research question that needs `search_web`, and a "log
   that I changed the oil" write-path turn — confirming each still routes/
   decides correctly and that `ollama.complete_structured()`'s replies are
   always valid JSON matching the schema (no more `{"tool": None}`
   silent-failure fallback firing on a well-formed request).
6. After the streaming refactor: watch the Network tab / browser UI on a
   Research-routed question (the case with the most orchestration steps)
   and confirm the placeholder text now updates progressively (agent
   name, then tool-choice status lines, then thinking preview, then the
   real answer) instead of sitting on "Thinking…" for the whole orchestration
   phase.
7. Run the full `ai-service` test suite (`uv run pytest`) — all existing
   + new tests green.
8. Update `docs/scaling-debt.md` #15 and write the `sd15-*.md` plan doc
   with the actual verification evidence gathered above, matching the
   existing docs' convention.
