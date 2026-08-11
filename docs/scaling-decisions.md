# Scaling Decisions

Deliberately-deferred decisions — things that are fine to leave as-is at
today's scale (one deployed household, single-developer operation) but where
the "real" fix is a meaningful design/infra commitment, not a quick patch.
Unlike `scaling-debt.md` (already-identified problems, pick up and fix any
time), entries here are **decisions to revisit later, not open items to
work through now** — each one exists so a future "should we finally do X"
conversation starts from what was already considered, instead of
re-deriving it from scratch.

Status legend: 🕒 deferred (not needed yet) · 🚧 being revisited · ✅ decided
and implemented

---

## 1. 🕒 Entity search: real substring-indexed full-text search

**Where:** `services/core-api/app/routers/entities.py::_search_filter`,
backing `GET /entities?q=` and `GET /entities/tags?q=` — type-ahead search
across `name`/`tags`/`location`/free-form `specs` values.

**Current state:** a per-household `$regex` substring scan (mid-word
matching, e.g. "rang" matches "Ranger"), scoped to the calling household by
the existing `entities(household_id, archived_at, domain)` compound index
(`scaling-debt.md` #1) — confirmed live via `explain()` to be `IXSCAN`, not
`COLLSCAN`; only the regex/`$objectToArray` work itself runs unindexed, over
just that household's own entities. `EntityCreate`/`EntityUpdate` cap
`name`/`location` at 200 chars, each tag at 100 chars (max 50 tags), and each
`specs` key/value at 200 chars (max 50 entries) — bounding the worst-case
per-document cost of that scan. See `scaling-debt.md` #7 for the full
investigation (`docs/scaling-debt-plans/` — this session's plan, once filed).

**Why it's deferred, not fixed:** Mongo's standard B-tree indexes only
accelerate prefix-anchored patterns (`^abc`), not arbitrary mid-word
substring regex — there's no cheap index-level fix for the search shape this
app actually wants (type-ahead, "rang" matches "Ranger"). Real
index-acceleration means one of:

- **A precomputed n-gram/trigram field** (e.g. `search_ngrams: list[str]`,
  indexed as a multikey field), used as a fast candidate pre-filter before
  the exact regex. Real engineering surface: a new schema field, a one-off
  backfill script for existing entities (no versioned migration framework
  exists yet — `services/worker/app/backfill_household_id.py` is the closest
  precedent, an explicitly one-off idempotent pass), and keeping the field in
  sync on every entity create/update (`routers/entities.py::create_entity`/
  `update_entity`). Trigram count per document also needs the same length
  caps already added for #7 to stay bounded.
- **Mongo Atlas Search** (`$search`) — not available today: this app
  self-hosts community Mongo (`docker-compose.yml`), and Atlas Search only
  ships on Atlas-hosted clusters. Would mean either migrating to Atlas or
  running a separate self-hosted search-capable Mongo variant.
- **A dedicated search engine** (MeiliSearch, Elasticsearch/OpenSearch,
  Typesense) — a new infra dependency (its own container, its own
  index-sync pipeline off entity writes), the heaviest option but also the
  most capable (typo tolerance, ranking, faceting) if search ever becomes a
  product focus rather than a household-scoped convenience filter.

**What would trigger revisiting this:** a household's entity count growing
large enough that per-keystroke search latency becomes noticeable in
practice (not just theoretically possible), or product direction making
search a first-class feature (cross-field ranking, typo tolerance) rather
than today's "filter a modest list as you type." Household entity count is
the driver (see `scaling-debt.md` #4's discussion of the same growth
dimension), not household *count* — this isn't a multi-tenancy concern.
