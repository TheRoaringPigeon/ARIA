import { apiGet, apiPatch } from './client'
import type { CurrentUser } from './types'

export function getCurrentUser(): Promise<CurrentUser> {
  return apiGet<CurrentUser>('/users/me')
}

export function updateCurrentUser(
  patch: Partial<{ theme: string | null; pinned_entity_ids: string[]; notify_overdue_email: boolean }>,
): Promise<CurrentUser> {
  return apiPatch<CurrentUser>('/users/me', patch)
}
