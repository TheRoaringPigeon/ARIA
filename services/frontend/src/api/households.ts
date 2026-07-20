import { apiDelete, apiGet, apiPost } from './client'
import type { Invite, Member } from './types'

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
