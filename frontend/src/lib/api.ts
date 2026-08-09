// API client.
//
// Two rules shape everything here:
//
//   1. The access token lives in memory. Not localStorage -- anything stored
//      there is readable by any script that ends up on the page, so one XSS
//      becomes a stolen session that outlives the tab. Losing it on reload is
//      the point; the refresh cookie is what restores the session.
//   2. The refresh token is an httpOnly cookie scoped to /api/auth, so it is
//      never readable from JavaScript and is not attached to ordinary API
//      calls. Every request therefore sends credentials, or the refresh call
//      would have nothing to send.

import type { ChatResponse, Document, TokenResponse, User } from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  // Declared and assigned explicitly rather than as a constructor parameter
  // property: those are TypeScript-only syntax, and `erasableSyntaxOnly` keeps
  // this codebase to syntax a plain type-stripping transform can handle.
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

let accessToken: string | null = null
let onSessionLost: (() => void) | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

/** Called when refresh fails, so the app can drop back to the login screen. */
export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler
}

// A single in-flight refresh, shared by every request that gets a 401.
//
// Without this, a page that fires four queries on mount sends four refresh
// requests the moment the token expires. Refresh tokens rotate and replaying a
// rotated one is treated as theft -- the backend revokes the entire family --
// so the stampede would log the user out. They all await this promise instead.
let refreshInFlight: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const resp = await fetch(`${BASE_URL}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!resp.ok) return false
      const body = (await resp.json()) as TokenResponse
      accessToken = body.access_token
      return true
    } catch {
      return false
    } finally {
      // Cleared in a microtask so everyone awaiting this round sees the same
      // result before a new attempt can start.
      queueMicrotask(() => {
        refreshInFlight = null
      })
    }
  })()

  return refreshInFlight
}

interface RequestOptions extends RequestInit {
  /** Internal: prevents a refreshed request from retrying forever. */
  _retried?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const resp = await rawRequest(path, options)

  if (resp.status === 204) return undefined as T
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export async function rawRequest(path: string, options: RequestOptions = {}): Promise<Response> {
  const { _retried, headers, ...rest } = options

  const merged = new Headers(headers)
  if (accessToken) merged.set('Authorization', `Bearer ${accessToken}`)

  const resp = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    headers: merged,
    credentials: 'include',
  })

  if (resp.status === 401 && !_retried && !path.startsWith('/api/auth/')) {
    // Expiry is indistinguishable from a bad token at the call site, so try
    // once. If refresh succeeds the original request is replayed; if not the
    // session is genuinely gone.
    const refreshed = await refreshAccessToken()
    if (refreshed) {
      return rawRequest(path, { ...options, _retried: true })
    }
    accessToken = null
    onSessionLost?.()
  }

  if (!resp.ok) {
    throw new ApiError(resp.status, await errorMessage(resp))
  }

  return resp
}

async function errorMessage(resp: Response): Promise<string> {
  try {
    const body = await resp.clone().json()
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    // FastAPI validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string }
      if (first.msg) return first.msg
    }
  } catch {
    // Fall through to the generic message.
  }
  return `Request failed (${resp.status})`
}

/**
 * Is the backend reachable at all?
 *
 * The frontend is hosted permanently on a CDN; the backend is brought up on
 * demand and torn down after. So "the API is simply not there right now" is a
 * normal state, not an error, and it has to be distinguished from a real
 * failure: a down backend makes `fetch` reject with a TypeError before any HTTP
 * status exists, which would otherwise surface to the user as a confusing
 * "something went wrong" on the login form.
 */
export async function isBackendUp(): Promise<boolean> {
  try {
    const resp = await fetch(`${BASE_URL}/api/health`, {
      method: 'GET',
      // A quick check, not a request worth waiting on. If the backend is down
      // the connection refuses fast; this cap is for the slow-DNS case.
      signal: AbortSignal.timeout(5000),
    })
    return resp.ok
  } catch {
    return false
  }
}

// --- auth ------------------------------------------------------------------

export const auth = {
  async register(email: string, password: string, fullName?: string): Promise<TokenResponse> {
    const body = await request<TokenResponse>('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, full_name: fullName || null }),
    })
    accessToken = body.access_token
    return body
  },

  async login(email: string, password: string): Promise<TokenResponse> {
    const body = await request<TokenResponse>('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    accessToken = body.access_token
    return body
  },

  async demo(): Promise<TokenResponse> {
    const body = await request<TokenResponse>('/api/auth/demo', { method: 'POST' })
    accessToken = body.access_token
    return body
  },

  async logout(): Promise<void> {
    try {
      await request<void>('/api/auth/logout', { method: 'POST' })
    } finally {
      // Drop the local token even if the server call failed -- the user asked
      // to be logged out, and leaving a live token in memory ignores that.
      accessToken = null
    }
  },

  me(): Promise<User> {
    return request<User>('/api/auth/me')
  },

  /** Restore a session on page load using the refresh cookie alone. */
  restore: refreshAccessToken,
}

// --- documents -------------------------------------------------------------

export const documents = {
  async list(): Promise<Document[]> {
    const body = await request<{ documents: Document[] }>('/api/documents')
    return body.documents
  },

  get(id: string): Promise<Document> {
    return request<Document>(`/api/documents/${id}`)
  },

  upload(file: File): Promise<Document> {
    const form = new FormData()
    form.append('file', file)
    // No Content-Type header: the browser has to set it, because only it knows
    // the multipart boundary. Setting it by hand produces a body the server
    // cannot parse.
    return request<Document>('/api/documents', { method: 'POST', body: form })
  },

  remove(id: string): Promise<void> {
    return request<void>(`/api/documents/${id}`, { method: 'DELETE' })
  },
}

// --- chat ------------------------------------------------------------------

export const chat = {
  /** Non-streaming. Used where a whole answer is simpler than a stream. */
  ask(question: string, documentId?: string | null): Promise<ChatResponse> {
    return request<ChatResponse>('/api/chat/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, document_id: documentId ?? null }),
    })
  },

  /** Streaming. Returns the raw response so the caller can read the body. */
  stream(question: string, documentId: string | null, signal: AbortSignal): Promise<Response> {
    return rawRequest('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, document_id: documentId ?? null }),
      signal,
    })
  },
}
