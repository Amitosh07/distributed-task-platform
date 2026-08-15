const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const TOKEN_KEY = 'dtp.access_token'
export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }
export const getToken = () => sessionStorage.getItem(TOKEN_KEY)
export const setToken = (token: string) => sessionStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY)
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers); headers.set('Accept', 'application/json')
  if (options.body) headers.set('Content-Type', 'application/json')
  const token = getToken(); if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try { response = await fetch(`${BASE_URL}${path}`, { ...options, headers }) } catch { throw new ApiError(0, 'Network error. Is the API running?') }
  if (response.status === 401) { clearToken(); window.dispatchEvent(new Event('auth-expired')) }
  if (!response.ok) { const body = await response.json().catch(() => null) as { error?: { message?: string } } | null; throw new ApiError(response.status, body?.error?.message || `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}
