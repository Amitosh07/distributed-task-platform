import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearToken, request, setToken } from './api'

describe('API client', () => {
  afterEach(() => { clearToken(); vi.unstubAllGlobals() })
  it('attaches the persisted bearer token', async () => {
    setToken('test-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await request<{ ok: boolean }>('/v1/projects')
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/v1/projects', expect.objectContaining({ headers: expect.any(Headers) }))
    const headers = fetchMock.mock.calls[0][1].headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer test-token')
  })
})
