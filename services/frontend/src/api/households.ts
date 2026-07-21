import { apiDelete, apiGet, apiPatch, apiPost } from './client'
import type { Household, Invite, Member } from './types'

export function getHousehold(): Promise<Household> {
  return apiGet<Household>('/households/me')
}

export function updateHousehold(patch: { city: string | null }): Promise<Household> {
  return apiPatch<Household>('/households/me', patch)
}

export function listMembers(): Promise<Member[]> {
  return apiGet<Member[]>('/households/members')
}

export function listInvites(): Promise<Invite[]> {
  return apiGet<Invite[]>('/households/invites')
}

export function createInvite(): Promise<Invite> {
  return apiPost<Invite>('/households/invites')
}

export function revokeInvite(token: string): Promise<void> {
  return apiDelete(`/households/invites/${token}`)
}
