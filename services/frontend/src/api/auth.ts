import { apiGet, apiPost } from './client'
import type { SessionInfo } from './types'

export function login(email: string, password: string): Promise<SessionInfo> {
  return apiPost<SessionInfo>('/auth/login', { email, password })
}

export function signup(
  householdName: string,
  name: string,
  email: string,
  password: string,
  city?: string,
): Promise<SessionInfo> {
  return apiPost<SessionInfo>('/auth/signup', {
    household_name: householdName,
    city: city || null,
    name,
    email,
    password,
  })
}

export function acceptInvite(
  token: string,
  name: string,
  email: string,
  password: string,
): Promise<SessionInfo> {
  return apiPost<SessionInfo>('/auth/accept-invite', { token, name, email, password })
}

export function logout(): Promise<{ status: string }> {
  return apiPost('/auth/logout')
}

export function getSession(): Promise<SessionInfo> {
  return apiGet<SessionInfo>('/auth/me')
}
