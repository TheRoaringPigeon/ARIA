# Fuzzy Entity Matching (fast-follow to M7's entity grounding): Implementation Plan

## Context

`entity_grounding.py`'s `find_matching_entities()` (built for M7's "household
data grounding" fast-follow) matches a chat query against household
entities via word-boundary substring matching: it checks whether an
entity's *name or tag* appears verbatim, as a whole word, inside the
query. This works when the query names the entity exactly ("talking points
with Dad", entity tagged "Dad") but fails whenever the query only names
*part* of the entity's stored name — caught live, dogfooding the real
Woodward household onto M9: asking "what's the next thing due for my
Ranger" found nothing, because the vehicle is named "2021 Ford Ranger" and
the match only ever looks for the *entity's* full string inside the query,
never the reverse. Asking with the exact stored name ("my 2021 Ford
Ranger") grounds correctly today — confirmed live, this is a matching gap,
not a broken grounding pipeline.

This plan adds a second-pass fallback: when the deterministic
word-boundary match finds nothing, ask the model which household entity
(if any) the query most likely refers to, given the full entity list. Only
fires on a genuine miss — a query that already gets an exact hit never
pays the extra latency or risks second-guessing a confident match.

**Scope, narrowed up front:**
1. **Read path (chat grounding) only — not the write path
   (`propose_action_node`'s entity whitelist).** Guessing wrong in a
   read-only answer is low-stakes and correctable with a follow-up
   question; guessing wrong in a write action creates a real, wrong
   log/schedule against the wrong entity. `propose_action_node` calls
   `find_matching_entities()` directly (uncapped) to build its whitelist —
   that call site is untouched, so "log an oil change on my Ranger"
   (partial name) still requires the full stored name for now, same as
   today. Revisit only if a concrete need shows up; not solved here,
   called out explicitly rather than silently left inconsistent.
2. **Single best guess, not a ranked list.** The model returns one
   `entity_id` or `null` — matches the literal ask ("pick something most
   likely," singular) and keeps the prompt/parsing as simple as the
   existing action-decision/research-tool-decision prompts already are.
   A message genuinely ambiguous between two similarly-named entities is
   an accepted limitation, not solved here (same spirit as M7's "no
   cross-specialist handoff" scope note).
3. **No enable/disable setting.** Consistent with the rest of this
   grounding pipeline (retrieval.py, entity_grounding.py's existing
   fetch/match path) having no on/off switches, only degrade-on-failure —
   a bad model reply or an unreachable Ollama just means no fuzzy match,
   not a broken request.

---

## Key design decisions

**Lives in `entity_grounding.py`, which gains its first `ollama`/adapter
dependency.** Every other LLM-decision prompt in this codebase
(supervisor routing, the research tool-decision, the action-decision)
lives in `agents/nodes.py` — but "resolve which household entities a query
refers to" is exactly `entity_grounding.py`'s existing job description, and
adding a second, LLM-backed resolution pass to it is a natural extension of
that responsibility, not a layering violation. Keeping it here also means
every current and future caller of `gather_entity_context()` — today
`research_node`'s direct call, `gather_baseline_context`'s call, and
therefore every specialist that goes through it — gets the improvement for
free, with no changes needed at any call site.

**Fires only inside `gather_entity_context()`'s own `matched is None`
branch — never when a caller hands it a precomputed `matched` list.**
`propose_action_node` already passes its own uncapped, separately-computed
`matched` into `gather_baseline_context` (forwarded to
`gather_entity_context`) specifically to avoid a redundant word-boundary
scan (see that function's own docstring) — since that list is always
non-`None` on the write path, the fuzzy branch is skipped automatically.
No new parameter or flag needed to keep the write path conservative; it
falls straight out of the calling convention already in place.

**Reuses `ollama.complete()` + `get_adapter().parse_tool_decision()` —
no `ModelAdapter` interface change.** `parse_tool_decision()` already does
nothing model-specific beyond stripping a `<think>` block and an optional
markdown fence before `json.loads`, returning whatever dict was parsed (or
a fallback dict on failure) — already proven shape-agnostic by the M8
action-decision prompt reusing it for a `{"tool", "args", "summary"}` shape
instead of the research tool's original `{"tool", "query"}`. This is the
same reuse again, this time for a `{"entity_id": ...}` shape.

**Prompt explicitly asks for a confident guess, not just "return null on
any doubt."** If the instruction were "only answer if certain," a model
would default to `null` for nearly every partial-name case — the exact
failure mode this plan exists to fix. Framed instead as: pick the entity
the message most plausibly refers to, and only answer `null` when the
message clearly isn't about anything in the list at all.

**Renders the full active entity list (id, name, domain, tags), capped at
a new `settings.entity_fuzzy_match_candidate_limit` (default 50) — not
just the tag/name strings `find_matching_entities` already tried.** Since
the deterministic pass found nothing, there's no natural matched subset to
narrow to; a household-tracking app's entity count is small enough that
rendering the (capped) full list is cheap. Domain is included so the model
can reason about a category reference too (e.g. "the truck"), not just a
substring of the name; capped, not unbounded, so a very large household
doesn't blow up the prompt.

**Degrades to `[]` on every failure axis** — Ollama unreachable, a
malformed/unparseable reply, or the model naming an `entity_id` that isn't
actually in the candidate list (a hallucination guard, not just trusting
the model's own id) — same contract every other grounding path in this
codebase already follows (`retrieval.py`, `citations.py`,
`entity_grounding.py`'s own existing 401/network-failure branches).

---

## File-by-file plan

### `services/ai-service/app/config.py`
- Add `entity_fuzzy_match_candidate_limit: int = 50`.

### `services/ai-service/app/entity_grounding.py`
- New imports: `from app import ollama`, `from app.adapters import get_adapter`.
- New `_FUZZY_ENTITY_MATCH_SYSTEM_PROMPT`: instructs the model to pick the
  one household entity (by id) the message most plausibly refers to, given
  a rendered list, or `null` if the message clearly isn't about any of
  them — respond with strict JSON `{"entity_id": "<id>"|null}`.
- New `_render_entities_for_fuzzy_match(entities: list[dict]) -> str`:
  `- {id}: {name} ({domain}) [tags: {tags}]` per line, `"(no household
  entities)"` if empty — same minimal style `nodes.py::_render_matched_entities`
  already uses, extended with tags; lives here rather than importing that
  private helper across modules.
- New `async def resolve_fuzzy_entity_match(query: str, entities: list[dict]) -> list[dict]`:
  builds the prompt from up to `settings.entity_fuzzy_match_candidate_limit`
  entities, calls `ollama.complete()`, parses via
  `get_adapter().parse_tool_decision()`, looks up the returned `entity_id`
  against `entities` (ignoring/degrading if it's `null`, missing, or names
  an id not actually in the list), wrapped in a single broad
  `try/except Exception` logging a warning and returning `[]` — never
  raises, matching every other function in this module.
- `gather_entity_context()`: inside the existing `if matched is None:`
  block, after `matched = find_matching_entities(query, entities)`, add:
  if `matched` is still empty and `entities` is non-empty, set
  `matched = await resolve_fuzzy_entity_match(query, entities)`. Nothing
  else in the function changes — the existing `if not matched: return []`
  and context-building loop below it already handle whatever `matched`
  ends up being.

### `services/ai-service/tests/test_entity_grounding.py`
- `test_gather_entity_context_returns_empty_when_nothing_matches` — extend
  to also monkeypatch `ollama.complete` (returning a `{"entity_id": null}`
  reply) so this test still exercises the real code path end-to-end
  instead of breaking on an unmocked network call.
- New `test_resolve_fuzzy_entity_match_*` cases: returns the matching
  entity dict for a confident `entity_id`; returns `[]` for a `null`
  decision; returns `[]` and doesn't raise on an Ollama failure; returns
  `[]` and doesn't raise on a malformed/non-JSON reply; returns `[]` when
  the model names an `entity_id` not present in the candidate list
  (hallucination guard); respects `entity_fuzzy_match_candidate_limit` by
  only rendering that many entities into the prompt (assert on the
  captured prompt content via a fake `ollama.complete`).
- New `test_gather_entity_context_falls_back_to_fuzzy_match_*` cases: a
  partial-name query ("my Ranger" style, entity named "2021 Ford Ranger")
  that the deterministic pass misses still returns that entity's context,
  via a monkeypatched `ollama.complete`/`resolve_fuzzy_entity_match`; a
  call that passes `matched=` explicitly (the write-path shape) never
  invokes fuzzy resolution at all — assert via a `resolve_fuzzy_entity_match`
  monkeypatch that raises if called, confirming the write path's
  conservatism holds; an empty `entities` list never invokes it either
  (nothing to render/pick from).

---

## Sequencing

1. `config.py`'s new setting.
2. `entity_grounding.py`: rendering helper, `resolve_fuzzy_entity_match`,
   the `gather_entity_context` wiring — small enough to land as one step.
3. Tests: the one existing-test update plus all new cases above.
4. Manual verification against the real running stack (below).

---

## Verification

**Manual walkthrough**, against the real Woodward household (the exact
case that surfaced this):
- Ask "what's the next thing due for my Ranger" — confirm it now grounds
  on the "2021 Ford Ranger" entity's real oil-change schedule, without
  needing the full stored name (the original failing case).
- Ask something genuinely unrelated to any household entity ("what's the
  weather like") — confirm it still degrades to no grounding rather than
  guessing an unrelated entity.
- Confirm the write path's scope boundary holds as designed: ask ARIA to
  "log an oil change on my Ranger" (partial name, a create-log request) —
  confirm this still resolves to "couldn't determine a specific entity"
  today (unchanged, deliberate), while "log an oil change on my 2021 Ford
  Ranger" (full name) still succeeds exactly as it does today.

**Automated:** `uv run pytest` in `ai-service`.

### Critical files
- `services/ai-service/app/config.py`
- `services/ai-service/app/entity_grounding.py`
- `services/ai-service/tests/test_entity_grounding.py`
