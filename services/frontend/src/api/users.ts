import { apiGet, apiPatch } from './client'
import type { CurrentUser } from './types'

export function getCurrentUser(): Promise<CurrentUser> {
  return apiGet<CurrentUser>('/users/me')
}

export function updateCurrentUser(patch: { theme: string | null }): Promise<CurrentUser> {
  return apiPatch<CurrentUser>('/users/me', patch)
}
