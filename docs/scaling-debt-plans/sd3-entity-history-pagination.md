# Entity-scoped history pagination (logs, documents) + safety caps (schedules, PDF export)

Covers [`scaling-debt.md`](../scaling-debt.md) item **#3** — unbounded
per-entity history queries, both backend (`.to_list(length=None)` with no
`limit`/`offset`) and frontend (renders every returned row with no
windowing). Scoped to the **full fix** per user direction: real
backend pagination + frontend "load more" UI, not just a higher cap.

## Context

Four backend call sites fetch an entity's full history unbounded:
`list_entity_logs` (`routers/logs.py:265-277`), `list_entity_documents`
(`routers/documents.py:195-214`), `list_entity_schedules`
(`routers/schedules.py:333-343`), and the PDF-export data pull
(`routers/entities.py:396-422`, which independently re-queries all three
collections rather than calling the list endpoints). The frontend then
renders whatever comes back with no windowing
(`EntityDetailPage.tsx:229-290` for logs, `DocumentList.tsx` for documents).

Researching this surfaced a consumer outside `core-api`/`frontend`:
`ai-service/app/core_api_client.py::list_entity_logs` calls the exact same
`GET /entities/{id}/logs` endpoint for household-entity grounding
(`entity_grounding.py::_build_entity_context`), fetches the *entire* log
history, and then slices to `settings.entity_logs_limit` (default 5)
**client-side** — the same unbounded-fetch problem, one layer further out.
Any change to this endpoint's shape has to account for that call site too.

Item [#1/#2](sd1-2-mongo-indexing-and-signup-race.md) already added the
indexes this plan's pagination needs
(`logs`: `household_id, entity_id, occurred_at`; `documents`: `household_id,
entity_ids, uploaded_at`) — this plan doesn't add new indexes, it uses ones
that already exist.

## Scope decision

Not all four call sites get the same treatment:

- **Logs and documents get real pagination** (backend `limit`/`offset` +
  frontend "load more"). These are the two that genuinely grow unbounded
  over an entity's lifetime — years of service history, years of uploaded
  manuals/receipts/photos.
- **Schedules gets a safety cap, not pagination.** Checked every frontend
  call site: `schedulesQuery.data` is consumed as a flat array in 4 places
  in `EntityDetailPage.tsx`, including as the `schedules` prop `LogForm`
  uses to populate its "link to a schedule" picker (lines 212, 238, 392) —
  that picker fundamentally needs the complete list to search across, so
  converting it to `useInfiniteQuery` would either break that picker or
  require a separate, larger UX redesign (a searchable/paginated
  schedule-picker), out of scope here. Schedule counts per entity are also
  inherently small in practice (a handful of recurring maintenance items,
  not "years of accumulated events" like logs) — unlike logs/documents,
  this isn't a real unbounded-growth problem, just an unguarded query. Fix:
  add a generous hardcoded `.limit()` as a pure safety valve (no query
  param, no response-shape change), matching the reasoning `MAX_CALENDAR_RANGE_DAYS`
  already uses elsewhere in this codebase for the same kind of guard.
- **PDF export keeps fetching everything, on purpose** — the whole point of
  an export is a complete document; paginating it would produce an
  incomplete PDF. Gets the same kind of hardcoded safety-valve cap as
  schedules (guards against a truly pathological case hanging WeasyPrint/
  pypdf — not real pagination, no realistic household approaches it).
- **`due-soon`/`schedules/calendar` are out of scope** — both are already
  bounded by an explicit date range (`within_days`, `MAX_CALENDAR_RANGE_DAYS`),
  a fundamentally different (and already-addressed) kind of unboundedness
  than "every log/document an entity has ever accumulated."

## Design

### Backend: `logs.py` — real pagination

```python
class LogsPage(BaseModel):
    items: list[LogEntry]
    has_more: bool

MAX_LIMIT = 200

@router.get(
    "/entities/{entity_id}/logs", response_model=LogsPage, response_model_by_alias=False
)
async def list_entity_logs(
    entity_id: str,
    limit: int = Query(default=50, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> LogsPage:
    await require_entity_access(db, session, entity_id)

    docs = (
        await db.logs.find({"entity_id": entity_id, "household_id": session.household_id})
        .sort("occurred_at", -1)
        .skip(offset)
        .limit(limit + 1)  # fetch one extra to detect a next page, no separate count query
        .to_list(length=limit + 1)
    )
    items = [LogEntry.model_validate(doc) for doc in docs[:limit]]
    return LogsPage(items=items, has_more=len(docs) > limit)
```

Same `fetch limit+1, has_more = len > limit` pattern `list_entity_tags`
already established (`entities.py:314-327`) — no new pattern introduced.
No sharing-filter complication here: logs have no `shared_with` of their
own (they inherit access from the parent entity, already gated by
`require_entity_access` above), so `skip`/`limit` at the Mongo level is
correct as-is.

### Backend: `documents.py` — real pagination, sharing filter moved into the query

Documents are different from logs: `Document.shared_with` can be *narrower*
than its linked entity's sharing (`list_entity_documents`'s existing
comment: "Being able to see the entity doesn't automatically mean every
document attached to it is shared with you too"), currently enforced by a
**post-fetch** Python filter (`if has_shared_access(...)`). Combined with
`skip`/`limit`, a post-fetch filter breaks pagination correctness — a page
could come back with fewer than `limit` items after filtering, and
`has_more` would be wrong. Fix: express the same sharing rule as a Mongo
query clause, mirroring the pattern `list_entities` already uses
(`entities.py:252-268`) with `uploaded_by` in place of `created_by`:

```python
class DocumentsPage(BaseModel):
    items: list[Document]
    has_more: bool
    total: int

MAX_LIMIT = 200

@router.get(
    "/entities/{entity_id}/documents", response_model=DocumentsPage, response_model_by_alias=False
)
async def list_entity_documents(
    entity_id: str,
    limit: int = Query(default=50, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> DocumentsPage:
    await require_entity_access(db, session, entity_id)

    query: dict = {"entity_ids": entity_id, "household_id": session.household_id}
    if session.role != "owner":
        query["$or"] = [
            {"shared_with": "household"},
            {"shared_with": {"$exists": False}},
            {"shared_with": session.user_id},
            {"uploaded_by": session.user_id},
        ]

    total = await db.documents.count_documents(query)
    docs = (
        await db.documents.find(query)
        .sort("uploaded_at", -1)
        .skip(offset)
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    items = [Document.model_validate(doc) for doc in docs[:limit]]
    return DocumentsPage(items=items, has_more=len(docs) > limit, total=total)
```

`total` is the *filtered* count (what this session can actually see), not
the raw linked-document count — needed by `ExportPdfModal`'s "Include N
linked documents" label, which should never advertise a document the
viewer can't actually access. It's computed once per page request
(a `count_documents` on the same indexed prefix as the `find` — cheap, not
worth caching across pages for a value only used for display).

### Backend: `schedules.py` — safety cap only, no shape change

```python
async def list_entity_schedules(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[Schedule]:
    await require_entity_access(db, session, entity_id)

    docs = (
        await db.schedules.find({"entity_id": entity_id, "household_id": session.household_id})
        # Safety valve, not real pagination — a household's per-entity
        # schedule count is inherently small (a handful of recurring
        # maintenance items), unlike logs/documents which genuinely grow
        # unbounded. This just stops a pathological case from ever loading
        # thousands of rows into a UI (LogForm's schedule picker) that
        # needs the complete list to search across, not a paginated subset.
        .limit(500)
        .to_list(length=500)
    )
    return [Schedule.model_validate(doc) for doc in docs]
```

Response shape (`list[Schedule]`) and every existing call site — 4 in
`EntityDetailPage.tsx`, including the `LogForm` schedule picker — are
unchanged.

### Backend: `entities.py` — PDF export safety cap only

```python
# Safety valve, not real pagination — an export needs every row to be a
# correct document. This only guards against a truly pathological case
# (a household with tens of thousands of logs on one entity) hanging
# WeasyPrint/pypdf; no realistic household approaches it.
MAX_EXPORT_ROWS = 5000

log_docs = (
    await db.logs.find(...)
    .sort("occurred_at", -1)
    .limit(MAX_EXPORT_ROWS)
    .to_list(length=MAX_EXPORT_ROWS)
)
# same .limit(MAX_EXPORT_ROWS) added to the schedules and documents queries
# a few lines below.
```

### `ai-service`: adopt the new `limit` param instead of fetching-then-slicing

```python
# core_api_client.py
async def list_entity_logs(cookie: str, entity_id: str, limit: int) -> list[dict]:
    result = await _get(f"/entities/{entity_id}/logs", cookie, params={"limit": limit})
    return result["items"]
```

```python
# entity_grounding.py::_build_entity_context — was:
#   core_api_client.list_entity_logs(cookie, entity["id"])  # fetched everything
#   ...
#   logs=logs[: settings.entity_logs_limit],                # then sliced client-side
# becomes:
    core_api_client.list_entity_logs(cookie, entity["id"], limit=settings.entity_logs_limit),
    ...
    logs=logs,  # already server-side limited
```

Strictly a cleanup enabled by this plan's backend change, not new scope —
`ai-service` already only ever wanted 5 logs; it can now ask for exactly
that instead of fetching everything and throwing most of it away.
`list_entity_schedules` in the same file is untouched (schedules endpoint's
response shape didn't change).

### Frontend: `api/logs.ts` + `hooks/useLogs.ts`

```typescript
// api/logs.ts
export interface LogsPage {
  items: LogEntry[]
  has_more: boolean
}

export function listEntityLogs(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<LogsPage> {
  const search = new URLSearchParams()
  if (params?.limit !== undefined) search.set('limit', String(params.limit))
  if (params?.offset !== undefined) search.set('offset', String(params.offset))
  const qs = search.toString()
  return apiGet<LogsPage>(`/entities/${entityId}/logs${qs ? `?${qs}` : ''}`)
}
```

```typescript
// hooks/useLogs.ts
const LOGS_PAGE_SIZE = 50

export function useEntityLogs(entityId: string | undefined) {
  return useInfiniteQuery({
    queryKey: ['logs', entityId],
    queryFn: ({ pageParam }) =>
      api.listEntityLogs(entityId as string, { limit: LOGS_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * LOGS_PAGE_SIZE : undefined,
    enabled: entityId !== undefined,
  })
}
```

Every `queryClient.invalidateQueries({ queryKey: ['logs', entityId] })`
call site (`useCreateLog`, `useUpdateLog`, `useDeleteLog` — all in the same
file) is untouched; invalidating an infinite query by its base key
refetches all currently-loaded pages, same as it does for the tags
precedent.

### Frontend: `api/documents.ts` + `hooks/useEntityDocuments.ts`

Same shape as logs, plus `total`:

```typescript
export interface DocumentsPage {
  items: Document[]
  has_more: boolean
  total: number
}

export function listEntityDocuments(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<DocumentsPage> { /* same URLSearchParams shape as listEntityLogs */ }
```

```typescript
const DOCUMENTS_PAGE_SIZE = 50

export function useEntityDocuments(entityId: string | undefined) {
  return useInfiniteQuery({
    queryKey: ['documents', entityId],
    queryFn: ({ pageParam }) =>
      api.listEntityDocuments(entityId as string, { limit: DOCUMENTS_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * DOCUMENTS_PAGE_SIZE : undefined,
    enabled: entityId !== undefined,
    // Unchanged intent from today's version, adapted to check every loaded
    // page instead of a single flat array — a document mid-OCR could be on
    // any loaded page, not just the first.
    refetchInterval: (query) => {
      const pages = query.state.data?.pages
      const stillProcessing = pages?.some((page) =>
        page.items.some((doc) => IN_PROGRESS_STATUSES.has(doc.processing_status)),
      )
      return stillProcessing ? 2000 : false
    },
  })
}
```

### Frontend: `EntityDetailPage.tsx`

Both `logsQuery.data`/`documentsQuery.data` change shape from a flat array
to TanStack Query's `{ pages: [...] }`. Derive flat arrays once near the
top of the component (mirroring `TagFilterModal.tsx`'s
`tagsQuery.data?.pages.flatMap(...)`):

```typescript
const logs = logsQuery.data?.pages.flatMap((page) => page.items) ?? []
const documents = documentsQuery.data?.pages.flatMap((page) => page.items) ?? []
const documentTotal = documentsQuery.data?.pages[0]?.total ?? 0
```

Every existing `logsQuery.data?.…`/`documentsQuery.data?.…` read (the
history tab's `.map`, the documents tab's `<DocumentList documents={...}>`,
`documentCount={documentsQuery.data?.length ?? 0}` on `ExportPdfModal`, the
`documentsQuery.data && documentsQuery.data.length > 0` Export-PDF-button
branch) switches to the derived `logs`/`documents`/`documentTotal` locals
instead. A "Load more" button, identical in markup/behavior to
`TagFilterModal.tsx`'s (`hasNextPage` / `fetchNextPage()` /
`isFetchingNextPage`), goes at the bottom of the history list (inside the
`tab === 'logs'` block, after the existing `.map`) and the documents list
(inside the `tab === 'documents'` block, after `<DocumentList>`).

`schedulesQuery` and every one of its 4 call sites (including the two
`schedules={schedulesQuery.data ?? []}` props into `LogForm`) are
completely unchanged — its response shape didn't change.

## Out of scope

- Real pagination for `list_entity_schedules` — see Scope decision above.
- Real pagination for PDF export — see Scope decision above.
- `due-soon`/`schedules/calendar` — already bounded by date range, a
  different problem.
- A searchable/paginated schedule picker inside `LogForm` — the thing that
  would let schedules genuinely scale past a few dozen per entity without
  breaking that picker's UX. Not needed today (schedule counts stay small
  in practice); revisit only if that assumption stops holding.
- Item #4 (the top-level `GET /entities` list's own silent 100-row cap) —
  a separate, already-numbered item; not touched here even though it's the
  same *class* of bug.

## Verification

- Update every existing `core-api` test that reads these two endpoints as
  a bare list — `test_logs_crud.py:76`, `test_documents_crud.py:115`,
  `test_sharing.py:100-184` (4 call sites), `test_sharing_pre_migration.py:70-87`,
  `test_schedules_crud.py:268` — to read `.json()["items"]` instead of
  `.json()`.
- New tests in `test_logs_crud.py`: create more than `limit` logs for one
  entity, confirm `has_more: true` on the first page and `false` once every
  page is walked via `offset`; confirm total item count across all pages
  equals the number created.
- New tests in `test_documents_crud.py`: same pagination shape test, plus
  a sharing-scoped case — as a member excluded from a subset of an
  entity's documents (some `shared_with` narrower than "household"),
  confirm `total`/`has_more`/pagination all reflect only what that member
  can see, not the full underlying set (proves the Mongo-level `$or`
  filter is correct, not just present).
- New test in `test_schedules_crud.py` (or extend an existing one):
  confirm the response shape is still a bare list (regression — this
  endpoint's contract didn't change).
- `ai-service`: update `test_core_api_client.py`/`test_entity_grounding.py`
  mocks for `list_entity_logs`'s new `limit` param and `{"items": [...]}`
  response shape; confirm `_build_entity_context` still returns exactly
  `settings.entity_logs_limit` logs when core-api has more than that many.
- Frontend: `npx tsc -b`, `npx oxlint`, `npx vite build` all clean (no
  frontend test suite exists in this repo — same verification bar every
  prior frontend-touching plan in this codebase has used).
- Manual, via `claude-in-chrome` against the real running stack: create a
  scratch entity, add more than `LOGS_PAGE_SIZE` logs and more than
  `DOCUMENTS_PAGE_SIZE` documents to it via direct API calls (fast, avoids
  clicking through the form N times), then confirm in the browser that the
  History and Documents tabs each show the first page, a working "Load
  more" button, and the full set once fully paginated through; confirm the
  Export PDF button's "Include N linked documents" count matches the real
  total, not just the first loaded page; confirm a normal-sized entity
  (under one page) shows no "Load more" button at all, matching today's
  behavior exactly. Clean up the scratch entity afterward.
