# Scaling Debt

Design decisions that work fine at today's scale (one deployed household,
single-developer operation, local/self-hosted Docker Compose) but will get
more expensive to live with as the app grows — more households, more real
concurrent users, and a possible move to AWS. This is a tracking list, not a
plan: each item needs its own design decision before it's fixed, same as the
`qol-backlog.md` convention. Written 2026-08-06 via a 4-agent code audit
(core-api/libs, ai-service, worker/infra, frontend) after milestone M11.

**This is the second `scaling-debt.md`.** The original (added `387b9f0`,
covering the entity-domain-registry refactor) was accidentally deleted in
commit `75873be` after most of its items were fixed — `roadmap.md` and
`docs/plans/m9-multi-household-accounts.md` still reference it by number for
the permission-seam work (`libs/auth`, `check_permission()`/`PERMISSIONS`),
which is real and still in place; those references have been updated to
point at item [#8](#8-permissions-registry-is-still-almost-entirely-empty)
below rather than the dead file. That doc's "what's already solid" callouts
(the `DOMAIN_REGISTRY`/`ENTITY_DOMAINS` pattern, generic per-resource
routers/collections, field-driven `EntityForm`) were re-verified during this
audit and are still holding up — see the closing section.

Status legend: 🔴 not started · 🟡 partially addressed · 🟢 fixed

---

## A. Data layer & multi-tenancy (`core-api`, `libs/shared`, `libs/auth`)

### 1. 🟢 Only one Mongo index exists anywhere in the system
**Where:** `services/core-api/app/indexes.py:23` — the *only* `create_index`
call in the whole codebase (`entities`: `household_id, archived_at,
domain`). Nothing on `worker` or `ai-service` either.

**Why it matters:** `users` has zero indexes, including no index on
`email`. `/auth/login` (`routers/auth.py:79`) and `/auth/signup`'s
duplicate-check (`routers/auth.py:102`) — the single highest-frequency
request path in the app — full-scan the entire `users` collection on every
call. Worse, the signup check-then-insert is a genuine TOCTOU race: two
concurrent signups with the same email can both pass the existence check
and both insert, since there's no unique index to reject the second write
at the database level.

**Severity:** blocks-AWS-launch. Fix: a unique index on `users.email`, plus
indexes on the hot filters below (#2).

**Fixed 2026-08-07** per
[`scaling-debt-plans/sd1-2-mongo-indexing-and-signup-race.md`](scaling-debt-plans/sd1-2-mongo-indexing-and-signup-race.md)
(bundled with #2). `users` gained a unique index on `email`
(`indexes.py`), and `/auth/signup` / `/auth/accept-invite` now catch
`pymongo.errors.DuplicateKeyError` around their `insert_one` and convert a
lost race into the same `409` the pre-check already returned, instead of
an unhandled `500` — the pre-check stays in place as a fast path, the
index is what actually closes the race. Verified live against the real
running stack: `db.users.getIndexes()` shows the new unique index;
`explain()` on a `users.email` lookup shows `IXSCAN`/`email_1`, 1 document
examined (was a full collection scan); a real duplicate-email signup still
returns `409`; a fresh signup still succeeds. New tests simulate the race
itself (monkeypatch the pre-check to see no conflict while a colliding
user already exists, confirming the index — not just the pre-check — is
what stops it) in `test_auth_signup.py` and `test_invites.py`; all 189
`core-api` tests pass.

### 2. 🟡 `logs`, `schedules`, `documents`, `sessions`, `invites` are also unindexed
**Where:** every query in `routers/logs.py`, `routers/schedules.py`,
`routers/documents.py`, `aria_auth/session.py:77`, and
`worker/app/tasks/send_overdue_digest.py:70-79`.

**Why it matters:** `_resync_schedule` (`logs.py:103-106`) runs a sorted
`find_one` against `logs` on *every* log create/update/delete — unindexed
sort+filter. `send_overdue_digest` runs one unindexed `schedules.find` per
household, per day, so the daily digest's total cost is O(households ×
total schedules across all of them), not O(one household's schedules).
`sessions.find_one({"_id": ...})` is fine (Mongo auto-indexes `_id`), but
nothing else on that collection is.

**Severity:** real-degradation-at-modest-scale, worsening linearly with
data volume — this gets materially worse well before what most people
would call "modest" real-world household counts.

**Fixed 2026-08-07 for `logs`/`schedules`/`documents`** per
[`scaling-debt-plans/sd1-2-mongo-indexing-and-signup-race.md`](scaling-debt-plans/sd1-2-mongo-indexing-and-signup-race.md)
(bundled with #1) — `logs` gained compound indexes for the entity-scoped
history query and the schedule-resync lookup; `schedules` gained one for
the entity-scoped list and one shared by `due-soon`/`calendar`/the
worker's daily digest (all three filter on the same
`household_id`/`active`/`pending_delete_at`/`interval_type` shape);
`documents` gained one for the entity-scoped list (a multikey index on
`entity_ids`). Still 🟡, not 🟢: `sessions` and `invites` were re-checked
during planning and found not to need a new index right now — `sessions`'
only query is a `_id` lookup (already covered by Mongo's automatic index;
the real problem there is unbounded *growth*, tracked separately as #5)
and `invites`' two query shapes are either `_id`-indexed already or a
small per-household list that isn't a hot path. Verified live: `explain()`
confirms `IXSCAN` on the new indexes; all 189 `core-api` tests pass.

### 3. 🔴 Unbounded per-entity history queries, both backend and frontend
**Where:** backend — `routers/logs.py:265-277` (`list_entity_logs`),
`routers/documents.py:195-206` (`list_entity_documents`),
`routers/schedules.py:340-343`/`372-384`/`443-450` (entity schedules,
due-soon, calendar), and the PDF-export data pull
(`routers/entities.py:396-414`) — all use `.to_list(length=None)`, no
`limit`/`offset` at all, unlike `GET /entities` which is properly capped at
`MAX_LIMIT=200`. Frontend — `src/hooks/useLogs.ts:6-12` and
`src/hooks/useEntityDocuments.ts:11-22` fetch and then `.map()`-render
every row with no windowing (`EntityDetailPage.tsx:229-290`,
`DocumentList.tsx:60-116`).

**Why it matters:** A vehicle or home system with years of service history,
or a Person entity with years of logs, loads and renders its *entire*
history on every page view and every PDF export. With, say, a few thousand
logs on one long-lived entity, that's a multi-thousand-document unindexed
fetch (compounding #2) plus a multi-thousand-row DOM render, on every visit
— not just what's actually displayed above the fold.

**Severity:** real-degradation-at-modest-scale — and it hits exactly the
households this app is meant to serve well (long-lived, well-tracked
entities), not edge cases.

### 4. 🔴 Entities list has a silent, uncommunicated 100-row cap
**Where:** `src/api/entities.ts:26-39` (`listEntities`) and
`src/hooks/useEntities.ts:7-16` never pass `limit`/`offset` to `GET
/entities`, which defaults to 100 (`routers/entities.py`, `MAX_LIMIT=200`).
No `has_more`/cursor in the response type, no "load more" UI anywhere in
`EntityListPage.tsx`, `SearchBar.tsx`, or `EntityCombobox.tsx`.

**Why it matters:** This is the *exact* bug already fixed once, for tags
specifically — `useEntityTags` (`useEntities.ts:18-37`) got real
`useInfiniteQuery` pagination with a "Load more" button
(`TagFilterModal.tsx`) after the same silent-cap problem was caught there.
The base entity list itself never got the same treatment, even though the
backend already supports `limit`/`offset`/`q`. A household with 100+
entities (very plausible after a few years: rooms, systems, appliances,
vehicles, tools, projects, people) will silently never see anything past
the first page — in the list, in filters, in bulk-select-all, or in global
search — with zero error or "N more" affordance telling the user data is
missing.

**Severity:** real-degradation-at-modest-scale today, trending toward
genuinely broken as households accumulate more entities over time. Silent
data loss is worse than a visible bug — prioritize this one.

### 5. 🔴 No session cleanup — `sessions` grows without bound
**Where:** `libs/auth/src/aria_auth/session.py:84-86` — a session row is
only ever deleted the one time its own already-expired token happens to be
presented again; logout deletes exactly one row
(`routers/auth.py:149`). No Mongo TTL index (`expireAfterSeconds`), no
Celery Beat sweep analogous to `purge_expired_trash`.

**Why it matters:** With real multi-user traffic (7-day session TTL,
multiple devices per person), the overwhelming majority of sessions just
expire unused and stay in the collection forever — an ever-growing,
unindexed (see #2) table that's queried on literally every authenticated
request.

**Severity:** real-degradation-at-modest-scale. Cheap fix: a Mongo TTL
index on the session's expiry field.

### 6. 🔴 Entity trash cascade (delete/restore) is non-transactional; the purge sweep is a per-entity round-trip loop
**Where:** `routers/entities.py:741-770` (`delete_entity` — 3 sequential
un-transacted writes: `entities.update_one`, then `logs.update_many`, then
`schedules.update_many`) and `:715-738` (`restore_entity_from_trash`, same
shape). `services/worker/app/tasks/purge_expired_trash.py:28-77` — the
hourly sweep does 6+ sequential Mongo round-trips *per expired entity*
(`delete_one`, `delete_many` × 2, `find`, `update_many` × 2) inside a plain
Python `for` loop, no `bulk_write`.

**Why it matters:** A crash/timeout between `delete_entity`'s three writes
leaves an entity trashed while its logs/schedules are still live and
visible via the list endpoints — a real, silent state inconsistency (unlike
the schedule-resync gap, which is explicitly documented and accepted as
fine). The purge sweep's cost scales linearly with concurrent trash volume
across *all* households sharing the hourly Beat run, and it shares a Celery
queue with OCR (#20) — a busy trash day and a busy upload day compete for
the same worker slots.

**Severity:** real-degradation-at-modest-scale — correctness risk grows
with write concurrency, not just data size.

### 7. 🔴 Entity search (`$regex`) is unindexed, and the code's own comment says so
**Where:** `routers/entities.py:112-158` (`_search_filter`), backing `GET
/entities?q=` (per-keystroke type-ahead, `min_length=1`) and `GET
/entities/tags?q=`. The docstring at `entities.py:126-128` still reads "No
indexes exist anywhere in `core-api` yet (single-household data, not
multi-tenant scale)" — stale as of `indexes.py` landing, and never revisited
for this path.

**Why it matters:** Type-ahead search runs a full per-household regex scan
(plus an `$objectToArray` scan of the free-form `specs` dict) on every
keystroke. This is the single most expensive query shape in the app, and it
scales directly with per-household entity count — exactly the count that
also drives #4.

**Severity:** real-degradation-at-modest-scale.

### 8. 🔴 `PERMISSIONS` registry is still almost entirely empty
**Where:** `libs/auth/src/aria_auth/permissions.py:27-30` — only `(None,
"delete")` and `(None, "undelete")` are role-gated (owner-only). Every
other mutating action (create/update/archive/restore, across every domain)
is open to any household member.

**Why it matters:** Fine for a single trusted household; as real
multi-household accounts onboard less-trusted members (roommates, tenants,
contractors), there's no enforcement seam being exercised beyond the two
hardcoded actions, even though the whole `Depends()`-based mechanism to add
more is already built and just needs registry entries.

**Severity:** minor-cleanup (product-scope gap, not a scaling failure per
se) — flag as a decision point before onboarding real non-owner members.

### 9. 🔴 Seed household/admin account created unconditionally, with a guessable default password
**Where:** `main.py:17` (`ensure_seed_household` runs in `lifespan` with no
environment gate), `app/config.py:21`
(`admin_password: str = "aria-dev"`).

**Why it matters:** `docker-compose.prod.yml` does wire a real
`CORE_API_ADMIN_PASSWORD` today, but there's no fail-fast if that var is
ever missing in a future deploy (ECS task def, ad-hoc redeploy, etc.) — it
would silently seed `owner@household.local` / `aria-dev` into production
with no warning.

**Severity:** minor-cleanup — require the var outside dev, or refuse to
seed if unset.

---

## B. Auth, abuse resistance, and public-internet readiness

### 10. 🔴 No rate limiting or brute-force protection on `/auth/login` or `/auth/signup`
**Where:** `routers/auth.py:68-129`; confirmed no rate-limit/throttle
library anywhere in `core-api` — `aria_shared/middleware.py` only does
CORS.

**Why it matters:** The app currently sits behind Caddy on a single
self-hosted machine, reachable from a home LAN plus whatever Caddy exposes
publicly. Once real signups are open to the internet, this is an
unthrottled credential-stuffing and signup-spam surface — nothing limits
password guesses per account or household creation per IP.

**Severity:** blocks-AWS-launch (or blocks any real public exposure,
AWS or not).

### 11. 🔴 `POST /chat` requires no authentication and has no rate limiting
**Where:** `ai-service/app/routers/chat.py:535-537` — `session_cookie:
str | None = Cookie(default=None, ...)`, never rejected if `None`; grepped
the whole service, no rate-limit/semaphore library anywhere.

**Why it matters:** A cookie-less request still runs the full
supervisor→specialist→Ollama pipeline (degraded, but still 2+ real LLM
calls). Nothing stops one browser tab, one frontend retry-loop bug, or one
external caller from firing unlimited concurrent `/chat` requests — each
one contending for the single shared GPU (#13). This is the most direct
way one bad actor (or one bug) starves every other household's chat once
this is reachable from the open internet.

**Severity:** blocks-AWS-launch.

### 12. 🔴 No CI/CD pipeline
**Where:** no `.github/` directory anywhere in the repo — only local
`.pre-commit-config.yaml`.

**Why it matters:** No automated test run, build, or deploy gate before a
merge lands. Today's deploy path is entirely manual (`docker compose -p
aria-prod -f docker-compose.prod.yml --env-file .env.prod up -d --build`
run by hand, per `docker-compose.prod.yml`'s own header comment) — fine for
a single-developer project, not something you want to be doing by hand
once real user data is on the line.

**Severity:** blocks-AWS-launch in spirit — not a hard technical blocker,
but a real production-safety gap.

### 13. 🟡 No AWS secrets-manager story yet (current state is otherwise clean)
**Where:** `.gitignore` correctly excludes `.env`/`.env.*` (only
`.env.example`/`.env.prod.example` are tracked — verified, no real secret
committed). `docker-compose.prod.yml` sources every credential from plain
`${VAR}` substitution against a `.env.prod` file sitting on the host.

**Why it matters:** No leak today, but "plaintext env file on one host,
loaded via `--env-file`" doesn't map onto AWS secret-injection patterns
(ECS task secrets, Secrets Manager, SSM Parameter Store) — worth planning
before the migration, not after.

**Severity:** blocks-AWS-launch for real production hygiene; not urgent
otherwise.

---

## C. AI service / LLM orchestration (`ai-service`)

### 14. 🔴 Single-GPU Ollama, no concurrency limit or queue in front of it — the sharpest scaling wall in the app
**Where:** `ai-service/app/ollama.py:8-15` (module-global `httpx.AsyncClient`,
`timeout=300.0`, no pool limit, no semaphore at any call site);
`docker-compose.llm.yml` (one `ollama` container, single GPU reservation,
explicitly shared between `ai-service` chat traffic *and* `worker`'s
embedding calls — "There's only one GPU on this machine... no reason to
duplicate it per stack"); no `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS`
tuning anywhere.

**Why it matters:** Every chat turn issues 2-4 *sequential* blocking Ollama
calls (classify → optionally tool-decide → stream the answer; the write
path and fuzzy-entity-match add more). With one process on one GPU and no
concurrency tuning, Ollama itself will start serializing or OOMing once
more than roughly one household is chatting at the same moment — and
concurrent document-upload OCR jobs (`worker`'s embedding calls) compete
for that exact same GPU slot with zero prioritization between "someone is
mid-conversation" and "a document just finished OCR." There's no queue, no
429, no shorter timeout to fail fast — a backed-up Ollama just makes every
concurrent user's request hang for up to 5 minutes with no backpressure
signal at all.

**Severity:** blocks-AWS-launch for "real multiple households" — this is
the single most likely user-facing bottleneck the moment more than one
household chats concurrently. Fine as-is for today's one household.

### 15. 🔴 Supervisor/tool-choice routing is free-text LLM parsing, brittle by construction
**Where:** `ai-service/app/agents/nodes.py:56-79` (`_SUPERVISOR_CHOICE_WORDS`
— "action" was deliberately chosen as a stand-in word specifically to dodge
false-positive matches against ordinary prose containing "action");
`ai-service/app/adapters/qwen.py:36-58` (`parse_choice` picks whichever
choice word appears *earliest* in the reply via a word-boundary scan) and
`:60-67` (`parse_tool_decision` — any non-JSON reply silently becomes
`{"tool": None}`).

**Why it matters:** This is documented in the code's own comments as a
live, previously-caught bug pattern (a specialist name colliding with
ordinary English, a model hallucinating a default location) — necessary
because Ollama's native tool-calling has a documented bug against Qwen3.
Every additional specialist added increases the odds some choice word
collides with normal model output, silently misrouting the turn. This is
inherent to the current model/serving choice, not something core-api-style
refactoring fixes — worth keeping in mind if the AWS move also means
reconsidering the LLM backend (a hosted API with real structured
tool-calling would remove this whole class of bug).

**Severity:** real-degradation-at-modest-scale — gets worse as specialist
count grows, not catastrophic today.

### 16. 🔴 Entity grounding is O(n) per message with no per-conversation caching
**Where:** `ai-service/app/entity_grounding.py:54-77`
(`find_matching_entities` — a Python loop + `re.search` over every fetched
entity's name/tags, on *every* chat turn) and `:90-126`
(`resolve_fuzzy_entity_match`, sending up to
`AI_SERVICE_ENTITY_FUZZY_MATCH_CANDIDATE_LIMIT` — default 50
— entities into one LLM prompt when the deterministic pass misses).
`core_api_client.py:16` (`ENTITIES_FETCH_LIMIT = 200`) refetches the full
entity list from core-api fresh on every single message, with no caching
across turns of the same conversation.

**Why it matters:** Invisible at today's scale. For a household that has
grown its entity list into the hundreds (very plausible — see #4), every
chat turn pays: a full `GET /entities` round-trip, an O(n) regex scan, and
— whenever the deterministic match misses — up to 50 entities' full
name/domain/tags rendered into an LLM prompt. None of this is cached even
across the same conversation's back-to-back turns, since each turn gets a
fresh `thread_id` (see #17 for why that's fine on the checkpoint side, but
it does mean zero reuse of anything computed on the prior turn).

**Severity:** real-degradation-at-modest-scale — household entity count is
the driver, not user count, but that count only grows over a household's
lifetime with this app.

### 17. 🟢 Checkpoint/thread growth — verified NOT a real risk
**Where:** `ai-service/app/routers/chat.py:385` (`thread_id =
str(uuid4())`, a fresh never-reused thread per turn), `app/agents/graph.py:73-88`
(60-minute TTL, `refresh_on_read=True`).

**Why it's fine:** Included for completeness since it was a natural
suspicion — the frontend resends full history client-side each turn, so
each LangGraph checkpoint stores exactly one turn's orchestration state
under a random UUID, expiring in an hour. No cross-conversation
accumulation, no cross-household leak risk. No action needed here.

### 18. 🔴 No length cap on chat message content or history length
**Where:** `ai-service/app/schemas/chat.py:6-10`
(`content: str = Field(min_length=1)`, no `max_length`) and the `messages`
list itself has no length cap either. On the frontend side,
`src/pages/ChatPage.tsx:171-174` resends the *entire* accumulated message
history on every turn (`useStreamChatMessage.ts`, `api/chat.ts:115-130`
just forward whatever's given, no truncation).

**Why it matters:** Combined with #11 (no auth/rate limit) and #14 (single
GPU, no backpressure), one oversized request or one long back-and-forth
session directly inflates how long a GPU slot is held, starving every other
concurrent household. Total request bytes also grow roughly quadratically
with turn count within one long chat session, since each turn re-sends
everything before it. Cheap fix relative to its blast radius: a
`max_length` on content, a cap on message count/total tokens, sent from
both ends of the wire.

**Severity:** real-degradation-at-modest-scale.

### 19. 🟡 Adding a specialist touches five hand-maintained sites (partially mitigated already)
**Where:** `agents/state.py:77` (`VALID_AGENTS` tuple), `:82-90`
(`AGENT_LABELS` dict), `agents/nodes.py:65-79`
(`_SUPERVISOR_CHOICE_WORDS`/`_AGENT_BY_CHOICE_WORD`), `agents/graph.py:30-49`
(new node + a new `_route` conditional-edge entry), plus a new prompt
clause in `_SUPERVISOR_SYSTEM_PROMPT`.

**Why it's only 🟡, not 🔴:** Two import-time `assert`s (`state.py:97-99`,
`nodes.py:77-79`) already catch drift between these tables at startup
rather than deep in a runtime failure — and `research_node`'s own tool
dispatch already learned this lesson once, refactoring an if/elif into a
plain dict lookup (`_RESEARCH_TOOL_HANDLERS`, `nodes.py:638-643`,
explicitly called out in its own comment as a past code-review fix). The
*specialist-level* routing hasn't had the same treatment yet.

**Severity:** minor-cleanup — worth applying the same dict-of-handlers
pattern to specialist routing before the roster grows past ~6-7, but the
guardrails already in place mean this fails loud, not silent.

---

## D. Background jobs (`worker`) and infra

### 20. 🔴 One Celery queue for everything — OCR can starve time-sensitive Beat tasks
**Where:** `services/worker/app/celery_app.py` — no `task_routes`, no
`queue=` on `process_document`, `send_overdue_digest`, or
`purge_expired_trash`; the worker command has no `-Q` flag
(`docker-compose.yml`/`docker-compose.prod.yml`, `celery -A app.celery_app
worker`).

**Why it matters:** OCR is CPU/IO-heavy and slow; it shares the exact same
queue and worker pool as the hourly trash purge and the daily digest. A
backlog of document uploads across many households will directly delay
housekeeping tasks with no isolation — invisible today, a real user-facing
QOL regression once upload volume is non-trivial.

**Severity:** real-degradation-at-modest-scale, trending toward
blocks-AWS-launch once background job volume is real.

### 21. 🔴 No retry/backoff policy on any Celery task — failures vanish
**Where:** no `autoretry_for`/`max_retries`/`retry_backoff` anywhere in
`services/worker`. `process_document.py:110-111` catches everything and
just flips `processing_status="failed"`, with no `self.retry()` and no
automatic re-enqueue anywhere else in the codebase.

**Why it matters:** A transient failure (Ollama momentarily busy from #14,
an S3 blip, a Mongo hiccup) permanently fails a document upload with no
recovery path except an operator manually querying for `processing_status:
"failed"` and re-triggering — there's no user-facing "retry" action either.
Transient infra hiccups become common, not rare, once there's real traffic.

**Severity:** real-degradation-at-modest-scale.

### 22. 🔴 Default Celery ack-early semantics — an OOM'd worker silently loses in-flight OCR jobs
**Where:** no `task_acks_late`/`worker_prefetch_multiplier` override
anywhere — Celery's default (ack-before-execute) applies to
`process_document`.

**Why it matters:** If the worker container is OOM-killed mid-OCR
(plausible for a large scanned PDF, and this is the most memory-hungry
container), the task is already acked and is never requeued — the document
gets stuck at whatever `processing_status` it last reached with zero record
that the job was ever lost. Compounds #21 directly.

**Severity:** real-degradation-at-modest-scale.

### 23. 🔴 `send_overdue_digest` is one long sequential task, not idempotent across retries
**Where:** `services/worker/app/tasks/send_overdue_digest.py:52-111` —
iterates every household's opted-in users in a flat Python loop, calling
`mail.send_mail()` (a brand-new blocking `smtplib` connection per call)
synchronously for each one; no "already sent today" marker per user.

**Why it matters:** Runtime scales linearly with household×user count,
occupying one worker slot for the whole run (worsening #20). Worse: if the
task crashes or gets retried partway through, already-emailed users receive
a duplicate digest on the next run — this is a correctness bug, not just a
performance one.

**Severity:** real-degradation-at-modest-scale (duration); the missing
dedupe is a real correctness bug worth fixing regardless of scale.

### 24. 🔴 No horizontal-scaling story for the worker or core-api
**Where:** exactly one `worker` service defined in both
`docker-compose.yml` and `docker-compose.prod.yml`, no `deploy.replicas`,
no documented pattern for running N worker containers against the same
Redis broker.

**Why it matters:** Celery concurrency is left at its untuned default
(prefork = host CPU count) and there's no `deploy.replicas`/ECS
desired-count anywhere — scaling out today means someone noticing and
hand-editing infra, not something the current setup anticipates.

**Severity:** real-degradation-at-modest-scale / blocks-AWS-launch,
specifically for the "real multiple households" goal.

### 25. 🔴 No backup story for Chroma (the vector index); Mongo/MinIO backups exist but are entirely manual
**Where:** `scripts/backup-mongo.ps1`/`backup-minio.ps1` exist and are
documented in `README.md` as commands to run by hand, with no scheduled
job. Nothing analogous exists for the `chroma_data`/`chroma_data_prod`
Docker volume at all.

**Why it matters:** Losing the Chroma volume means silently losing the
entire semantic-search index for every household, with no automated
detection and no documented rebuild-from-Mongo procedure (re-running OCR +
embedding for every document, per household). Mongo/MinIO at least have
scripts, but "operator remembers to run this periodically" is a
single-developer practice, not a real-user production one.

**Severity:** blocks-AWS-launch for real user data-loss risk (Chroma
specifically); real-degradation-at-modest-scale for the manual-only
Mongo/MinIO cadence (a scheduled job is a small fix).

---

## E. Frontend (extensibility + runtime scale)

### 26. 🔴 No route-level code splitting — the whole app is one bundle
**Where:** `src/App.tsx:5-14` eagerly imports every page; no
`React.lazy`/`Suspense` boundary anywhere in `src/`.

**Why it matters:** Every user pays the parse/compile cost of chat SSE
handling, the calendar grid, PDF export, photo capture, and all five entity
domain forms on first load, regardless of which page they actually open.
This only grows as more domains/features ship, and nothing in the current
structure would naturally introduce splitting later without a deliberate
pass.

**Severity:** minor-cleanup today, compounding toward
real-degradation-at-modest-scale as the feature list keeps growing.

### 27. 🔴 Offline log queue: Workbox silently drops entries after 24h with no UI signal
**Where:** `src/sw.ts:19-22` — `BackgroundSyncPlugin` configured with
`maxRetentionTime: 24 * 60` (24 hours); `src/lib/pendingLogs.ts` stores
queued logs in `idb-keyval` with no age/size cap;
`useLogSyncListener.ts:29-97` only ever reconciles the UI store on an
explicit sync success/failure signal — it never independently checks a
record's age against Workbox's own 24h window.

**Why it matters:** A user offline for more than 24 hours (a multi-day trip
while still logging things) will have queued log-creates silently expire
out of Workbox's actual retry queue, but the `idb-keyval` record — the only
thing `PendingLogList` reads — stays forever showing `status: 'pending'`
with no indication it will never sync. This is data-loss-adjacent, not just
a rendering-scale issue: the underlying `createLog` call still works if the
user manually hits Retry, but nothing tells them they need to.

**Severity:** real-degradation-at-modest-scale, specific to the PWA/offline
path — but a real "your data silently vanished" bug when it hits.

### 28. 🔴 Hand-rolled per-resource data-fetching hooks, no shared factory
**Where:** `src/hooks/useEntities.ts` (116 lines, 11 exported hooks),
`useLogs.ts`, `useSchedules.ts`, `useDeleteDocument.ts`,
`useRenameDocument.ts`, `useUploadDocument.ts`, `useEntityDocuments.ts`,
`useDocumentDraft.ts`, `useHousehold.ts` — each independently reimplements
the same `useQuery({queryKey, queryFn})` / `useMutation({..., onSuccess: ()
=> qc.invalidateQueries(...)})` shape by hand (e.g. six near-identical
6-line mutation blocks in `useEntities.ts:47-101`).

**Why it matters:** This is the mirror image of the domain-registry win
(#29 below) — entity *shape* extensibility was solved with a real registry
and codegen, but data-fetching *hook* extensibility wasn't. A 10th resource
type (M9 sharing objects, future offline-sync records, etc.) means
copy-pasting a whole new ~60-100 line hook file rather than configuring an
existing generic one.

**Severity:** minor-cleanup — code-health/extensibility, not a runtime
risk, but directly answers "will we get if-statements a mile long as we
extend" for the data layer specifically: no, but we will get a lot of
copy-pasted files.

### 29. 🟢 Domain-registry discipline (from the original scaling-debt.md) — re-verified, still holding up
**Where:** grepped for `domain ===`/`switch(domain)`/`case '...'` outside
`src/domains/*.ts` across every feature shipped since the original fix
(calendar, bulk actions, export modal, mobile nav, pinned entities). The
only hits are filter-button active-state checks (`DueSoonPage.tsx:101`,
`EntityListPage.tsx:163`), not behavior branching — every real
domain-specific decision goes through `DOMAIN_REGISTRY[domain].uiVariant`/
`.fields`/`.logTypes`.

**Why it's worth calling out:** Genuinely good news — this is exactly the
kind of debt that tends to silently regress as feature velocity increases,
and it hasn't. No action needed; keep enforcing it as new features land.

### 30. 🟡 `api/types.ts` has no drift detector beyond entity attributes
**Where:** only `EntityAttributes`/`EntityDomain`/`LogType`
(re-exported from the codegen'd `src/domains/generated.ts`, guarded by
`libs/shared/tests/test_export_ts.py`) are protected against backend schema
drift. `Entity`, `LogEntry`, `Schedule`, `DueScheduleItem`,
`CalendarOccurrence`, `Document`, `DocumentDraft`, `SessionInfo`, `Member`,
`Household`, `Invite`, `CurrentUser` (`src/api/types.ts:9-173`) are all
hand-written against their Pydantic counterparts with nothing enforcing
agreement.

**Why it's 🟡, not 🔴:** No live mismatch was found during this audit (spot
checked `SharedWith` from M9 — it currently matches) — the gap is that
agreement is coincidental/manually-maintained rather than enforced, unlike
the sibling case that already has a real drift test.

**Severity:** minor-cleanup today, but a growing risk given the codegen
precedent already exists and wasn't extended here — a future field
rename/add on `LogEntry`/`Schedule`/`Document` compiles fine and fails
silently at runtime (`undefined` fields) instead of failing the build.

---

## What's already solid (context, not debt)

Re-verified during this audit, still true, don't need rework:

- **`DOMAIN_REGISTRY`/`ENTITY_DOMAINS`** and the Python→TypeScript codegen
  seam (`entities/export_ts.py` → `generated.ts`) — adding a domain remains
  "new config + 3 registrations," and no feature shipped since has
  reintroduced ad-hoc domain branching (#29).
- **`libs/auth`'s permission seam** — real `Depends()`-based
  `check_permission()` on every mutating route, session context threaded
  end-to-end. The registry is under-populated (#8), but the mechanism
  itself is sound and was the right foundation to build.
- **Blocking work inside async `core-api` handlers is consistently
  offloaded** — WeasyPrint, pypdf merging, boto3 S3 calls, and Pillow
  re-encoding are all wrapped in `run_in_threadpool`/`asyncio.to_thread`
  (`entities.py:526,528`, `documents.py:143,156,243`). No event-loop-blocking
  issue found anywhere.
- **Sessions are Mongo-backed**, not in-process — this already scales
  cleanly across multiple `core-api` replicas if #24 gets addressed.
- **Password hashing is adequate** — PBKDF2-HMAC-SHA256, 600k iterations
  (`libs/auth/.../passwords.py`).
- **Chroma retrieval is genuinely household-scoped** (M9) — confirmed no
  cross-household leak: `retrieval.py` short-circuits to `[]` when
  `household_id is None` rather than falling back to an unscoped query, and
  the real query filters `where={"household_id": ...}`.
- **MCP write path is safe today** — `mcp_server.py` doesn't independently
  validate household ownership on `entity_id`, but `core-api`'s
  `require_entity_for_create` dependency does (scoped lookup + sharing
  check, 404 on mismatch), so the actual write endpoint is the enforcement
  point regardless of which caller hits it.
- **Secrets hygiene is currently clean** — no real secret has ever been
  committed; only `.example` files are tracked. The gap (#13) is about
  where secrets *should* live for AWS, not a current leak.
- **A real prod deployment already exists** — `docker-compose.prod.yml` +
  Caddy (automatic HTTPS via Let's Encrypt) + backup/restore scripts. This
  isn't a from-scratch AWS migration; it's hardening an already-working
  single-host deployment.

---

## How to use this document

1. Items are grouped by area but independent — pick any one without needing
   to sequence against the others, same as `qol-backlog.md`.
2. Before starting an item with real design surface (indexing strategy,
   Celery queue split, rate limiting, GPU/LLM serving changes), turn it
   into a proper sub-task plan (`EnterPlanMode`) — same bar as a roadmap
   milestone. Pure config/plumbing items (a TTL index, a `max_length`) don't
   need one.
3. When an item ships, flip its status marker and add a one-line note here,
   same convention as `roadmap.md`/`qol-backlog.md`.
4. If AWS migration work surfaces new debt not listed here, add it under
   the relevant section (or a new "F. AWS migration" section) rather than
   letting it live only in conversation history.
