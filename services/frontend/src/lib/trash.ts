// Mirrors services/worker/app/config.py's entity_trash_grace_hours default —
// no shared settings module between the two services to read it from live,
// so keep both in sync by hand if that default changes. Centralized here so
// at least the frontend's own two references (RecentlyDeletedPage's badge,
// EntityDetailPage's delete confirm copy) can't drift from each other.
export const ENTITY_TRASH_GRACE_HOURS = 24 * 3
export const ENTITY_TRASH_GRACE_DAYS = ENTITY_TRASH_GRACE_HOURS / 24
