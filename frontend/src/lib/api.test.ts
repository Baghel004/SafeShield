import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, isBackendUp, request, setAccessToken, setSessionLostHandler } from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('request', () => {
  beforeEach(() => setAccessToken(null))

  afterEach(() => {
    vi.restoreAllMocks()
    setSessionLostHandler(null)
    setAccessToken(null)
  })

  it('attaches the bearer token when there is one', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    setAccessToken('token-abc')

    await request('/api/documents')

    const headers = (fetchMock.mock.calls[0]![1] as RequestInit).headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer token-abc')
  })

  it('always sends credentials, so the refresh cookie travels', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}))
    vi.stubGlobal('fetch', fetchMock)

    await request('/api/documents')

    expect((fetchMock.mock.calls[0]![1] as RequestInit).credentials).toBe('include')
  })

  it('refreshes once on a 401 and replays the request', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'expired' }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: 'fresh', expires_in: 900 }))
      .mockResolvedValueOnce(jsonResponse({ documents: [] }))
    vi.stubGlobal('fetch', fetchMock)
    setAccessToken('stale')

    const result = await request<{ documents: unknown[] }>('/api/documents')

    expect(result).toEqual({ documents: [] })
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      '/api/documents',
      '/api/auth/refresh',
      '/api/documents',
    ])
    // The replay must carry the new token, not the stale one.
    const replayHeaders = (fetchMock.mock.calls[2]![1] as RequestInit).headers as Headers
    expect(replayHeaders.get('Authorization')).toBe('Bearer fresh')
  })

  it('shares one refresh across concurrent 401s', async () => {
    // The property that matters. Refresh tokens rotate and replaying a rotated
    // one is treated as theft -- the backend revokes the whole family -- so a
    // stampede of refreshes would log the user out.
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/auth/refresh') {
        return Promise.resolve(jsonResponse({ access_token: 'fresh', expires_in: 900 }))
      }
      const headers = new Headers()
      return Promise.resolve(
        fetchMock.mock.calls.filter((c) => c[0] === url).length > 1 || headers.get('x') === null
          ? jsonResponse({ ok: true })
          : jsonResponse({}, 401),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    setAccessToken('stale')

    // Three requests that all 401 at once.
    const failing = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockImplementation((url: string) =>
        url === '/api/auth/refresh'
          ? Promise.resolve(jsonResponse({ access_token: 'fresh', expires_in: 900 }))
          : Promise.resolve(jsonResponse({ ok: true })),
      )
    vi.stubGlobal('fetch', failing)

    await Promise.all([request('/api/a'), request('/api/b'), request('/api/c')])

    const refreshCalls = failing.mock.calls.filter((c) => c[0] === '/api/auth/refresh')
    expect(refreshCalls).toHaveLength(1)
  })

  it('does not try to refresh a failing auth call', async () => {
    // Otherwise a wrong password triggers a refresh attempt on every login.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'bad creds' }, 401))
    vi.stubGlobal('fetch', fetchMock)

    await expect(request('/api/auth/login', { method: 'POST' })).rejects.toThrow(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reports the session lost when refresh fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: 'no' }, 401))
    vi.stubGlobal('fetch', fetchMock)
    const lost = vi.fn()
    setSessionLostHandler(lost)
    setAccessToken('stale')

    await expect(request('/api/documents')).rejects.toThrow(ApiError)
    expect(lost).toHaveBeenCalledOnce()
  })

  it('surfaces the server detail as the error message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'File is not a PDF' }, 422)))

    await expect(request('/api/documents', { method: 'POST' })).rejects.toThrow('File is not a PDF')
  })

  it('unwraps a FastAPI validation error list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: [{ msg: 'String too short' }] }, 422)),
    )

    await expect(request('/api/chat/sync', { method: 'POST' })).rejects.toThrow('String too short')
  })

  it('returns undefined for 204 rather than failing to parse', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))

    await expect(request('/api/documents/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})

describe('isBackendUp', () => {
  afterEach(() => vi.restoreAllMocks())

  it('is true when health responds ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 200 })))
    await expect(isBackendUp()).resolves.toBe(true)
  })

  it('is false when the backend is unreachable', async () => {
    // The on-demand case: the cluster is torn down, so fetch rejects with a
    // TypeError before any HTTP status exists. This must read as "down", not
    // throw, or the whole app crashes when the backend is simply absent.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(isBackendUp()).resolves.toBe(false)
  })

  it('is false on a 5xx', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })))
    await expect(isBackendUp()).resolves.toBe(false)
  })
})
