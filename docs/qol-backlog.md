# ARIA — Quality-of-Life Backlog

**Status:** living document, same spirit as `roadmap.md` but for polish rather
than milestones. Items here are small-to-medium, independently shippable
improvements to something that already works — not new capability areas.
Nothing here blocks or is blocked by the roadmap; pull items into a real
sub-task plan (`EnterPlanMode`) when picked up, same as a milestone bullet.

Status legend: ✅ done · 🚧 in progress · ⬜ not started

Each item notes the current behavior (verified against the running code, not
guessed) so scope is clear before anyone picks it up.

---

## Raised by the user (2026-07-20)

- ✅ **Theme should follow the user, not the browser.** Done — `theme` now
  lives on the `User` record (`core-api`), via a new `GET`/`PATCH /users/me`
  (self-service, not owner-gated like `/households/me`). `ThemeProvider`
  still reads/writes `localStorage['aria-theme']` first for an instant paint
  and offline/logged-out fallback, but now also fetches the account's theme
  on mount (and on login/signup/accept-invite, via a `['user']` react-query
  key) and adopts it when present — a second member logging in on their own
  device gets their own theme, not this browser's last-used one.

- ✅ **Global search bar.** Done, for entities (v1 scope — logs/documents
  have no household-wide list endpoint at all yet, so they're a separate
  follow-up). `GET /entities` now takes `q`, matching `name`/`tags`/
  `location`/`specs` values (case-insensitive, `re.escape`d). A header-level
  `SearchBar` in `Layout.tsx` debounces input (300ms, 2-char minimum) and
  shows a dropdown of matches; clicking one navigates to `/entities/:id`.

- ✅ **Filters for entities and "what's due".** Done. `DueSoonPage.tsx` gained
  a domain filter (same chip UI as `EntityListPage`, backed by a new
  `domain` query param on `GET /schedules/due-soon` since schedules don't
  carry an entity's domain themselves) and an "Overdue only" checkbox
  (client-side, filters the existing `is_overdue` field). `EntityListPage.tsx`
  gained a status dropdown (client-side filter — statuses are domain-specific
  via `DOMAIN_REGISTRY` and low-cardinality, so no backend change needed) and
  a tag filter. The tag filter started as a plain `<select>` derived from the
  currently-loaded (capped, domain/archived-filtered) entity page, but that
  silently hid tags outside the page — households accumulate tags fast
  enough (a few hundred expected in normal use) that this didn't scale. It's
  now a searchable modal (`TagFilterModal.tsx`, single-select, "Load more"
  pagination) backed by a new paginated `GET /entities/tags` endpoint
  (distinct tag values, `q`/`domain`/`include_archived`-scoped) and a new
  exact-match `tag` param on `GET /entities` itself, so filtering isn't
  limited to whatever page of entities happened to load. Changing the domain
  filter resets both status and tag since their option sets depend on domain.

- ✅ **Health tab should be owner-only.** Done — the "Health" `NavLink` in
  `Layout.tsx` is now gated on `session?.role === 'owner'`, and `/health` in
  `App.tsx` is wrapped in a new `RequireOwner` guard (redirects non-owners to
  `/`, same pattern as the existing `RequireAuth`), so a member can't reach
  the route directly by URL either.

- ✅ **Calendar view for "what's due," click a day to add.** Done, alongside
  its own direct extension below (shipped as one feature). `DueSoonPage.tsx`
  gained a List/Calendar toggle; `CalendarView.tsx` is a month grid (Monday-
  start, matching `lib/recurrence.ts`'s existing weekday convention) backed
  by a new `GET /schedules/calendar?from=&to=&domain=` endpoint. Unlike
  due-soon (which only reads each schedule's single cached `next_due_at`),
  this endpoint projects every occurrence of a recurring schedule that falls
  within the requested range via a new pure `project_occurrences()` in
  `app/logic/schedules.py` — time-based schedules step forward/backward from
  their `last_completed_at` anchor, monthly schedules are computed per
  calendar month, and everything is floored at the schedule's `created_at`
  so a past range before a schedule existed correctly returns nothing.
  Past months are navigable; a caveat worth remembering is that "past
  occurrences" of a `time`-type schedule are a backward projection from the
  *current* anchor, not stored history — completing the schedule off-cadence
  later will shift previously-shown past dates, since there's no
  per-occurrence completion log to be faithful to instead.

- ✅ **Calendar-added items create the real entity schedule/log.** Done as
  part of the same change. Clicking a day opens `DayComposer.tsx`: pick an
  existing entity via a new `EntityCombobox.tsx` (a `SearchBar.tsx`-derived
  typeahead that returns a value instead of navigating) or create one inline
  via the existing `EntityForm` (now takes an optional `initialDomain` prop),
  then choose "Recurring schedule" or "One-off / mark as done" to render the
  existing `ScheduleForm`/`LogForm` (the former now takes an optional
  `initialDate` prop to pre-fill the clicked date) wired to the real
  `useCreateSchedule`/`useCreateLog` mutations — no calendar-only event path
  exists anywhere in this design.

## Additional suggestions

- ✅ **Bulk actions on the entity list.** Done, scoped to archive/restore
  (hard-delete stays per-entity — owner-only, no undo, already its own
  backlog item). `EntityListPage.tsx` gained a checkbox per row (restructured
  from a single `<Link>` row into a `<div>` with a checkbox sibling to the
  `<Link>`, so navigation still works), a tri-state "Select all" checkbox
  scoped to the currently-filtered rows, and a bulk action bar (Archive /
  Restore / Clear) that appears once anything is selected. Backed by two new
  backend endpoints, `POST /entities/bulk-archive` and `.../bulk-restore`
  (`app/routers/entities.py`), rather than looping the frontend over the
  existing single-id endpoints — one `find` + one `update_many`, running the
  same per-entity household/permission/sharing checks `require_entity()`
  already does for the single-item routes, returning
  `{succeeded, not_found, forbidden}` so the UI can show e.g. "Archived 4 of
  5 selected — 1 failed." (no toast system exists in this codebase, so this
  is a plain inline banner, same convention as `EntityForm`'s inline errors).

- ✅ **Recent/pinned entities.** Done, scoped to manual pin only (asked the
  user recent-vs-pinned-vs-both; pinned won — deliberate curation that
  stays put, rather than an auto-reshuffling recently-viewed strip).
  `EntityListPage.tsx` rows gained a ★/☆ toggle button (a plain Unicode
  glyph — no icon library anywhere in this codebase to reuse), and the list
  now renders pinned entities first under a small "Pinned" label with a
  divider before the rest, still fully respecting the existing domain/
  status/tag/archived filters. Persisted per-user via a new
  `pinned_entity_ids: list[str]` field on `User` (`libs/shared`), read/
  written through the same `GET`/`PATCH /users/me` self-service endpoints
  the `theme` field already uses (no owner-gating, `model_fields_set` so an
  omitted field is a no-op) — syncs across devices like theme does, not a
  per-browser `localStorage` list. New `usePinnedEntities.ts` hook mirrors
  `ThemeContext`'s exact read/write shape (instant local-state toggle,
  background `PATCH`, best-effort — no retry/rollback, same tradeoff theme
  already accepts) but as a plain hook, not a Context, since only one page
  consumes it and pins have no pre-paint concern to solve. Hard-deleting an
  entity (`DELETE /entities/{id}`) now also `$pull`s its id from every
  household member's `pinned_entity_ids`, alongside the existing logs/
  schedules/documents cascade cleanup — archiving does *not* unpin, since
  archive is reversible and an archived entity is still validly pinnable.

- ✅ **Notifications for overdue items.** Done, both parts. In-app: a new
  `OverdueBanner.tsx`, rendered in `Layout.tsx` alongside `OfflineBanner`
  so it shows on every route, not just "What's Due" — one aggregated
  message ("You have N item(s) overdue.") rather than one per item,
  clickable through to `/due-soon`, dismissible, and — once dismissed —
  stays hidden for the rest of that calendar day via a
  `localStorage['aria-overdue-dismissed-date']` date string (client-side
  only, resets itself the next day). Email: a new opt-in
  `User.notify_overdue_email` field (default `False`, self-service via the
  existing `GET`/`PATCH /users/me`, toggled from a new card on
  `ProfilePage`), sent daily by a new `send_overdue_digest` Celery task
  (`services/worker/app/tasks/send_overdue_digest.py`) querying
  `schedules`/`entities`/`users` directly via pymongo, one plain-text email
  per opted-in user scoped to their household's overdue items. This
  required standing up infrastructure that didn't exist: the backlog text
  assumed M9's invite flow had SMTP to "leverage" — it didn't (link-only
  invites) — so this added a stdlib-`smtplib`-based `app/mail.py`, a new
  Celery Beat schedule (`services/worker/app/celery_app.py`, daily at
  13:00 UTC — no household timezone concept exists yet, see below), a new
  `worker-beat` compose service, and a `mailpit` dev-only service (SMTP
  catcher, web UI at `localhost:8025`) so the digest is inspectable
  locally with no real mail account. Production sends through a real relay
  configured via `WORKER_SMTP_*` in `.env.prod`. Not filtered by per-entity
  `shared_with` sharing rules — this is an explicit opt-in notification,
  not a passive view, and that check needs a live `SessionContext` this
  task doesn't have; revisit only if the sharing gap matters in practice.

- ✅ **Undo for delete.** Done, as a grace-period trash (asked the user
  confirm-dialog-with-countdown vs. grace period; grace period won).
  `DELETE /entities/{id}` (`entities.py`) no longer deletes outright — it
  stamps a new `pending_delete_at` on the entity and cascade-linked logs/
  schedules (new field on all three `libs/shared` models), which every
  list/detail route (`list_entities`, `get_entity`, `list_entity_tags`,
  `require_entity_access`/`require_entity_for_create` — so logs/schedules
  against a trashed entity 404 too, `schedules/due-soon`, `schedules/
  calendar`) now excludes unconditionally, unlike `archived_at`'s
  opt-in-via-flag treatment — trash is a terminal state, not something a
  view opts back into. A new owner-only `undelete` permission action
  (`libs/auth`) backs `POST /entities/{id}/restore-from-trash` (clears
  `pending_delete_at` on the entity + cascade rows) and a new `GET
  /entities/trash` listing. Documents and `pinned_entity_ids` are left
  untouched while trashed (reversible, same reasoning archive already
  relies on) — the actual cascade purge (logs/schedules delete, document
  unlink + orphan cleanup via the existing `enqueue_document_deletion`
  path, `pinned_entity_ids` pull, entity delete) moved from the inline
  route to a new hourly Celery Beat sweep, `purge_expired_trash`
  (`services/worker`), gated by a new `entity_trash_grace_hours` setting
  (worker-only — core-api never needed it, since it only ever stamps
  "now"). Frontend: new owner-gated `/trash` route + nav link,
  `RecentlyDeletedPage.tsx` (modeled on `EntityListPage`'s row shape) with
  a "purges in N days" badge, and `EntityDetailPage`'s delete confirm
  copy now says "Move to Trash" instead of "Permanently delete." Caught
  live in browser verification (not by the type checker or test suite):
  core-api serializes datetimes without a `Z`/offset suffix, so
  `new Date(pending_delete_at)` in the badge's day-math silently parsed it
  as local time instead of UTC, off by the browser's UTC offset — fixed by
  forcing UTC when no designator is present, rather than left as a latent
  bug no frontend code had hit before (nothing else parses a backend
  timestamp into a JS `Date`, so there was no existing convention to
  follow).

- ✅ **Household-level default settings.** Done, full scope (name + a
  timezone that due-date logic actually consults, not just stores — the
  user picked this over the smaller "store it but don't wire it in yet"
  option). `Household` (`libs/shared`) gains `timezone: str | None` (an
  IANA zone name; `None` behaves as UTC everywhere), and `name` is now
  editable via `PATCH /households/me` (previously signup-only) alongside
  the existing `city`. New `aria_shared.timezones` (`household_today()`,
  `to_household_date()`, built on stdlib `zoneinfo`) replaces every
  `date.today()`/UTC-midnight "today" in the due-date path: `GET
  /schedules/due-soon`'s overdue check, the calendar endpoint's occurrence
  projection (`created_at` is now converted to the household's local date
  instead of the old one-day floor-slack hack in `project_occurrences()`
  that existed specifically to compensate for not having a timezone),
  `_seed_baseline`'s default starting date, and the worker's
  `send_overdue_digest` cutoff (now computed per household instead of one
  global UTC-midnight cutoff — verified live: two households with the same
  `next_due_at` land on opposite sides of "overdue" once their timezones
  disagree about what day it is). The digest's *send time* itself stays one
  global Celery Beat crontab, not per-household local time, deliberately —
  that would need an hourly sweep plus a sent-today dedupe marker, a
  distinct architecture change out of scope here. `python:3.12-slim`
  doesn't ship an IANA tz database, so `tzdata` was added as a hard
  `libs/shared` dependency, not a Windows-only fallback. Frontend: new
  `HouseholdSettingsCard.tsx` (mirrors `HouseholdLocationCard.tsx`) with a
  required name field and a timezone `<select>` populated via
  `Intl.supportedValuesOf('timeZone')` (prefilled with the browser's own
  detected zone as a suggestion when unset); `DueSoonPage.tsx` and
  `CalendarView.tsx` now compute "today" via a new `todayInTimezone()`
  (`lib/dates.ts`) off the household's timezone instead of browser-local
  `new Date()` — verified live in the browser that the calendar's "today"
  cell visibly shifts a day when the household's zone crosses a UTC-relative
  boundary.

- ⬜ **Keyboard shortcuts / command palette.** A `Cmd+K`-style quick-open
  (jump to an entity, add a log, open chat) would pair naturally with the
  global search bar item above — likely the same underlying search index.

- ⬜ **Export/print a single entity's history.** Useful for anything
  warranty- or resale-relevant (a vehicle's full service history, a home
  appliance's manual + logs) — a "export as PDF" on `EntityDetailPage`
  bundling logs, schedules, and linked documents.

- ✅ **Mobile-friendlier layout.** Done. `Layout.tsx`'s single unbroken flex
  row (5 nav links + search + profile + logout, no breakpoints anywhere in
  the frontend previously) now collapses on narrow viewports (Tailwind v4's
  default `md:` / 768px breakpoint — no `tailwind.config.*` exists in this
  repo, so nothing extra to configure). Below `md`, nav links + profile +
  logout move behind a ☰/✕ toggle button (plain Unicode glyph, matching the
  ★/☆ pin-toggle convention rather than adding an icon library) that opens a
  dropdown panel — asked the user hamburger-dropdown vs. off-canvas drawer
  vs. bottom tab bar; dropdown won as the simplest fit for a 3-5-link nav
  with no new overlay/animation machinery. The search bar was kept out of
  that panel — asked the user whether it should collapse to an icon or stay
  always visible; it stays visible as its own full-width row directly below
  the brand/hamburger row (a second `SearchBar` instance, deliberately —
  the two are independent and only one is ever visible/interactive via
  `hidden`/`md:hidden`, simpler than relocating one instance with
  `flex-wrap`/`order` utilities), since search is a frequently-used feature
  that shouldn't need an extra tap through the menu. The dropdown panel
  closes on selecting a nav link (`onClick` alongside navigation) and on an
  outside click, mirroring `SearchBar.tsx`'s existing click-outside pattern.
  Desktop (`md`+) markup and behavior are unchanged. Verified live at both a
  desktop width and a 375px mobile width (via an injected same-origin
  iframe, since this environment's browser-automation window resize didn't
  actually change the page's viewport) — confirmed owner-only Health/Trash
  still gate correctly inside the mobile panel.

- ⬜ **Inline schedule editing from "What's Due."** Today, snoozing/rescheduling
  a due item requires navigating to the entity detail page. A quick
  "snooze 1 week" / "mark done" action directly on the `DueSoonPage` row
  would remove a click for the single most common interaction with this page.

---

## How to use this document

1. Items are independent — pick any one without needing to sequence against
   the others (unlike `roadmap.md`'s milestones, which build on each other).
2. Before starting an item with real design surface (the calendar view,
   global search, household settings), turn it into a proper sub-task plan —
   same bar as a roadmap milestone. Pure gating/plumbing items (health-tab
   role check, theme persistence) don't need one.
3. When an item ships, flip its status marker and add a one-line note here,
   same convention as `roadmap.md`.
4. New QOL ideas that surface in conversation go here, not just left in
   chat history — add a bullet under "Additional suggestions" or a new
   section if a theme of related items emerges.
