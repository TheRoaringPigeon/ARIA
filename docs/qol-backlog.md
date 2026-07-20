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

- ⬜ **Theme should follow the user, not the browser.** Today `ThemeContext.tsx`
  reads/writes `localStorage['aria-theme']` only — it isn't tied to a session,
  user, or household at all. It *looks* household-scoped because a shared
  household device keeps whichever theme was last clicked, but really it's
  per-browser: a second member logging in on their own laptop gets the
  default (`slate`), and switching users on the same browser doesn't switch
  the theme either. Fix: persist `theme` on the `User` record (`core-api`),
  add a `PATCH /users/me` or fold it into the existing profile update path,
  and have `ThemeProvider` hydrate from `session.theme` after login instead
  of (or in addition to, as an offline-first fallback) `localStorage`.

- ⬜ **Global search bar.** No free-text search exists anywhere in the
  frontend today — `EntityListPage.tsx` only filters by domain
  (`DOMAIN_FILTERS`) and an archived checkbox. Add a search input (name,
  tags, location, spec values) that works at least across entities, and
  ideally documents and logs too. A single header-level search (in `Layout.tsx`)
  that fans out across domains would read better than a per-page search box.

- ⬜ **Filters for entities and "what's due".** Entities already have a domain
  filter; "what's due" (`DueSoonPage.tsx`) only has the `withinDays` window
  selector. Add: entity-domain filter on `DueSoonPage`, a status/tag filter on
  `EntityListPage`, and an overdue-only toggle on `DueSoonPage`.

- ⬜ **Health tab should be owner-only.** `HealthPage.tsx` and its nav link in
  `Layout.tsx` are unconditionally visible to every session today — no role
  check anywhere in either file. `session.role` already exists (surfaced on
  `ProfilePage.tsx`) from M9's account work, so this is purely a frontend
  gating change: hide the nav link and route for non-owners. Low effort,
  self-contained.

- ⬜ **Calendar view for "what's due," click a day to add.** `DueSoonPage.tsx`
  is a flat list today, sorted implicitly by whatever `useDueSoon` returns.
  Add a month-grid calendar view (toggle alongside the existing list view,
  not a replacement — the list is better for "what's overdue right now").
  Clicking a day opens a small composer that pre-fills the due date; clicking
  an existing due item's day shows what's already scheduled that day.

- ⬜ **Calendar-added items create the real entity schedule/log.** Direct
  extension of the item above: the day-composer shouldn't create a
  calendar-only event — it should call the same `POST /schedules` (or
  `POST /logs` for a one-off/completed item) the entity detail page's
  existing forms use, just pre-filled with the clicked date and requiring an
  entity to be picked (or created inline via the existing `EntityForm`).
  This keeps "the calendar" as a view over real schedule data, not a second
  source of truth — worth calling out explicitly since it's the one item
  here with real design risk (entity picker UX, recurrence fields, what
  happens if no entity exists yet).

## Additional suggestions

- ⬜ **Dark mode / system theme preference.** All 7 `THEMES` in
  `ThemeContext.tsx` are light-background palettes (one, `night`, is dark but
  it's just another manual pick, not tied to OS `prefers-color-scheme`).
  Worth deciding whether "night" becomes an actual auto dark-mode or stays a
  manual option alongside a real light/dark toggle.

- ⬜ **Bulk actions on the entity list.** Archive/delete are per-entity today
  (via the detail page). Multi-select + bulk archive on `EntityListPage`
  would help households with many stale entities (the M9 dogfooding note
  about 5 leftover "Smoke Test" fixtures needing manual cleanup is a real
  example of this gap).

- ⬜ **Recent/pinned entities.** `EntityListPage` has no ordering control
  beyond domain/archived filters — no "recently viewed" or manual pin, which
  matters once a household has enough entities that scrolling/filtering to
  the same 3-4 frequently-referenced ones (the daily-driver car, the
  furnace) gets old.

- ⬜ **Notifications for overdue items.** Nothing currently pushes "this is
  overdue" anywhere — `DueSoonPage` is pull-only (you have to open the app).
  Even a simple email digest (leveraging the same infra as invite emails
  from M9) or an in-app badge count on the "What's Due" nav link would close
  this.

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
