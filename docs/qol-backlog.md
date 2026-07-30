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

- ⬜ **Undo for delete.** M9 landed real hard-delete (owner-only) distinct
  from archive. Archive is already a soft, reversible state, but hard-delete
  has no undo window — worth a confirm-dialog-with-countdown or a short
  grace period, especially since it's a destructive, no-recovery action.

- ⬜ **Household-level default settings.** Now that theme is moving to
  per-user (see above), the household itself has no settings surface at all
  beyond membership (`HouseholdMembersCard`). A household name/timezone
  (schedules currently compare against local browser time in
  `DueSoonPage.tsx`'s `daysUntil()` — no household timezone concept exists)
  would matter for multi-timezone households.

- ⬜ **Keyboard shortcuts / command palette.** A `Cmd+K`-style quick-open
  (jump to an entity, add a log, open chat) would pair naturally with the
  global search bar item above — likely the same underlying search index.

- ⬜ **Export/print a single entity's history.** Useful for anything
  warranty- or resale-relevant (a vehicle's full service history, a home
  appliance's manual + logs) — a "export as PDF" on `EntityDetailPage`
  bundling logs, schedules, and linked documents.

- ⬜ **Mobile-friendlier layout.** `Layout.tsx`'s header is a single flex row
  of nav links with no responsive collapse — worth a pass once there are
  more nav items (search, calendar) competing for header space.

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
