import { apiGet, apiPost } from './client'
import type { SessionInfo } from './types'

export function login(password: string): Promise<SessionInfo> {
  return apiPost<SessionInfo>('/auth/login', { password })
}

export function logout(): Promise<{ status: string }> {
  return apiPost('/auth/logout')
}

export function getSession(): Promise<SessionInfo> {
  return apiGet<SessionInfo>('/auth/me')
}
