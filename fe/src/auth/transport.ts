let identityVersion = 0
let identityAbort = new AbortController()
const cleanupCallbacks = new Set<() => void>()
export const authEvent = 'jingpo:auth-invalid'
export const authSyncEvent = 'jingpo:auth-sync'
export const authSyncKey = 'jingpo.auth-change'

export function currentIdentityVersion() { return identityVersion }
export function onIdentityReset(callback: () => void) { cleanupCallbacks.add(callback) }

export function clearIdentityRequests() {
  identityVersion += 1
  identityAbort.abort()
  identityAbort = new AbortController()
  for (const callback of cleanupCallbacks) callback()
}

export function notifyIdentityChange(code: string) {
  clearIdentityRequests()
  window.dispatchEvent(new CustomEvent(authEvent, { detail: code }))
}

export function syncIdentityTabs() {
  try { localStorage.setItem(authSyncKey, crypto.randomUUID()) } catch { return }
}

export function csrfHeaders(): Record<string, string> {
  const cookie = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('jingpo_csrf='))
  return cookie ? { 'X-CSRF-Token': decodeURIComponent(cookie.slice('jingpo_csrf='.length)) } : {}
}

export async function identityFetch(input: string, init: RequestInit = {}, identityBound = true): Promise<Response> {
  const version = identityVersion
  const signals = [init.signal, identityBound ? identityAbort.signal : undefined].filter((signal): signal is AbortSignal => Boolean(signal))
  const signal = signals.length ? AbortSignal.any(signals) : undefined
  const response = await fetch(input, { credentials: 'include', cache: 'no-store', ...init, signal })
  if (identityBound && version !== identityVersion) throw new DOMException('账号状态已变化', 'AbortError')
  return response
}

export class AuthRequestError extends Error {
  constructor(message: string, public status: number, public code: string, public requestId?: string) { super(message) }
}

export async function authRequest<T>(path: string, init: RequestInit = {}, identityBound = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  if (init.method && !['GET', 'HEAD'].includes(init.method)) Object.entries(csrfHeaders()).forEach(([key, value]) => headers.set(key, value))
  let response: Response
  try {
    response = await identityFetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1${path}`, { ...init, headers, signal: init.signal ?? AbortSignal.timeout(15_000) }, identityBound)
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
    throw new Error('账号服务连接失败或请求超时，请重试。', { cause: reason })
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const code = body?.error?.code ?? `HTTP_${response.status}`
    if (identityBound && (response.status === 401 || ['AUTH_CHANGED', 'USERNAME_REQUIRED', 'PROFILE_INCOMPLETE', 'EMAIL_NOT_VERIFIED', 'CSRF_REJECTED'].includes(code))) notifyIdentityChange(code)
    throw new AuthRequestError(body?.error?.message ?? '账号操作未完成，请稍后重试。', response.status, code, body?.request_id)
  }
  return body as T
}
