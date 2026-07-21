# M10 — Web-grounded chat (research agent gets internet access)

## Context

Roadmap milestone M10 (`docs/roadmap.md`) picks up an M8 follow-up: the
Research Assistant specialist (M7) can search uploaded documents but has no
access to the live internet, so two real cases fail today — "give me talking
points before I see Jun" can't pull recent news about her employer, and
"what's the weather in Lizella, GA" gets a stale/refused answer from the
model's training data. This plan adds two read-only tools (web search,
weather) to the Research Assistant's existing bounded tool-choice loop,
following the exact adapter-seam pattern this codebase already uses for
`ModelAdapter` (`app/adapters/`).

Decisions locked in with the user before this plan:
- **Search provider: Brave Search API** (free tier, 2000 queries/mo). User
  supplied a key to put in `.env` (gitignored, never committed).
- **Weather provider: Open-Meteo** (free, no key, includes a free geocoding
  endpoint — no separate geocoding key needed).
- **Both behind a swappable adapter/DI seam** — a `SearchProvider` ABC +
  `BraveSearchAdapter`, and a `WeatherProvider` ABC + `OpenMeteoAdapter` —
  explicitly requested so either can be swapped later without touching
  calling code, mirroring `app/adapters/base.py`'s `ModelAdapter` seam.
- **Household location**: add an optional `city` field asked at signup
  (blank allowed). When set, it's the default weather location; an explicit
  place named in the chat query always overrides it.

## Design

### 1. `libs/shared` — `Household.city`
`libs/shared/src/aria_shared/models/household.py`: add `city: str | None =
None` to `Household`. No migration needed — Mongo is schemaless and every
existing read path that builds a `Household`-shaped dict already tolerates
missing optional fields (same pattern M9's `shared_with` post-mortem
established).

### 2. `core-api` — collect + expose city
- `routers/auth.py`: `SignupRequest` gains `city: str | None = None`;
  `signup()` writes it into the inserted household doc.
- `routers/households.py`: new `GET /households/me` returning `{id, name,
  city}` for the caller's own household (`session.household_id`) — this is
  the one place `ai-service` needs to resolve a household's default weather
  location, same shape as `GET /auth/me` already exposes `household_id`
  itself. Reuses `get_current_session` dependency, no new auth surface.
- `services/core-api/tests/test_auth_signup.py`: extend for `city` round-trip
  (present, absent, blank-string-treated-as-not-set is NOT needed — just
  store whatever's given, blank string is a valid value to skip downstream).
- New `services/core-api/tests/test_households.py` case (or extend existing
  household test file if one exists) for `GET /households/me`.

### 3. `frontend` — collect city at signup
- `SignupPage.tsx`: one more optional text input, "City (optional) — used to
  default weather answers in chat".
- `api/auth.ts`'s `signup()` gains a `city?: string` param, threaded into the
  request body as `city`.
- No display/edit surface for city on `ProfilePage`/`HouseholdMembersCard`
  this milestone — out of scope, same as the roadmap bullet only asking for
  collection at creation.

### 4. `ai-service` — provider adapters
New `app/providers/` package (parallel to `app/adapters/`, kept separate
since these aren't `ModelAdapter`s — no Ollama involvement):

- `app/providers/search.py`:
  - `SearchResult` dataclass: `title, url, snippet, published_at: str |
    None`.
  - `SearchProvider` ABC: `async def search(self, query: str, since: date |
    None = None) -> list[SearchResult]`.
  - `BraveSearchAdapter(SearchProvider)`: calls
    `https://api.search.brave.com/res/v1/web/search` with header
    `X-Subscription-Token: settings.brave_search_api_key`. `since` is
    enforced by filtering results whose `age`/`page_age` field (Brave
    returns this) is on/after the cutoff — Brave's API has no native date
    filter param, so this is a post-filter, not a query param. Degrades to
    `[]` on missing key, HTTP error, or malformed response — logged at
    `warning`, matching every other tool's contract in this codebase.
- `app/providers/weather.py`:
  - `WeatherResult` dataclass: `location_label, temperature_c,
    condition, wind_kph`.
  - `WeatherProvider` ABC: `async def get_weather(self, location: str) ->
    WeatherResult | None`.
  - `OpenMeteoAdapter(WeatherProvider)`: geocodes `location` via
    `geocoding-api.open-meteo.com/v1/search` (first result), then calls
    `api.open-meteo.com/v1/forecast` with `current=temperature_2m,weather_code,wind_speed_10m`.
    No API key. Degrades to `None` on geocoding miss, HTTP error, or
    malformed response.
- `app/config.py`: add `search_provider: str = "brave"`,
  `weather_provider: str = "open_meteo"`, `brave_search_api_key: str = ""`,
  `web_search_result_limit: int = 5` — mirrors `model_adapter`'s
  string-selector pattern.
- `app/providers/__init__.py`: `get_search_provider()` /
  `get_weather_provider()` lazy singletons keyed off the two settings
  strings, mirroring `app/adapters/__init__.py`'s `get_adapter()`.

### 5. `ai-service` — wire into the Research Assistant loop
`app/agents/nodes.py::research_node` currently loops up to
`settings.agent_max_tool_calls` times over one tool
(`search_household_documents`). Extend:

- `_RESEARCH_TOOL_SYSTEM_PROMPT`: describe three tools —
  `search_household_documents(query)`, `search_web(query)`,
  `get_weather(location)` (location optional — omitted means "use the
  household's default location"). Decision JSON gains an optional
  `location` field for the weather case, alongside the existing `query`.
- Dispatch on `decision["tool"]` inside the existing loop body (currently
  only one branch) — add `search_web` and `get_weather` branches next to
  the existing `search_household_documents` branch, each appending to
  `tool_calls_made` and to a new `web_results: list[SearchResult |
  WeatherResult]` accumulator (kept separate from `chunks`, since these
  aren't document chunks and citations resolve differently — see §6).
- **Date cutoff for `search_web`**: await `entity_context_task` *before*
  the loop's first `search_web` call rather than only at the end (today it's
  only awaited after the loop finishes) — needed to compute `since` from the
  matched entity's most recent log `occurred_at`. Concretely: on first use of
  `search_web`, if `entity_context_task` isn't done yet, await it, then take
  `max(log["occurred_at"] for entity in entity_context for log in
  entity.logs)` if any matched entity has logs, else `None`. This only adds
  latency to turns that actually call `search_web` (document-only or
  weather-only turns are unaffected) and only awaits it once (cached in a
  local variable after first computation, not recomputed per loop
  iteration).
- **Weather default location**: when `get_weather` is called with no
  `location` in the decision, resolve it from the household's `city` — fetch
  via a new `core_api_client.get_household()` (wraps `GET
  /households/me`), degrading to "no location available" (skip the call,
  don't guess) if the household has no city set and none was named.

### 6. `ai-service` — citations/sources for web + weather results
`schemas/chat.py`'s `Citation` is document-shaped (`document_id, filename,
page_number, section_header, entity_ids`). Extend it in place (per the
roadmap's explicit "extend, don't fork a second UI" instruction) rather than
adding a parallel type:

```python
class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["document", "web"] = "document"
    document_id: str | None = None
    filename: str | None = None
    page_number: int | None = None
    section_header: str | None = None
    entity_ids: list[str] = []
    url: str | None = None       # web only
    title: str | None = None     # web only (search result title, or "Weather for <location>")
```
- `citations.py::resolve_citations` unchanged (still document-only,
  `source_type="document"` default).
- `research_node` builds `Citation(source_type="web", url=..., title=...)`
  entries directly from its `web_results` accumulator (no core-api round
  trip needed — everything required is already in `SearchResult`/
  `WeatherResult`) and appends them to the same `citation_list` returned in
  state — the `citations` SSE frame and `build_system_prompt()` both already
  iterate `citation_list` generically, so no frame-shape change is needed
  elsewhere.
- `build_system_prompt()` / `_render_excerpt`-equivalent: web/weather results
  need to be rendered into the prompt text too (not just returned as
  citations) — add a `_render_web_result`/render pass for `source_type ==
  "web"` citations, appended after the document excerpts section, so the
  model actually sees the search/weather content, not just a citation
  stub. (Today `chunks` carries the excerpt text for documents; web results
  have no `chunks`-equivalent, so their snippet/condition text needs to ride
  in the citation's own fields — add `snippet: str | None = None` to
  `Citation` for this, distinct from `title`.)

### 7. Strict decoupling
Same contract as every other tool: missing `brave_search_api_key`, an HTTP
failure, a malformed response, or Open-Meteo being unreachable all degrade
to "this tool just wasn't used this turn" (empty list / `None`), never a
raised exception — the loop already treats "tool call failed" as "stop using
it, continue with what's gathered so far" via the existing broad
`try/except` around the decision+dispatch step.

### 8. `frontend` — render web citations distinctly
`ChatBubble.tsx`'s citation pill row: branch on `citation.source_type`.
Document citations keep today's `filename, p.N` pill linking to
`downloadUrl()`. Web citations render `title` (or hostname parsed from
`url` if no title) linking to `citation.url` directly, with a distinct
visual treatment (e.g. a small globe/link icon or different pill
background) so a household member can tell "from the web just now" apart
from "from your manual" at a glance — no new component, just a conditional
branch in the existing `.map()`.
- `api/chat.ts`'s `ChatCitation` interface: add `source_type: 'document' |
  'web'`, `url?: string`, `title?: string`, `snippet?: string` to match the
  extended wire shape.

### 9. `docker-compose.yml` / `.env.example`
- `.env.example`: add `BRAVE_SEARCH_API_KEY=` (blank, documented as
  optional — "leave blank to disable web search, ARIA degrades to
  document/household-only grounding").
- `docker-compose.yml`'s `ai-service` environment block: add
  `AI_SERVICE_BRAVE_SEARCH_API_KEY: ${BRAVE_SEARCH_API_KEY:-}`,
  `AI_SERVICE_SEARCH_PROVIDER: brave`, `AI_SERVICE_WEATHER_PROVIDER:
  open_meteo`.
- The user's actual key goes into the real (gitignored) `.env` at the repo
  root — never written into a committed file.

## Files touched (summary)

- `libs/shared/src/aria_shared/models/household.py` — `city` field
- `services/core-api/app/routers/auth.py` — signup accepts `city`
- `services/core-api/app/routers/households.py` — `GET /households/me`
- `services/core-api/tests/test_auth_signup.py`,
  new `services/core-api/tests/test_households.py` (or extended existing)
- `services/ai-service/app/providers/__init__.py`,
  `providers/search.py`, `providers/weather.py` — new
- `services/ai-service/app/config.py` — new settings
- `services/ai-service/app/core_api_client.py` — `get_household()`
- `services/ai-service/app/agents/nodes.py` — `research_node` loop + prompt
- `services/ai-service/app/schemas/chat.py` — extended `Citation`
- `services/ai-service/app/routers/chat.py` — web-result rendering in
  `build_system_prompt()`
- new `services/ai-service/tests/test_providers.py` (or
  `test_search.py`/`test_weather.py` split, matching existing one-module-
  per-concern test layout), extended `test_agents.py`, `test_chat.py`
- `services/frontend/src/pages/SignupPage.tsx`, `api/auth.ts`,
  `api/chat.ts`, `components/ChatBubble.tsx`
- `docker-compose.yml`, `.env.example`
- `docs/roadmap.md` — flip M10 to ✅ with an implementation note once done

## Verification

Per the `verify` skill / this project's established pattern: bring up the
full stack via `docker compose up`, then:
1. Real API check: sign up a fresh household with a city set, confirm `GET
   /households/me` returns it.
2. Ask ARIA about a company mentioned in a log tied to a Person entity
   ("talking points for seeing Jun") — confirm a `research` or relevant
   route fires `search_web`, the system prompt actually contains recent
   results, and the answer references them; confirm citations include
   `source_type: "web"` entries the frontend renders distinctly.
3. Ask "what's the weather" with no location named, household city set —
   confirm it resolves to that city. Ask with an explicit place — confirm it
   overrides the household default.
4. Strict-decoupling check: unset `BRAVE_SEARCH_API_KEY` (or block Brave's
   host) and confirm chat still answers, just without web grounding — same
   for Open-Meteo being unreachable.
5. Run `ai-service`, `core-api`, and `frontend` test suites
   (`uv run pytest`, `npm run lint && npm run build`) — all green.
6. Update `docs/roadmap.md`'s M10 section with a "Done as of" note per this
   doc's own convention (see M7/M8/M9 entries) once verified.

## Post-implementation notes

Landed almost exactly as planned above — §1–§9 all built as scoped, no
design deviations. Two real bugs surfaced only by live-testing against the
real running stack (docker-compose, real `qwen3:14b`, live Brave/Open-Meteo
APIs — not caught by the 178-test unit suite, all passing the whole time):

- **The supervisor never routed to Research Assistant for either of this
  milestone's own example queries.** `_SUPERVISOR_SYSTEM_PROMPT`'s
  `research` category was still worded exactly as M7 left it — "about the
  content of an uploaded document, manual, receipt, or invoice" — with no
  mention of the web/weather capability this milestone just added, so a
  live "talking points about Jun" / "weather in Lizella, GA" request both
  classified as `general`, which has none of the new tools. Fixed by
  extending the category's wording to also cover "needs current/live
  information from the internet... or asks about the weather"; confirmed
  live afterward that both example queries route to `research`.
- **Open-Meteo's geocoder silently rejects "City, ST" / "City, State".**
  Confirmed live: a request for `"Lizella, GA"` — this milestone's own exit
  criterion's exact example query — returned zero geocoding results, while
  the bare `"Lizella"` matched correctly. `OpenMeteoAdapter.get_weather()`
  now retries with just the portion before the first comma when the full
  string misses, before giving up — covered by
  `test_open_meteo_retries_with_bare_city_when_city_state_form_misses`.

Full live verification (real stack, no mocks): fresh household signup with
`city: "Lizella, GA"` round-tripped through `GET /households/me`; a real
Person entity ("Jun", company "Anthropic") with a dated log; a live chat
request for talking points about Jun routed to Research Assistant, called
`search_web`, and returned real Brave results as `source_type: "web"`
citations (the model's own query reformulation chose a less-precise search
term than hoped — a model-quality nuance, not a plumbing bug, same
"the model decides" tool-choice contract M7/M8 already accepted); a live
weather request for "Lizella, GA" routed to Research Assistant, called
`get_weather`, and returned a real current answer (29.3°C, partly cloudy,
wind 19.8 kph) grounded via an `open-meteo.com` citation; confirmed
`BraveSearchAdapter.search()` degrades to `[]` with no exception when the
API key is unset. 178 ai-service tests pass (15 new in
`test_providers.py`, 8 new in `test_agents.py`, 2 new in `test_chat.py`),
109 core-api tests pass (4 new), `frontend` `lint`/`build` both clean. Not
verified: an actual browser click-through of the new signup city field and
web-citation pill styling — no browser tooling available in this session,
the same gap every AI-milestone plan since M3 has noted.
