# Entity search: bound inputs, fix the stale docstring, defer real indexing

Covers [`scaling-debt.md`](../scaling-debt.md) item **#7** — entity
type-ahead search (`GET /entities?q=`) runs an unindexed `$regex`
substring scan plus an `$objectToArray` scan of `specs`, and the code's own
docstring claims no indexes exist anywhere in `core-api` — stale since item
#1 landed.

## Context

`_search_filter` (`services/core-api/app/routers/entities.py:119-136`)
backs `GET /entities?q=` and `GET /entities/tags?q=`: a case-insensitive
`$regex` substring match against `name`/`tags`/`location`, plus an
`$objectToArray`/`$filter`/`$regexMatch` scan over the free-form `specs`
dict, so "rang" matches "Ranger" mid-word (`$text` only matches whole
tokens, so it wasn't an option). The docstring says "No indexes exist
anywhere in `core-api` yet" — true when originally written, false since
item #1 added `entities(household_id, archived_at, domain)`.

Two things were verified while scoping this:

1. **Mongo's standard B-tree indexes can't accelerate mid-word substring
   regex** — only prefix-anchored (`^abc`) patterns benefit. So "add an
   index" isn't actually a fix for the regex/`$objectToArray` work itself;
   real acceleration means a precomputed n-gram/trigram field, or a
   dedicated search engine, or Mongo Atlas Search (unavailable — this app
   self-hosts community Mongo, `docker-compose.yml`).
2. **`name`/`tags`/`location`/`specs` had no length constraints at all**
   (`services/core-api/app/schemas/entities.py`,
   `libs/shared/src/aria_shared/models/entities/__init__.py`) — a real,
   independent gap, and one that would also make a future n-gram scheme's
   trigram count unbounded.

Given severity is "real-degradation-at-modest-scale" (not blocks-launch),
and the household-scoping index (#1) already bounds *which* documents reach
the regex (confirmed live, see Verification below) — the actual fix here is
scoped light: bound the input, correct the stale comment, and record the
n-gram/Atlas-Search/dedicated-search-engine options as a deliberately
deferred decision rather than building any of them now.

## Design

### Length caps (`services/core-api/app/schemas/entities.py`)

`EntityCreate`/`EntityUpdate` gain `max_length` constraints via
`Annotated`/`Field` (Pydantic v2 applies `max_length` to any sized
collection, not just strings, so this covers list/dict size too):

- `name`, `location`: 200 chars
- each tag: 100 chars; at most 50 tags
- each `specs` key/value: 200 chars; at most 50 entries

These bound the worst-case per-document cost of `_search_filter`'s regex
and `$objectToArray`/`$filter` work, independent of any indexing strategy.
`EntityBase` (`aria_shared`) is left unconstrained — it's the internal
storage/response model, constructed only from an already-validated
`EntityCreate`/`EntityUpdate` body, so the request-boundary constraint is
sufficient (no scenario reaches `EntityBase` with an oversized field that
didn't already pass through the create/update schema).

### Docstring fix (`_search_filter`)

Replace the stale "no indexes exist" claim with an accurate one: the
`household_id`/`archived_at`/`domain` compound index (item #1) already
scopes which documents reach the regex; the regex/`$objectToArray` work
itself structurally can't be index-accelerated by a standard B-tree; real
acceleration is a deferred decision tracked in the new
`docs/scaling-decisions.md`, not built here.

### New doc: `docs/scaling-decisions.md`

A new living doc, same spirit as `qol-backlog.md`/`scaling-debt.md` but
scoped to decisions deliberately *not* being made yet — things that only
become worth doing at meaningfully more scale, as opposed to
`scaling-debt.md`'s "already-identified, pick up any time" items. First
entry covers this item's deferred options (n-gram/trigram field + backfill,
Atlas Search, a dedicated search engine) and what would trigger revisiting
them.

## Verification

- New `services/core-api/tests/test_entities_validation.py`: 422 on
  oversized `name`/`location`/tag/`specs` key/value, 422 on too many
  tags/specs entries, 201/200 at exactly the max length/count (boundary
  case), for both `POST /entities` and `PATCH /entities/{id}`.
- Full `core-api` suite (238 tests) passes — no regression to existing
  search/creation/update behavior.
- Manual, via the `verify` skill against the real running stack: logged in
  to the seed household, ran `explain("executionStats")` in `mongosh`
  against the exact query shape `GET /entities?q=...` builds (household
  filter + `$and`-wrapped search `$or` + archived/pending-delete filters).
  Confirmed the winning plan is `IXSCAN` on
  `household_id_1_archived_at_1_domain_1` (not `COLLSCAN`), with the
  regex/`$expr` clauses applied as a `FETCH`-stage filter over just the
  index-selected candidates — `totalDocsExamined: 1` against the dev
  household's data, matching `totalKeysExamined: 2`. Confirmed
  `GET /entities?q=` still returns matches correctly after the schema
  change.
