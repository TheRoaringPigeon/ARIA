# ARIA

## Git commits

Never run `git commit` unless the user explicitly asks for that specific
commit, in that turn. An earlier "commit this" in the conversation only
authorizes the commit it was said for — it does NOT carry forward to later
work in the same session, even later work on the same feature/backlog item.
Finishing a task (tests passing, build clean, verified live) is not itself
permission to commit. When work is ready, stop and say so; let the user
review the diff and tell you when to commit.

## Scripts

The user is on Windows — user-run scripts in `scripts/` are PowerShell
(`.ps1`), not bash. Don't add `.sh` scripts there.

## Local model

Every LLM step runs on `qwen3.5:9b` on a 12 GB RTX 3060, and must work
there. Before designing or changing a prompt, schema, or LLM step in
`ai-service`, read `docs/local-models.md`; its principles are rules, not
background. The short version:

- Code does the logic; the model picks from options code narrowed, or
  writes prose. One concern per call.
- Internal decisions use `ollama.complete_structured()` (JSON Schema,
  `think: false`, `temperature: 0`), with a `reason` field before the answer
  field, enum values derived from the API's own types and given one-line
  definitions.
- When the valid answers are per request (this household's entities),
  build them into that call's schema as an `enum`; never have the model
  copy an id from prose.
- Validate every proposal in code (against the API's own Pydantic models)
  before the user sees it; decisions fall back to a safe default instead of
  failing the turn.
- Every prompt and schema sent to the model is versioned in
  `app/prompt_versions.py`; `tests/test_prompt_versions.py` fails until a
  changed prompt's version is bumped and re-pinned.
- A prompt/schema change ships with a `scripts/llm-probe.ps1` score against
  the real model (`9/10 → 10/10`, with prompt versions) in the commit, and
  a row in that doc's **Model notes**. Unit tests prove plumbing; only the
  probe proves a prompt. Add a probe case when a live miss is found.

## Commands

```
docker compose -p aria-llm -f docker-compose.llm.yml up -d --build   # shared Ollama (aria-llm), start first
docker compose up -d --build                                          # dev stack
docker compose --profile mcp up -d mcp-server                         # MCP server on :8003
uv run --package <pkg> pytest            # from that package's dir: aria-shared, aria-auth, core-api, ai-service, worker
uvx ruff@0.16.3 check .                  # lint (what pre-commit runs)
cd services/frontend; npm run lint; npm run build   # oxlint, then tsc -b + vite build
scripts/llm-probe.ps1 [-Suite routing|action|fuzzy]  # score LLM decision steps against the live model
```

AVN (`../AVN`) runs its own Ollama on host port 11434, the same port
`aria-llm` publishes, and both use the one GPU. Only one can be up at a
time; check `docker ps` before starting either, and ask before stopping
AVN's.

## Guides

These docs are rules, not background. Read the relevant one before writing
or reviewing code in its area:

- `docs/local-models.md`: anything the model runs.
- `docs/data-model.md`: collections, fields, and why they're shaped that way.
- `docs/architecture.md`: how the services fit together, and which one owns what.
- `docs/scaling-debt.md`: known gaps, ranked.

When a decision changes one of these rules, update the guide in the same
change. `docs/plans/` and `docs/scaling-debt-plans/` are history (what was
planned and shipped), not rules; don't treat them as current.

## Debt

Don't add to `docs/scaling-debt.md` silently, and don't leave TODOs in code:
record new debt as an item there. When touching code an item covers, fix it
if it's in reach and update or close the item in the same change.

## Session handoff: `docs/next-step.md`

Carries the immediate next task across context clears.

- **At the start of a session, read it.** If it has content, that's the
  task to pick up. If it's empty, nothing is pending; take the next step
  from `docs/roadmap.md` or `docs/qol-backlog.md`.
- **Before the user clears context**, rewrite it: where things stand, exact
  next steps, open decisions, and anything not in git (gitignored files,
  running containers, local env).
- **When the handoff's work is done**, empty it (leave the file present but
  blank), so an empty file reliably means "nothing pending".

## Household data

The database holds a real household's records, documents, and people
(names, contacts, birthdays). Read it when debugging needs it; never copy
it into code, tests, probe cases, docs, or commit messages. Tests and
probes use synthetic fixtures only.

## Mongo write patterns

When a code path does N sequential Mongo round trips over a collection of
items (a `for` loop calling `find_one`/`update_one`/`delete_one` per item),
distinguish two different problems before reaching for a fix:

1. **Round-trip cost that scales with N is (almost) always a pure batching
   fix, no infra change needed.** Collapse the loop to a fixed number of
   round trips using `$in`-scoped `update_many`/`delete_many` over the whole
   set at once. Two techniques that come up constantly:
   - **Preserve conditional/race-safety filters by re-applying them inside
     the batch write itself**, not by trusting an earlier snapshot read —
     e.g. `delete_many({"_id": {"$in": ids}, "still_valid_condition": ...})`
     evaluates the condition per-document at write time, same as a per-item
     conditional `delete_one` would, just batched.
   - **When a batch write only returns a count, not which ids matched**
     (`delete_many`/`update_many`), recover "what actually happened" by
     diffing an `_id`-only snapshot taken before against one taken after,
     rather than looping to check each item individually.
2. **Cross-collection atomicity is a different, heavier problem — don't
   reach for it by default.** If the actual requirement is "these N writes
   across different collections must all succeed or all roll back together,"
   that's not a batching problem, it's a transactions problem, and MongoDB
   transactions require the deployment to be a replica set (even a
   single-node one) — a bare standalone `mongod` cannot do them at all, full
   stop. Reaching for real atomicity pulls in real infra surface
   (replica-set conversion, connection URI changes, a prod migration/restart
   step) — treat it with the same weight as any other item with real design
   surface (`EnterPlanMode`-level, not a quick fix). Don't default to it just
   because "transaction" sounds like the correct-by-construction answer —
   check whether the requirement is really atomicity or just round-trip cost
   first.
3. **Test-infra reality check before committing to transactions**: this
   repo's Mongo test doubles (`mongomock_motor` in `core-api`, hand-rolled
   fakes in `worker`) don't support sessions/transactions at all —
   `AsyncMongoMockClient().start_session()` raises `NotImplementedError`.
   Per-call `session=` kwargs are silently accepted everywhere else, so
   route/task business logic still unit-tests fine through a
   dependency-injected no-op transaction override; true rollback-on-failure
   atomicity has to be verified live against the real compose stack, not in
   the unit suite — plan for that up front rather than discovering it
   mid-implementation.

## MCP: aria-household-ops

`services/ai-service/app/mcp_server.py` runs a standalone FastMCP server
(docker-compose service `mcp-server`, profile `mcp`, port 8003) exposing two
write tools: `create_log` and `create_schedule`. It's registered with Claude
Code as `aria-household-ops` (`http://localhost:8003/mcp`, local/project MCP
config — `claude mcp list` to check).

Both tools take an explicit `session_cookie: str` argument — they don't do
their own auth. To call them:

1. Run `scripts/aria-mcp-login.ps1` — logs in as the `Claude` service account
   (a `member`-role user in the household, created via the invite flow) using
   credentials in `.env.aria-mcp` (gitignored, not in git history), and
   prints a fresh `aria_session` cookie value on stdout.
2. Pass that value as the `session_cookie` argument to `create_log` or
   `create_schedule`.

The service account is scoped to whatever household it was invited into —
`member` role can create/update/archive/restore logs, schedules, and
entities there, but not hard-delete (owner-only). Session cookies from step 1
are valid 7 days, but just re-run the script each time rather than caching —
it's cheap and avoids expiry bugs.

If `create_log`/`create_schedule` aren't showing up as callable tools, the
MCP connection loads at session start — start a new session.
