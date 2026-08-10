# Entity list pagination (fix the silent 100-row cap)

Covers [`scaling-debt.md`](../scaling-debt.md) item **#4** — `GET
/entities` defaults to a 100-row cap with no `has_more`/cursor in the
response and no "load more" UI anywhere it's consumed
(`EntityListPage.tsx`, `SearchBar.tsx`, `EntityCombobox.tsx`). A household
with 100+ entities silently never sees anything past the first page, in the
list, in bulk-select-all, or in search — no error, no "N more" affordance.
The doc flags this one explicitly: "Silent data loss is worse than a
visible bug — prioritize this one."

## Context

`routers/entities.py` (`list_entities`) already accepts `limit`/`offset`
(default `limit=100`, capped at `settings.max_page_limit` = 200) but
returns a bare `list[EntityBase]` — no `has_more`. The frontend never
passes `limit`/`offset` at all (`api/entities.ts`, `hooks/useEntities.ts`),
so every list/search view gets exactly whatever the backend default
returns and has no way to know more exists.

This is the same shape of bug items #1-3 already fixed (indexing, signup
race, entity-history pagination) — the fix follows the exact pattern
`list_entity_tags`, `list_entity_logs`, and `list_entity_documents`
established: fetch `limit + 1`, return `{items, has_more}`,
`useInfiniteQuery` + "Load more" on the frontend (`TagFilterModal.tsx` is
the reference implementation).

Three consumers of `GET /entities` exist, all in-repo (checked, no
others): `EntityListPage.tsx` (the real browse/filter/bulk-select list),
`SearchBar.tsx` and `EntityCombobox.tsx` (type-ahead dropdowns), and
`ai-service/app/core_api_client.py::list_entities` (used by entity
grounding, `entity_grounding.py`, requesting `ENTITIES_FETCH_LIMIT=200`
per chat turn).

## Scope decisions

- **`status` becomes a real backend query param.** Today
  `EntityListPage.tsx` filters by status *client-side*, over whatever the
  backend happened to return. Once the list is paginated, that would mean
  a status filter only searches loaded pages — reintroducing the exact
  silent-gap bug this plan fixes. Fix: add `status: str | None` to
  `list_entities`, same equality-filter pattern as the existing `domain`
  param.
- **"Select all" scopes to loaded entities, with a hint.** Not building a
  filter-based "select all N matching, including unloaded" bulk endpoint —
  bigger feature, not needed today. `EntityListPage`'s "Select all"
  continues to mean "everything loaded so far", and a small hint appears
  next to it when `hasNextPage` is true ("Load more to include the rest in
  Select all") so it's never silently incomplete.
- **Search dropdowns (`SearchBar`, `EntityCombobox`) get a capped page, not
  infinite scroll.** A "Load more" button inside a transient type-ahead
  dropdown is an unusual pattern; the standard fix for a search box is a
  reasonable result cap plus a "refine your search" hint. Both request a
  smaller `limit` (20) and show a hint when `has_more` is true instead of
  silently truncating.
- **Stable sort order, added now.** `list_entities` had no `.sort()` at
  all (relied on incidental Mongo return order), which is unsafe for
  `skip`/`limit` pagination — a doc can be skipped or duplicated across
  pages if insertion order isn't fixed. Added `.sort([("name", 1), ("_id",
  1)])` — alphabetical, `_id` tiebreaker for stability, mirrors the
  tiebreaker `list_entity_logs` already uses. No new index needed at
  today's per-household entity counts (hundreds, not millions) — an
  in-memory sort after the existing `household_id/archived_at/domain`
  index narrows the result set is fine; revisit only if that assumption
  stops holding, same reasoning the doc already uses elsewhere for
  modest-scale items.
- **`ai-service` entity grounding**: `core_api_client.list_entities` just
  unwraps the new `{"items": [...]}` shape and keeps requesting
  `ENTITIES_FETCH_LIMIT=200` (unchanged) — a household with over 200
  entities still gets a truncated grounding set with no pagination here,
  but that's item #16 (entity-grounding scaling) territory, not this one.

## Design

### Backend: `routers/entities.py`

`list_entities` gains an `EntitiesPage {items, has_more}` response model, a
`status` query param, and `.sort([("name", 1), ("_id", 1)])` before
`skip`/`limit`; fetches `limit + 1` to detect a next page, same pattern
`list_entity_tags` already uses in the same file. Default `limit` drops
from 100 to 50 to match the page-size convention already used for
tags/logs/documents (all 50 on the frontend). `list_entity_tags` and
`list_trashed_entities` in the same file are untouched.

### Frontend: `api/entities.ts` + `hooks/useEntities.ts`

`listEntities` returns `EntitiesPage`. `useEntities` becomes a
`useInfiniteQuery` (`ENTITIES_PAGE_SIZE = 50`), taking `status` as a real
param instead of leaving it to callers to filter post-fetch. A new
`useEntitySearch` hook backs the two type-ahead call sites: a plain
`useQuery` requesting `SEARCH_RESULT_LIMIT = 20`, no infinite pagination.
All mutation hooks keep invalidating `['entities']` as a prefix —
unchanged, still invalidates both query shapes.

### Frontend: `EntityListPage.tsx`

`status` now flows to `useEntities` instead of a post-fetch `.filter()`.
`entities` is derived as `entitiesQuery.data?.pages.flatMap((page) =>
page.items) ?? []` (same pattern as `TagFilterModal.tsx`). Pinned/unpinned
split, "select all", and bulk archive/restore all operate on this
flattened array exactly as before — semantically "loaded so far". A "Load
more" button (`hasNextPage`/`fetchNextPage()`/`isFetchingNextPage`) is
added below the entity rows, and a small hint appears next to "Select all"
when `hasNextPage` is true.

### Frontend: `SearchBar.tsx` + `EntityCombobox.tsx`

Both switch from `useEntities` to `useEntitySearch`, read
`results.data?.items` instead of `results.data`, and show a "refine your
search" hint when `results.data?.has_more` is true.

### `ai-service`: `core_api_client.py`

`list_entities` unwraps `result["items"]` from the new response shape.
`entity_grounding.py` is otherwise unchanged — it already treats the
return value as a flat list.

## Out of scope

- Item #16 (entity-grounding O(n) scan, no per-conversation caching) —
  `ai-service` still fetches up to `ENTITIES_FETCH_LIMIT=200` per chat turn
  with no caching; unwrapping the new response shape doesn't change that.
- A filter-based "select all matching, including unloaded" bulk endpoint —
  see Scope decisions above.
- `list_entity_tags` / `list_trashed_entities` — different endpoints in the
  same file, already paginated (tags) or not part of this bug (trash, an
  owner-only low-traffic view).
- Item #7 (unindexed `$regex` search) — `_search_filter` itself is
  untouched; this plan only changes what wraps its results.

## Verification

Verified live against the real running stack (not just the automated
suites, which all pass): `core-api` — 221 tests pass (rebuilt the image
first; `tests/` isn't bind-mounted like `app/` is, so a stale image was
initially running old test files against the new endpoint — caught via a
misleading `TypeError` before rebuilding). `ai-service` — 219 tests pass,
including the two `test_core_api_client.py::test_list_entities_*` tests
updated to mock the new `{"items": [...], "has_more": ...}` shape; tests in
`test_entity_grounding.py`/`test_agents.py`/`test_chat.py` that monkeypatch
`core_api_client.list_entities` directly needed no changes, since they mock
above the shape-unwrap boundary. New `test_entities_pagination.py` covers
`has_more` flipping across pages, no duplicate/skipped entity when walking
every page (stable `name`/`_id` sort), and the new `status` param filtering
server-side (including combined with `limit`, to guard the exact bug this
plan fixes). Frontend: `npx tsc -b`, `npx oxlint` (0 errors, 2 pre-existing
unrelated warnings), `npx vite build` all clean.

Manual, via `claude-in-chrome` against the real household (not a throwaway
one — confirmed live via the browser's own session, not a fresh login):
created 40 scratch entities directly through the browser's authenticated
`fetch` (17 pre-existing + 40 = 57, past the 50-item page size) and
confirmed live: the "Select all" hint ("50 loaded — Load more below to
include the rest") appeared while a second page was unloaded and correctly
disappeared once "Load more" was clicked and the full set of 57 loaded; the
household's real entities (17, under one page) showed no "Load more" button
and no select-all hint before the scratch entities were added — no
regression for the common case; `SearchBar`'s dropdown, searching "ZZZ
Scratch" (40 matches), capped at 20 results and showed "Showing top 20 —
refine your search to narrow results."; direct API checks (`curl` against a
separate seed household) confirmed `has_more` flips correctly across a
`limit=3` page boundary with no duplicate/skipped entity, alphabetical
`name` sort, and `status=sold` filtering server-side rather than only over
a loaded page. All 40 scratch entities plus one abandoned leftover from an
earlier verification session ("PAGINATION SCRATCH TEST - delete me") were
moved to Trash afterward (this app's existing grace-period cleanup path);
the separate seed household's 56 scratch entities were also deleted the
same way.
