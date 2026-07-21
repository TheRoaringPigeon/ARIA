export const CORE_API_URL = import.meta.env.VITE_CORE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Thrown when fetch() itself never got a response (offline, DNS failure,
// connection refused, etc. — fetch()'s only rejection shape is a TypeError).
// Distinct from ApiError, which means a response *was* received but wasn't
// ok — that distinction is what tells a caller "queue this for later" apart
// from "the server actually rejected it."
export class NetworkError extends Error {}

export async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
  } catch {
    // No JSON body to extract a detail message from — fall back to statusText.
    return res.statusText
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${CORE_API_URL}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
  } catch (err) {
    throw err instanceof TypeError ? new NetworkError(err.message) : err
  }

  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorDetail(res))
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path)
}

export function apiPost<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
    headers: extraHeaders,
  })
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE' })
}
